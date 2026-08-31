"""Independent PRE-A report sidecar.

This module is intentionally outside the authoritative PRIMARY Shadow path.
It reads a completed human-facing DAILY_REPORT.md as untrusted data and writes
one separate PRE_A_REPORT.md.  It never mutates SQLite, Shadow artifacts,
Research Grade, Execution Action, Position Size, or broker state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .config import load_environment, require_secret
from .providers import CodexExecProvider, OpenAIResponsesProvider


PRE_A_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["analysis_status", "candidates", "global_notes"],
    "properties": {
        "analysis_status": {
            "type": "string",
            "enum": ["COMPLETE", "INSUFFICIENT_SOURCE_REPORT"],
        },
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "ticker",
                    "source_grade",
                    "promotion_readiness",
                    "a_trajectory",
                    "fundamental_direction",
                    "expectation_gap",
                    "price_lag",
                    "catalyst_window",
                    "missing_gates",
                    "promotion_triggers",
                    "demotion_triggers",
                    "expiry_or_recheck",
                    "why",
                    "source_limitations",
                ],
                "properties": {
                    "ticker": {"type": "string", "minLength": 1, "maxLength": 16},
                    "source_grade": {
                        "type": "string",
                        "enum": ["A", "A-", "B+", "B", "EXCLUDE", "NOT_EVALUATED", "UNKNOWN"],
                    },
                    "promotion_readiness": {
                        "type": "string",
                        "enum": ["PRE_A_HIGH", "PRE_A", "WATCH_TRAJECTORY", "NONE", "NOT_EVALUATED"],
                    },
                    "a_trajectory": {
                        "type": "string",
                        "enum": ["VERY_HIGH", "HIGH", "MEDIUM", "LOW", "NONE", "NOT_EVALUATED"],
                    },
                    "fundamental_direction": {
                        "type": "string",
                        "enum": ["STRONG_VERIFIED", "VERIFIED", "PARTIAL_VERIFIED", "WEAK", "NEGATIVE", "UNKNOWN"],
                    },
                    "expectation_gap": {
                        "type": "string",
                        "enum": ["VERIFIED", "PARTIAL_VERIFIED", "FORMING", "ABSENT", "UNKNOWN"],
                    },
                    "price_lag": {
                        "type": "string",
                        "enum": ["PRESENT", "PARTIAL_VERIFIED", "ABSENT", "UNKNOWN"],
                    },
                    "catalyst_window": {
                        "type": "string",
                        "enum": ["IMMEDIATE", "NEAR", "IN_WINDOW", "OUTSIDE", "UNKNOWN"],
                    },
                    "missing_gates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["gate", "severity", "reason"],
                            "properties": {
                                "gate": {"type": "string", "minLength": 1, "maxLength": 160},
                                "severity": {"type": "string", "enum": ["MINOR", "MODERATE", "MAJOR", "CRITICAL"]},
                                "reason": {"type": "string", "minLength": 1, "maxLength": 600},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "promotion_triggers": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "demotion_triggers": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                    "expiry_or_recheck": {"type": "string", "minLength": 1, "maxLength": 300},
                    "why": {"type": "string", "minLength": 1, "maxLength": 1400},
                    "source_limitations": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
                "additionalProperties": False,
            },
        },
        "global_notes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 800},
        },
    },
    "additionalProperties": False,
}


class PreASidecarError(RuntimeError):
    """Fail-closed PRE-A sidecar contract error."""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ticker_occurrences(report_text: str, ticker: str) -> list[re.Match[str]]:
    ticker_upper = ticker.strip().upper()
    if not ticker_upper:
        return []
    haystack = report_text.upper()
    patterns = [
        re.compile(rf"`{re.escape(ticker_upper)}`"),
        re.compile(rf"\|\s*{re.escape(ticker_upper)}\s*\|"),
    ]
    if len(ticker_upper) >= 2:
        patterns.append(re.compile(rf"(?<![A-Z0-9]){re.escape(ticker_upper)}(?![A-Z0-9])"))
    matches: list[re.Match[str]] = []
    for pattern in patterns:
        matches.extend(pattern.finditer(haystack))
    return sorted(matches, key=lambda item: item.start())


def _source_supports_b_plus(report_text: str, ticker: str) -> bool:
    haystack = report_text.upper()
    for match in _ticker_occurrences(report_text, ticker):
        start = max(0, match.start() - 320)
        end = min(len(haystack), match.end() + 320)
        if "B+" in haystack[start:end]:
            return True
    return False


def validate_sidecar_payload(payload: dict[str, Any], report_text: str) -> None:
    """Apply Python-owned fail-closed invariants after model schema validation."""
    if not isinstance(payload, dict):
        raise PreASidecarError("PRE-A provider output must be one object")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise PreASidecarError("PRE-A candidates must be an array")
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise PreASidecarError("PRE-A candidate must be an object")
        ticker = str(candidate.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen:
            raise PreASidecarError("PRE-A ticker is missing or duplicated")
        seen.add(ticker)
        if not _ticker_occurrences(report_text, ticker):
            raise PreASidecarError(f"PRE-A hallucinated ticker not present in source report: {ticker}")
        readiness = str(candidate.get("promotion_readiness") or "")
        source_grade = str(candidate.get("source_grade") or "")
        if readiness in {"PRE_A", "PRE_A_HIGH"}:
            if source_grade != "B+":
                raise PreASidecarError(f"PRE-A readiness requires B+ source grade: {ticker}")
            if not _source_supports_b_plus(report_text, ticker):
                raise PreASidecarError(f"source report does not locally support B+ for PRE-A candidate: {ticker}")
        if readiness == "PRE_A_HIGH":
            gates = candidate.get("missing_gates") or []
            critical = sum(1 for gate in gates if isinstance(gate, dict) and gate.get("severity") == "CRITICAL")
            major = sum(1 for gate in gates if isinstance(gate, dict) and gate.get("severity") == "MAJOR")
            if critical:
                raise PreASidecarError(f"PRE_A_HIGH cannot contain CRITICAL unresolved gates: {ticker}")
            if major > 1 or len(gates) > 2:
                raise PreASidecarError(f"PRE_A_HIGH missing-gate cap exceeded: {ticker}")


def _render_markdown(
    *,
    source_report: Path,
    source_hash: str,
    payload: dict[str, Any],
    provider_name: str,
    model_name: str,
    telemetry: dict[str, Any],
) -> str:
    lines = [
        "# PRE-A Sidecar Report",
        "",
        "> NON-AUTHORITATIVE SIDECAR. This report cannot change PRIMARY grade, action, position size, or broker state.",
        "",
        f"- source report: `{source_report.as_posix()}`",
        f"- source SHA-256: `{source_hash}`",
        f"- analysis status: `{payload.get('analysis_status', 'UNKNOWN')}`",
        f"- provider/model: `{provider_name}/{model_name}`",
        f"- broker_write_count: `0`",
        f"- primary_mutation: `NO`",
        f"- web/sec/news re-search: `NO`",
        "",
        "## Summary",
        "",
    ]
    candidates = payload.get("candidates") or []
    if not candidates:
        lines.append("`NO_PRE_A_CANDIDATE_FROM_SOURCE_REPORT`")
    else:
        lines.extend([
            "| Ticker | Source Grade | Promotion Readiness | A-Trajectory | Fundamental | Expectation Gap | Price Lag | Catalyst |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for item in candidates:
            lines.append(
                "| {ticker} | {source_grade} | {promotion_readiness} | {a_trajectory} | {fundamental_direction} | {expectation_gap} | {price_lag} | {catalyst_window} |".format(**item)
            )
    for item in candidates:
        lines.extend([
            "",
            f"## {item['ticker']}",
            "",
            f"- Source Grade: `{item['source_grade']}`",
            f"- Promotion Readiness: `{item['promotion_readiness']}`",
            f"- A-Trajectory: `{item['a_trajectory']}`",
            f"- Fundamental Direction: `{item['fundamental_direction']}`",
            f"- Expectation Gap: `{item['expectation_gap']}`",
            f"- Price Lag: `{item['price_lag']}`",
            f"- Catalyst Window: `{item['catalyst_window']}`",
            f"- Recheck / Expiry: {item['expiry_or_recheck']}",
            "",
            f"**Why:** {item['why']}",
            "",
            "### Missing Gates",
        ])
        if item["missing_gates"]:
            for gate in item["missing_gates"]:
                lines.append(f"- `{gate['severity']}` — **{gate['gate']}**: {gate['reason']}")
        else:
            lines.append("- NONE stated by source-derived sidecar analysis")
        lines.append("")
        lines.append("### Promotion Triggers (recertification request only)")
        for trigger in item["promotion_triggers"] or ["NONE"]:
            lines.append(f"- {trigger}")
        lines.append("")
        lines.append("### Demotion Triggers")
        for trigger in item["demotion_triggers"] or ["NONE"]:
            lines.append(f"- {trigger}")
        lines.append("")
        lines.append("### Source Limitations")
        for limitation in item["source_limitations"] or ["NONE"]:
            lines.append(f"- {limitation}")
    lines.extend(["", "## Global Notes", ""])
    for note in payload.get("global_notes") or ["NONE"]:
        lines.append(f"- {note}")
    safe_usage = {
        key: telemetry.get(key)
        for key in ("input_tokens", "cached_tokens", "output_tokens", "reasoning_output_tokens", "latency_ms", "usage_source")
        if telemetry.get(key) is not None
    }
    lines.extend([
        "",
        "## Sidecar Runtime Metadata",
        "",
        "```json",
        json.dumps(safe_usage, ensure_ascii=False, sort_keys=True, indent=2),
        "```",
        "",
        "## Hard Boundary",
        "",
        "A PRE-A trigger is **not** an A-/A promotion. It may only justify a separate blind recertification using the authoritative Stock Agent rules.",
        "",
    ])
    return "\n".join(lines)


def generate_pre_a_report(
    source_report: Path,
    output_path: Path,
    *,
    provider: Any,
    prompt_text: str,
    reasoning_effort: str,
) -> Path:
    source_report = source_report.resolve()
    output_path = output_path.resolve()
    if source_report == output_path:
        raise PreASidecarError("PRE-A output must not overwrite the PRIMARY source report")
    if not source_report.is_file():
        raise PreASidecarError("source DAILY_REPORT.md does not exist")
    report_text = source_report.read_text(encoding="utf-8")
    if not report_text.strip():
        raise PreASidecarError("source DAILY_REPORT.md is empty")
    if len(report_text.encode("utf-8")) > 2_000_000:
        raise PreASidecarError("source DAILY_REPORT.md exceeds the sidecar input bound")

    request = {
        "prompt_id": "sidecar.pre_a_report_v1",
        "messages": [
            {"role": "system", "content": prompt_text},
            {
                "role": "user",
                "content": "SOURCE_DAILY_REPORT_DATA_BEGIN\n" + report_text + "\nSOURCE_DAILY_REPORT_DATA_END",
            },
        ],
        "prompt_body": prompt_text,
        "output_schema_definition": PRE_A_SCHEMA,
        "reasoning_effort": reasoning_effort,
        "max_tokens": 10000,
        "attempt": 1,
    }
    payload, telemetry = provider.call(request)
    validate_sidecar_payload(payload, report_text)
    source_hash = _sha256_text(report_text)
    rendered = _render_markdown(
        source_report=source_report,
        source_hash=source_hash,
        payload=payload,
        provider_name=str(getattr(provider, "provider", "unknown")),
        model_name=str(getattr(provider, "model", "unknown")),
        telemetry=dict(telemetry or {}),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output_path)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a non-authoritative PRE-A report from one completed PRIMARY DAILY_REPORT.md")
    parser.add_argument("--source-report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--llm-provider", choices=["luna", "codex"], default="luna")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    parser.add_argument("--prompt", type=Path)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[1]
    load_environment(project_root)
    prompt_path = args.prompt or project_root / "docs" / "pre_a" / "PRE_A_REPORT_PROMPT_V1.md"
    if not prompt_path.is_file():
        raise PreASidecarError("PRE-A prompt file is missing")
    prompt_text = prompt_path.read_text(encoding="utf-8")

    run_name = args.source_report.parent.name or "unknown-run"
    output_path = args.output or project_root / "pre_a_reports" / run_name / "PRE_A_REPORT.md"

    if args.llm_provider == "luna":
        effort = args.reasoning_effort or os.getenv("LUNA_DEEP_REASONING_EFFORT", "high")
        provider = OpenAIResponsesProvider(
            require_secret("OPENAI_API_KEY"),
            os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
            reasoning_effort=effort,
            timeout=float(os.getenv("LUNA_TIMEOUT_SEC", "90")),
        )
    else:
        effort = args.reasoning_effort or "high"
        provider = CodexExecProvider(
            binary=os.getenv("CODEX_EXEC_BIN", "codex"),
            timeout=float(os.getenv("CODEX_EXEC_TIMEOUT_SEC", "120")),
        )

    target = generate_pre_a_report(
        args.source_report,
        output_path,
        provider=provider,
        prompt_text=prompt_text,
        reasoning_effort=effort,
    )
    print(json.dumps({
        "ok": True,
        "source_report": str(args.source_report),
        "pre_a_report": str(target),
        "authoritative": False,
        "primary_mutation": False,
        "broker_write_count": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
