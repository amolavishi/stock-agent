from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .security import redact_secrets


class HermesError(RuntimeError):
    pass


class HermesCancelledError(HermesError):
    pass


@dataclass
class HermesResponse:
    data: dict[str, Any]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    api_calls: int = 1
    estimated_cost_usd: float = 0.0
    session_id: str = ""
    completed: bool = True
    failed: bool = False
    usage_raw: dict[str, Any] | None = None


class HermesAdapter(Protocol):
    def invoke_json(self, prompt: str, role: str) -> HermesResponse: ...


def extract_json(text: str) -> dict[str, Any]:
    values = extract_json_objects(text)
    if values:
        return values[0]
    raise HermesError("Hermes response did not contain a valid JSON object")


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    """Return every decodable JSON object, including objects nested in CLI envelopes."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    candidates.append(text.strip())
    decoder = json.JSONDecoder()
    values: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if not isinstance(value, dict):
            return
        fingerprint = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint in seen:
            return
        seen.add(fingerprint)
        values.append(value)
        for nested in value.values():
            if isinstance(nested, dict):
                add(nested)
            elif isinstance(nested, str) and "{" in nested:
                for item in extract_json_objects(nested):
                    add(item)

    for candidate in candidates:
        try:
            add(json.loads(candidate))
        except json.JSONDecodeError:
            for index, char in enumerate(candidate):
                if char == "{":
                    try:
                        value, _ = decoder.raw_decode(candidate[index:])
                        add(value)
                    except json.JSONDecodeError:
                        pass
    return values


ROLE_REQUIRED_KEYS = {
    "research": {"suggested_decision", "confidence", "bull_case", "bear_case", "evidence_ids"},
    "critic": {"verdict", "critical_flaws", "failure_scenarios", "evidence_conflicts",
               "critic_decision", "confidence"},
    "chairman": {"decision", "confidence"},
    "command_parser": {"intent", "tickers", "confidence", "missing_fields"},
}


def extract_role_json(text: str, role: str) -> dict[str, Any]:
    values = extract_json_objects(text)
    if not values:
        raise HermesError("Hermes response did not contain a valid JSON object")
    required = ROLE_REQUIRED_KEYS.get(role, set())
    if not required:
        return values[0]
    ranked = sorted(values, key=lambda value: len(required.intersection(value)), reverse=True)
    if required.issubset(ranked[0]):
        return ranked[0]
    # Let the typed role agent issue its bounded one-shot repair request.  The
    # candidate is still validated before construction, and a failed repair
    # remains fail-closed; rejecting here would bypass the existing repair path.
    return ranked[0]


def parse_usage_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize Hermes 0.20 one-shot usage JSON without inventing missing values."""
    def integer(name: str) -> int:
        try:
            return max(0, int(payload.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    def number(name: str) -> float:
        try:
            return max(0.0, float(payload.get(name) or 0))
        except (TypeError, ValueError):
            return 0.0

    input_tokens = integer("input_tokens")
    output_tokens = integer("output_tokens")
    cache_read = integer("cache_read_tokens")
    cache_write = integer("cache_write_tokens")
    reasoning = integer("reasoning_tokens")
    return {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning,
        "total_tokens": integer("total_tokens") or
                        input_tokens + output_tokens + cache_read + cache_write + reasoning,
        "api_calls": integer("api_calls"),
        "estimated_cost_usd": number("estimated_cost_usd"),
        "model": str(payload.get("model") or ""), "provider": str(payload.get("provider") or ""),
        "session_id": str(payload.get("session_id") or ""),
        "completed": bool(payload.get("completed")), "failed": bool(payload.get("failed")),
        "failure": str(payload.get("failure") or ""), "raw": payload,
    }


class _TelemetryMixin:
    usage_recorder: Any
    call_context: dict[str, Any]

    def set_call_context(self, **values: Any) -> None:
        self.call_context = dict(values)

    def _record_call(self, response: HermesResponse, role: str, started_at: str,
                     finished_at: str, prompt: str, response_text: str,
                     error_type: str = "") -> None:
        if not self.usage_recorder:
            return
        context = dict(getattr(self, "call_context", {}))
        record = {
            "api_call_id": context.get("api_call_id") or str(uuid.uuid4()),
            "run_id": context.get("run_id", ""), "request_id": context.get("request_id", ""),
            "ticker": context.get("ticker", ""), "role": role,
            "round_no": int(context.get("round_no", 0)), "phase": context.get("phase", role.upper()),
            "provider": response.provider, "model": response.model,
            "reasoning_effort": context.get("reasoning_effort", ""),
            "started_at": started_at, "finished_at": finished_at, "latency_ms": response.latency_ms,
            "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
            "reasoning_tokens": response.reasoning_tokens,
            "cache_read_tokens": response.cache_read_tokens,
            "cache_write_tokens": response.cache_write_tokens,
            "total_tokens": response.total_tokens, "api_calls": response.api_calls,
            "estimated_cost_usd": response.estimated_cost_usd,
            "repair_attempt": int(bool(context.get("repair_attempt", False))),
            "completed": int(response.completed), "failed": int(response.failed),
            "error_type": error_type, "prompt_chars": len(prompt),
            "response_chars": len(response_text),
        }
        self.usage_recorder(record)


class HermesHTTPAdapter(_TelemetryMixin):
    """Configurable local JSON endpoint; no automatic transport fallback."""

    def __init__(self, endpoint: str, model: str, timeout: float = 120,
                 usage_recorder=None):
        self.endpoint, self.model, self.timeout = endpoint, model, timeout
        self.usage_recorder, self.call_context = usage_recorder, {}

    def invoke_json(self, prompt: str, role: str) -> HermesResponse:
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        body = json.dumps({"prompt": prompt, "role": role, "model": self.model,
                           "safe_mode": True, "tools": []}).encode()
        request = urllib.request.Request(self.endpoint, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise HermesError(redact_secrets(f"Hermes HTTP call failed: {exc}")) from exc
        content = payload.get("content") or payload.get("result") or payload.get("response")
        data = content if isinstance(content, dict) else extract_json(str(content))
        usage = payload.get("usage", {})
        result = HermesResponse(data, "deepseek", self.model,
            int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)),
            int(usage.get("prompt_cache_hit_tokens", 0)),
            int(payload.get("latency_ms", 0) or round((time.perf_counter() - started) * 1000)),
            reasoning_tokens=int(usage.get("reasoning_tokens", 0)),
            cache_read_tokens=int(usage.get("prompt_cache_hit_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            estimated_cost_usd=float(usage.get("estimated_cost_usd", 0) or 0))
        self._record_call(result, role, started_at, datetime.now(timezone.utc).isoformat(),
                          prompt, str(content))
        return result


class HermesCLIAdapter(_TelemetryMixin):
    def __init__(self, executable: str, model: str, provider: str = "deepseek", timeout: float = 180,
                 usage_recorder=None):
        path = Path(executable)
        if not path.is_file():
            raise HermesError(f"Hermes executable not found: {path}")
        self.executable, self.model, self.provider, self.timeout = str(path), model, provider, timeout
        self.usage_recorder, self.call_context = usage_recorder, {}

    def invoke_json(self, prompt: str, role: str) -> HermesResponse:
        guarded = ("Return exactly one JSON object. Do not call tools, shell, files, network, or memory. "
                   f"Role={role}.\n\n{prompt}")
        env = os.environ.copy()
        reasoning = str(self.call_context.get("reasoning_effort", "") or "high")
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="stock_agent_usage_") as tmp:
            usage_path = Path(tmp) / "usage.json"
            hermes_python = Path(self.executable).with_name("python.exe")
            use_stdin_bridge = os.name == "nt" and hermes_python.is_file()
            if use_stdin_bridge:
                command = [str(hermes_python), "-m", "stock_agent.hermes_stdin_runner",
                           "--provider", self.provider, "--model", self.model,
                           "--reasoning", reasoning, "--usage-file", str(usage_path)]
            else:
                command = [self.executable, "--provider", self.provider, "-m", self.model,
                           "--reasoning", reasoning, "--safe-mode", "--usage-file", str(usage_path),
                           "-z", guarded]
            stdout = ""
            error_type = ""
            returncode = -1
            failure: Exception | None = None
            try:
                cancellation_check = self.call_context.get("cancellation_check")
                if cancellation_check:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE if use_stdin_bridge else None,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8", errors="replace", env=env,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if use_stdin_bridge and process.stdin:
                        process.stdin.write(guarded)
                        process.stdin.close()
                        process.stdin = None
                    deadline = time.monotonic() + self.timeout
                    while True:
                        try:
                            stdout, stderr = process.communicate(timeout=0.25)
                            break
                        except subprocess.TimeoutExpired:
                            if cancellation_check():
                                if os.name == "nt":
                                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                                else:
                                    process.kill()
                                process.communicate()
                                error_type = "CANCELLED"
                                failure = HermesCancelledError("Hermes call cancelled")
                                stdout, stderr = "", ""
                                break
                            if time.monotonic() >= deadline:
                                process.kill()
                                process.communicate()
                                raise subprocess.TimeoutExpired(command, self.timeout)
                    returncode = process.returncode if process.returncode is not None else -1
                    if not failure and returncode:
                        error_type = f"CLI_EXIT_{returncode}"
                        failure = HermesError(redact_secrets(
                            f"Hermes CLI exit {returncode}: {stderr[-800:]}"))
                else:
                    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                            errors="replace", timeout=self.timeout, env=env, check=False,
                                            input=guarded if use_stdin_bridge else None)
                    stdout, returncode = result.stdout, result.returncode
                    if result.returncode:
                        error_type = f"CLI_EXIT_{result.returncode}"
                        failure = HermesError(redact_secrets(
                            f"Hermes CLI exit {result.returncode}: {result.stderr[-800:]}"))
            except subprocess.TimeoutExpired as exc:
                phase = str(self.call_context.get("phase", "UNKNOWN"))
                error_type = "TIMEOUT"
                failure = HermesError(
                    f"Hermes timeout after {self.timeout:.0f}s (role={role}, phase={phase}). "
                    "The run stopped without an automatic paid retry.")
            except OSError as exc:
                error_type, failure = "OS_ERROR", HermesError(redact_secrets(f"Hermes CLI failed: {exc}"))
            usage_payload: dict[str, Any] = {}
            if usage_path.is_file():
                try:
                    usage_payload = json.loads(usage_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    error_type = error_type or "USAGE_FILE_INVALID"
            usage = parse_usage_report(usage_payload)
            if not failure and usage["failed"]:
                detail = stdout.strip()
                failure = HermesError(redact_secrets(
                    f"Hermes provider failed: {detail[-800:]}"
                    if detail else "Hermes provider failed without a diagnostic"))
                error_type = error_type or "PROVIDER_FAILURE"
            latency = round((time.perf_counter() - started) * 1000)
            response = HermesResponse(
                {}, usage["provider"] or self.provider, usage["model"] or self.model,
                usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"], latency,
                usage["reasoning_tokens"], usage["cache_read_tokens"], usage["cache_write_tokens"],
                usage["total_tokens"], usage["api_calls"], usage["estimated_cost_usd"],
                usage["session_id"], bool(usage["completed"] and not failure),
                bool(usage["failed"] or failure), usage["raw"])
            if not failure:
                try:
                    response.data = extract_role_json(stdout, role)
                except Exception as exc:
                    error_type, failure = "INVALID_JSON", exc
                    response.failed, response.completed = True, False
            self._record_call(response, role, started_at, datetime.now(timezone.utc).isoformat(),
                              prompt, stdout, error_type)
            if failure:
                raise failure
            return response


def default_hermes_executable() -> str:
    configured = os.getenv("HERMES_EXECUTABLE", "")
    if configured:
        return configured
    discovered = shutil.which("hermes") or shutil.which("hermes.exe")
    if discovered:
        return discovered
    known = Path(os.getenv("LOCALAPPDATA", "")) / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe"
    if known.is_file():
        return str(known)
    raise HermesError("Hermes executable was not found. Set HERMES_EXECUTABLE or add hermes to PATH.")
