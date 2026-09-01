"""Exact-source integrity bridge for V8 MAIN Discovery.

The canonical V8 manifest on MAIN used to be declarative only: it named the
02..14 files and hashes while the files were not loaded by the runtime. This
module makes source identity executable. It patches the MAIN scanner coach so
that each scanner prompt is compiled from the actual source body only after an
exact SHA-256 check. A missing/mismatched source is a run-global input
integrity failure; it is never replaced with a paraphrased scanner.

This module does not create candidates, Research Grade, PRE-A status, actions,
position sizes, or broker writes.
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

V8_MAIN_SOURCE_FIDELITY_VERSION = "V8_MAIN_SOURCE_FIDELITY_V1.0"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO_ROOT / "docs" / "v8_canonical" / "SOURCE_MANIFEST.json"

_SCANNER_FILES = {
    "02": "02_비AI_비반도체_광역_블라인드_Discovery_V8.md",
    "03": "03_최근_IPO_Busted_IPO_재평가_Discovery_V8.md",
    "04": "04_턴어라운드_실적주_Discovery_V8.md",
    "05": "05_정책_이벤트_국방_원전_우라늄_핵심광물_에너지_안보_Discovery_V8.md",
    "06": "06_우주_방산_ISR_항공우주_부품_Discovery_V8.md",
    "07": "07_덜_알려진_수익성_개선_소형주_Discovery_V8.md",
    "08": "08_공모_블록딜_Secondary_소화_후_회복주_Discovery_V8.md",
    "09": "09_내부자_매수_자사주_방어형_턴어라운드_Discovery_V8.md",
    "10": "10_부채_리파이낸싱_파산위험_제거형_Discovery_V8.md",
    "11": "11_실적_후_추정치_상향_지연반응주_Discovery_V8.md",
    "12": "12_고객집중_해소_두_번째_대형고객_확보주_Discovery_V8.md",
    "13": "13_핀테크_헬스케어_비반도체_소프트웨어_로테이션_Discovery_V8.md",
    "14": "14_AI_병목_확장_예외_후보_Discovery_V8.md",
}

_RUNTIME_ADDENDUM = r"""

# STOCK AGENT MAIN RUNTIME CONTRACT — SOURCE-PRESERVING ADDENDUM
The V8 text above is the strategy authority for this scanner. The following
requirements only adapt its output to the repository's structured runtime; they
do not replace, summarize, weaken, or reinterpret the V8 strategy.

- Execute in HUNT_ONLY_RECALL_FIRST mode.
- Evaluate the supplied candidate universe/evidence packet; do not merely name
  a theme or repeat the scanner description.
- Research Grade A/A-/B+/B is forbidden here.
- Discovery priority is research ordering only.
- UNKNOWN is neither PASS nor FAIL; preserve decision-relevant unknowns and
  exact verification questions.
- Use DEEP_DIVE_SECONDARY for high-information-value unresolved cases rather
  than silently rejecting them, unless a verified cheap structural hard gate
  actually fails.
- Distinguish signal strength from research value.
- Return only the runtime JSON object required by the attached schema. Tables
  requested by the source are represented by the structured candidate fields;
  the source strategy logic itself remains binding.
- scanner_id, scanner_source_sha256 and screened_count must match the Python
  execution contract. grade_authority must be false.
