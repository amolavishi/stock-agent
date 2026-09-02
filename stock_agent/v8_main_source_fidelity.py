"""Exact V8.4 source-lock bridge for MAIN Discovery.

The production scanner contract is self-contained: a fresh checkout carries the
canonical V8.4 Discovery common contract, canonical universe rules and exact
02..14 scanner profiles. Every file is verified against an independent source
lock with SHA-256 over raw bytes. No newline normalization, paraphrase fallback
or model-generated substitute is permitted.

This module owns source identity only. It creates no candidates, Research Grade,
PRE-A status, execution action, position size, or broker write.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import zipfile
from typing import Any

from . import v8_main_discovery_coach as coach
from .hunt_resilience_v17 import _project_value
from .providers import OpenAIResponsesProvider, ProviderRequestError

V8_MAIN_SOURCE_FIDELITY_VERSION = "V8_MAIN_SOURCE_FIDELITY_V8_4_V2.0"
V8_4_SOURCE_LOCK_VERSION = "V8_4_DISCOVERY_SOURCE_LOCK_V1.0"
V8_4_PACKAGE_VERSION = "8.4.0"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO_ROOT / "docs" / "v8_canonical" / "V8_4_DISCOVERY_SOURCE_MANIFEST.json"
_PACKAGED_ROOT = _REPO_ROOT / "prompts" / "v8_4"

_RUNTIME_ADDENDUM = r"""

# STOCK AGENT MAIN RUNTIME ADAPTER — NON-STRATEGY AUTHORITY
The three exact V8.4 source sections above are the strategy authority. This
adapter only maps them to the repository's structured runtime.

- Execute HUNT_ONLY_RECALL_FIRST.
- Evaluate the supplied candidate universe/evidence packet; do not merely repeat
  the scanner theme.
- Research Grade A/A-/B+/B, PRE-A status and Execution Action are forbidden.
- Scanner-local Discovery priority is research ordering only; do not compare
  numeric priority across scanners.
- UNKNOWN is neither PASS nor FAIL. Preserve decision-relevant unknowns and
  exact next-verification questions.
- A verified cheap structural hard failure may be EXCLUDE. Missing expensive
  research is DISCOVERY_INSUFFICIENT / Secondary, not a synthetic hard fail.
- Return only the attached runtime JSON schema.
- scanner_id, scanner_source_sha256 and screened_count must match Python's
  execution contract. grade_authority must be false.