"""

_SOURCE_STATE: dict[str, dict[str, Any]] = {}
_INSTALLED = False


class V8SourceIntegrityError(RuntimeError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_entries() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise V8SourceIntegrityError(f"canonical V8 manifest unavailable: {_MANIFEST}") from exc
    entries: dict[str, dict[str, Any]] = {}
    for item in raw.get("files") or []:
        if isinstance(item, dict) and item.get("file"):
            entries[str(item["file"])] = dict(item)
    return entries


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.getenv("V8_SOURCE_ROOT", "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    roots.append(_REPO_ROOT / "prompts" / "v8")
    return roots


def _archive_candidates() -> list[Path]:
    values: list[Path] = []
    configured = os.getenv("V8_SOURCE_ARCHIVE", "").strip()
    if configured:
        values.append(Path(configured).expanduser())
    values.extend(sorted(_REPO_ROOT.glob("STOCK_SCANNING_PROMPTS_V8_A_GRADE_PIPELINE*.zip")))
    return values


def _read_source_bytes(filename: str) -> tuple[bytes | None, str | None]:
    for root in _candidate_roots():
        path = root / filename
        if path.is_file():
            return path.read_bytes(), str(path)
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


def resolve_scanner_source(scanner_id: str) -> dict[str, Any]:
    sid = str(scanner_id)
    filename = _SCANNER_FILES[sid]
    manifest_path = f"prompts/v8/{filename}"
    entry = _manifest_entries().get(manifest_path)
    expected = str((entry or {}).get("sha256") or coach.V8_SCANNERS[sid]["sha256"])
    data, location = _read_source_bytes(filename)
    if data is None:
        result = {
            "scanner_id": sid,
            "source_file": manifest_path,
            "expected_sha256": expected,
            "actual_sha256": None,
            "source_location": None,
            "status": "MISSING",
            "source_text": None,
        }
    else:
        actual = _sha(data)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        status = "PASS" if actual == expected and text is not None else ("HASH_MISMATCH" if actual != expected else "DECODE_FAILURE")
        result = {
            "scanner_id": sid,
            "source_file": manifest_path,
            "expected_sha256": expected,
            "actual_sha256": actual,
            "source_location": location,
            "status": status,
            "source_text": text if status == "PASS" else None,
        }
    _SOURCE_STATE[sid] = dict(result)
    return result


def source_bundle_status() -> dict[str, Any]:
    rows = [resolve_scanner_source(sid) for sid in sorted(_SCANNER_FILES)]
    return {
        "version": V8_MAIN_SOURCE_FIDELITY_VERSION,
        "complete": all(row["status"] == "PASS" for row in rows),
        "scanner_count": len(rows),
        "pass_count": sum(row["status"] == "PASS" for row in rows),
        "rows": [{k: v for k, v in row.items() if k != "source_text"} for row in rows],
    }


def _source_backed_install(runtime: Any) -> None:
    _ORIGINAL_INSTALL_PROMPTS(runtime)
    for sid in sorted(_SCANNER_FILES):
        state = resolve_scanner_source(sid)
        if state["status"] == "PASS":
            body = str(state["source_text"]) + _RUNTIME_ADDENDUM
        else:
            body = (
                "V8_SOURCE_INTEGRITY_BLOCKED\n"
                f"scanner_id={sid}\nsource_file={state['source_file']}\n"
                f"expected_sha256={state['expected_sha256']}\nstatus={state['status']}\n"
                "Do not perform discovery with a reconstructed or paraphrased strategy."
            )
        coach._register_prompt(runtime, f"v8_main.discovery_{sid}", coach.SCANNER_SCHEMA_ID, body)
        spec = coach.V8_SCANNERS[sid]
        spec["source_file"] = str(state["source_file"])
        spec["source_integrity_status"] = str(state["status"])
        spec["runtime_prompt_sha256"] = _sha(body.encode("utf-8"))


def _scanner_provider_call(self: OpenAIResponsesProvider, request: dict[str, Any]):
    prompt_id = str(request.get("prompt_id") or "")
    if prompt_id.startswith("v8_main.discovery_") and prompt_id != "v8_main.discovery_rejection_sentinel":
        sid = prompt_id.rsplit("_", 1)[-1]
        state = _SOURCE_STATE.get(sid) or resolve_scanner_source(sid)
        if state.get("status") != "PASS":
            raise ProviderRequestError(
                f"V8 source integrity failure scanner={sid} status={state.get('status')} expected={state.get('expected_sha256')}",
                retryable=False,
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
    coach._install_prompts = _source_backed_install  # type: ignore[assignment]
    OpenAIResponsesProvider.call = _scanner_provider_call  # type: ignore[assignment]
    _INSTALLED = True


_ORIGINAL_INSTALL_PROMPTS = coach._install_prompts
_BASE_PROVIDER_CALL = OpenAIResponsesProvider.call