"""

_SOURCE_STATE: dict[str, dict[str, Any]] = {}
_CORE_STATE: dict[str, dict[str, Any]] = {}
_LOCK_CACHE: dict[str, Any] | None = None
_PREPARED = False
_INSTALLED = False


class V8SourceIntegrityError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_lock() -> dict[str, Any]:
    global _LOCK_CACHE
    if _LOCK_CACHE is not None:
        return _LOCK_CACHE
    try:
        raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise V8SourceIntegrityError(f"V8.4 source lock unavailable: {_MANIFEST}") from exc
    if str(raw.get("manifest_version") or "") != V8_4_SOURCE_LOCK_VERSION:
        raise V8SourceIntegrityError("unexpected V8.4 source-lock version")
    if str(raw.get("package_version") or "") != V8_4_PACKAGE_VERSION:
        raise V8SourceIntegrityError("unexpected V8.4 package version")
    scanner_ids = {str(item.get("scanner_id") or "") for item in raw.get("scanners") or [] if isinstance(item, dict)}
    if scanner_ids != set(coach.V8_SCANNERS):
        raise V8SourceIntegrityError(f"V8.4 scanner lock mismatch: {sorted(scanner_ids)}")
    core_roles = {str(item.get("role") or "") for item in raw.get("core") or [] if isinstance(item, dict)}
    if core_roles != {"discovery_common_contract", "canonical_us_universe_rules"}:
        raise V8SourceIntegrityError(f"V8.4 core source lock mismatch: {sorted(core_roles)}")
    _LOCK_CACHE = raw
    return raw


def _scanner_entries() -> dict[str, dict[str, Any]]:
    return {
        str(item["scanner_id"]): dict(item)
        for item in _load_lock().get("scanners") or []
        if isinstance(item, dict) and item.get("scanner_id")
    }


def _core_entries() -> dict[str, dict[str, Any]]:
    return {
        str(item["role"]): dict(item)
        for item in _load_lock().get("core") or []
        if isinstance(item, dict) and item.get("role")
    }


def prepare_v8_4_source_lock() -> None:
    """Pin coach scanner identities to the V8.4 lock before schemas are built."""
    global _PREPARED
    if _PREPARED:
        return
    for sid, entry in _scanner_entries().items():
        coach.V8_SCANNERS[sid]["sha256"] = str(entry["sha256"])
        coach.V8_SCANNERS[sid]["source_file"] = f"prompts/v8_4/{entry['path']}"
        coach.V8_SCANNERS[sid]["source_package_version"] = V8_4_PACKAGE_VERSION
    _PREPARED = True


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.getenv("V8_SOURCE_ROOT", "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    roots.append(_PACKAGED_ROOT)
    return roots


def _archive_candidates() -> list[Path]:
    values: list[Path] = []
    configured = os.getenv("V8_SOURCE_ARCHIVE", "").strip()
    if configured:
        values.append(Path(configured).expanduser())
    return values


def _read_source_bytes(relative_path: str) -> tuple[bytes | None, str | None]:
    filename = Path(relative_path).name
    for root in _candidate_roots():
        path = root / relative_path
        if path.is_file():
            return path.read_bytes(), str(path)
        flat = root / filename
        if flat != path and flat.is_file():
            return flat.read_bytes(), str(flat)
    for archive in _archive_candidates():
        if not archive.is_file():
            continue
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                matches = [name for name in zf.namelist() if Path(name).name == filename]
                if len(matches) == 1:
                    return zf.read(matches[0]), f"{archive}!/{matches[0]}"
        except (OSError, zipfile.BadZipFile, KeyError):
            continue
    return None, None


def _resolve_entry(entry: dict[str, Any], *, source_id: str) -> dict[str, Any]:
    expected = str(entry.get("sha256") or "")
    expected_bytes = int(entry.get("bytes") or 0)
    relative_path = str(entry.get("path") or "")
    data, location = _read_source_bytes(relative_path)
    result: dict[str, Any] = {
        "source_id": source_id,
        "source_file": f"prompts/v8_4/{relative_path}",
        "expected_sha256": expected,
        "expected_bytes": expected_bytes,
        "actual_sha256": None,
        "actual_bytes": None,
        "source_location": location,
        "status": "MISSING",
        "source_text": None,
    }
    if data is None:
        return result
    actual = _sha(data)
    result["actual_sha256"] = actual
    result["actual_bytes"] = len(data)
    if actual != expected:
        result["status"] = "HASH_MISMATCH"
        return result
    if len(data) != expected_bytes:
        result["status"] = "BYTE_COUNT_MISMATCH"
        return result
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        result["status"] = "DECODE_FAILURE"
        return result
    result["status"] = "PASS"
    result["source_text"] = text
    return result


def resolve_core_sources() -> dict[str, dict[str, Any]]:
    for role, entry in _core_entries().items():
        _CORE_STATE[role] = _resolve_entry(entry, source_id=f"CORE:{role}")
    return {key: dict(value) for key, value in _CORE_STATE.items()}


def resolve_scanner_source(scanner_id: str) -> dict[str, Any]:
    prepare_v8_4_source_lock()
    sid = str(scanner_id)
    entry = _scanner_entries()[sid]
    result = _resolve_entry(entry, source_id=sid)
    result["scanner_id"] = sid
    result["source_package_version"] = V8_4_PACKAGE_VERSION
    _SOURCE_STATE[sid] = dict(result)
    return result


def source_bundle_status() -> dict[str, Any]:
    prepare_v8_4_source_lock()
    core = resolve_core_sources()
    scanner_rows = [resolve_scanner_source(sid) for sid in sorted(coach.V8_SCANNERS)]
    core_rows = list(core.values())
    all_rows = core_rows + scanner_rows
    return {
        "version": V8_MAIN_SOURCE_FIDELITY_VERSION,
        "manifest_version": V8_4_SOURCE_LOCK_VERSION,
        "package_version": V8_4_PACKAGE_VERSION,
        "canonical_package": str(_load_lock().get("canonical_package") or ""),
        "canonical_runtime_tree_hash": str(_load_lock().get("canonical_runtime_tree_hash") or ""),
        "complete": all(row["status"] == "PASS" for row in all_rows),
        "scanner_count": len(scanner_rows),
        "pass_count": sum(row["status"] == "PASS" for row in scanner_rows),
        "core_count": len(core_rows),
        "core_pass_count": sum(row["status"] == "PASS" for row in core_rows),
        "rows": [{k: v for k, v in row.items() if k != "source_text"} for row in scanner_rows],
        "core_rows": [{k: v for k, v in row.items() if k != "source_text"} for row in core_rows],
        "all_rows": [{k: v for k, v in row.items() if k != "source_text"} for row in all_rows],
    }


def _compiled_body(scanner_id: str) -> tuple[str, dict[str, Any]]:
    sid = str(scanner_id)
    core = resolve_core_sources()
    scanner = resolve_scanner_source(sid)
    required = [core["discovery_common_contract"], core["canonical_us_universe_rules"], scanner]
    failed = [item for item in required if item["status"] != "PASS"]
    if failed:
        detail = ",".join(f"{item['source_id']}:{item['status']}" for item in failed)
        raise V8SourceIntegrityError("V8_SOURCE_INTEGRITY:" + detail)
    body = (
        str(core["discovery_common_contract"]["source_text"])
        + "\n\n# --- CANONICAL UNIVERSE AUTHORITY ---\n\n"
        + str(core["canonical_us_universe_rules"]["source_text"])
        + "\n\n# --- SCANNER-LOCAL AUTHORITY ---\n\n"
        + str(scanner["source_text"])
        + _RUNTIME_ADDENDUM
    )
    meta = {
        "scanner_source_sha256": scanner["actual_sha256"],
        "common_contract_sha256": core["discovery_common_contract"]["actual_sha256"],
        "universe_rules_sha256": core["canonical_us_universe_rules"]["actual_sha256"],
        "source_package_version": V8_4_PACKAGE_VERSION,
        "compiled_prompt_sha256": _sha(body.encode("utf-8")),
    }
    return body, meta


def _source_backed_install(runtime: Any) -> None:
    prepare_v8_4_source_lock()
    _ORIGINAL_INSTALL_PROMPTS(runtime)
    for sid in sorted(coach.V8_SCANNERS):
        try:
            body, meta = _compiled_body(sid)
            status = "PASS"
        except V8SourceIntegrityError as exc:
            body = (
                "V8_SOURCE_INTEGRITY_BLOCKED\n"
                f"scanner_id={sid}\nerror={exc}\n"
                "Do not perform discovery with reconstructed or paraphrased source."
            )
            meta = {
                "scanner_source_sha256": coach.V8_SCANNERS[sid]["sha256"],
                "common_contract_sha256": None,
                "universe_rules_sha256": None,
                "source_package_version": V8_4_PACKAGE_VERSION,
                "compiled_prompt_sha256": _sha(body.encode("utf-8")),
            }
            status = "BLOCKED"
        coach._register_prompt(runtime, f"v8_main.discovery_{sid}", coach.SCANNER_SCHEMA_ID, body)
        spec = coach.V8_SCANNERS[sid]
        spec.update(meta)
        spec["source_integrity_status"] = status
        spec["runtime_prompt_sha256"] = meta["compiled_prompt_sha256"]


def _scanner_provider_call(self: OpenAIResponsesProvider, request: dict[str, Any]):
    prompt_id = str(request.get("prompt_id") or "")
    if prompt_id.startswith("v8_main.discovery_") and prompt_id != "v8_main.discovery_rejection_sentinel":
        sid = prompt_id.rsplit("_", 1)[-1]
        status = source_bundle_status()
        if not status.get("complete"):
            failed = [
                f"{item.get('source_id')}:{item.get('status')}"
                for item in status.get("all_rows") or []
                if item.get("status") != "PASS"
            ]
            raise ProviderRequestError("V8 source integrity failure " + ",".join(failed), retryable=False)
        state = _SOURCE_STATE.get(sid) or resolve_scanner_source(sid)
        if state.get("status") != "PASS":
            raise ProviderRequestError(
                f"V8 source integrity failure scanner={sid} status={state.get('status')}", retryable=False
            )
        cloned = copy.deepcopy(request)
        runtime_input = cloned.get("runtime_input")
        if runtime_input not in (None, {}, []):
            messages = [dict(item) for item in (cloned.get("messages") or []) if isinstance(item, dict)]
            messages.append({
                "role": "user",
                "content": "V8_CANONICAL_SCANNER_RUNTIME_INPUT\n" + json.dumps(
                    _project_value(runtime_input, key="runtime_input", aggressive=False),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            })
            cloned["messages"] = messages
            cloned["runtime_input"] = {}
        return _BASE_PROVIDER_CALL(self, cloned)
    return _BASE_PROVIDER_CALL(self, request)


def install_v8_main_source_fidelity() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    prepare_v8_4_source_lock()
    coach._install_prompts = _source_backed_install  # type: ignore[assignment]
    OpenAIResponsesProvider.call = _scanner_provider_call  # type: ignore[assignment]
    _INSTALLED = True


_ORIGINAL_INSTALL_PROMPTS = coach._install_prompts
_BASE_PROVIDER_CALL = OpenAIResponsesProvider.call
