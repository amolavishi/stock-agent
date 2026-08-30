"""Operator-triggered SHADOW_V1.1 orchestration and immutable evaluation logs.

This module does not contain investment logic.  It projects authoritative
SQLite state produced by the existing HUNT and execution runtimes into a
daily, human-readable report and point-in-time evaluation dataset.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import RunMode, canonical_hash, utc_now
from .vault import SecureVault, content_digest


# P0 correctness hotfixes are a new immutable Shadow minor version.  Existing
# V1.0 rows remain readable and append-only; new operator runs use V1.1.
SHADOW_VERSION = "SHADOW_V1.1"
HORIZONS = (1, 3, 5, 10, 20, 40)


def _git_value(root: Path, *arguments: str) -> str:
    environment = dict(os.environ)
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    try:
        completed = subprocess.run(
            ["git", *arguments], cwd=root, env=environment,
            text=True, encoding="utf-8", errors="replace",
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "UNKNOWN"
    return (completed.stdout or "").strip() if completed.returncode == 0 else "UNKNOWN"


def _tree_hash(root: Path) -> str:
    rows: list[tuple[str, str]] = []
    if not root.exists():
        return "UNKNOWN"
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rows.append((path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return canonical_hash(rows)


def _source_tree_hash(project_root: Path) -> str:
    """Hash production/test source while excluding generated run artifacts.

    Shadow metadata must identify the code that produced a run without making
    a compile-created ``__pycache__`` or an output directory part of the
    source identity.
    """
    rows: list[tuple[str, str]] = []
    for directory_name in ("stock_agent", "tests"):
        directory = project_root / directory_name
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rows.append((path.relative_to(project_root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()))
    return canonical_hash(rows) if rows else "UNKNOWN"


def _git_worktree_metadata(project_root: Path) -> dict[str, Any]:
    """Capture dirty-tree evidence without persisting source contents."""
    status = _git_value(project_root, "status", "--porcelain=v1", "--untracked-files=all")
    diff = _git_value(project_root, "diff", "HEAD")
    staged_diff = _git_value(project_root, "diff", "--cached", "HEAD")
    unavailable = status == diff == staged_diff == "UNKNOWN"
    return {
        "git_dirty": None if unavailable else bool(status),
        "git_status_hash": hashlib.sha256(status.encode("utf-8")).hexdigest() if status != "UNKNOWN" else "UNKNOWN",
        "git_diff_hash": canonical_hash([diff, staged_diff]) if not (diff == staged_diff == "UNKNOWN") else "UNKNOWN",
        "source_tree_hash": _source_tree_hash(project_root),
        "source_provenance_status": "UNKNOWN" if unavailable else ("DIRTY_WORKTREE" if status else "CLEAN_COMMITTED"),
    }


def validate_report_provenance(log: dict[str, Any], report_text: str) -> None:
    """Reject a human report whose source identity differs from its Run Log.

    Reports are projections only, but they must still identify the exact
    source snapshot that produced them.  Keeping this check in the report
    writer prevents a manually assembled acceptance report from silently
    claiming a different dirty-tree/hash state than the authoritative log.
    """
    markers = {
        "git_diff_hash": "Git Diff Hash:",
        "source_tree_hash": "Source Tree Hash:",
        "code_git_sha": "Git SHA:",
    }
    for key, marker in markers.items():
        expected = str(log.get(key) or "UNKNOWN")
        matches = [line.split(":", 1)[1].strip().strip("`")
                   for line in str(report_text).splitlines()
                   if line.strip().casefold().startswith(marker.casefold())]
        if matches and any(value != expected for value in matches):
            raise RuntimeError(f"REPORT_PROVENANCE_MISMATCH:{key}")


def reproducibility_metadata(
    project_root: Path,
    prompt_library_root: Path,
    *,
    model: str,
    provider: str,
    reasoning_effort: dict[str, str],
    config_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a secret-free runtime snapshot suitable for long-term audit."""
    safe_config = dict(config_values or {})
    for key in list(safe_config):
        if any(marker in key.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTHORIZATION", "COOKIE")):
            safe_config[key] = "REDACTED"
    lock = next((project_root / name for name in ("requirements.lock", "requirements.txt", "pyproject.toml") if (project_root / name).exists()), None)
    metadata = {
        "shadow_version": SHADOW_VERSION,
        "code_git_sha": _git_value(project_root, "rev-parse", "HEAD"),
        "branch": _git_value(project_root, "branch", "--show-current"),
        "prompt_library_hash": _tree_hash(prompt_library_root),
        "config_hash": canonical_hash(safe_config),
        "model": model,
        "provider": provider,
        "reasoning_effort": dict(reasoning_effort),
        "schema_version": "shadow-log-v1",
        "database_schema_version": "shadow-v1",
        "timezone": "Asia/Seoul",
        "python_version": platform.python_version(),
        "os": platform.platform(),
        "dependency_lock_hash": hashlib.sha256(lock.read_bytes()).hexdigest() if lock else "UNKNOWN",
        "broker_write_count": 0,
    }
    metadata.update(_git_worktree_metadata(project_root))
    return metadata


@dataclass(frozen=True)
class ShadowRunResult:
    shadow_run_id: str
    status: str
    hunt_run_id: str | None
    execution_run_id: str | None
    artifact_paths: dict[str, str]
    broker_write_count: int = 0


class LunaHealthChecker:
    """Lightweight transport health using a real Stock Agent output schema."""

    def __init__(self, provider: Any, prompt_runtime: Any) -> None:
        self.provider = provider
        self.prompt_runtime = prompt_runtime

    @staticmethod
    def _schema_example(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
        """Build a non-authoritative, schema-valid echo fixture for health.

        The value is sent only as a provider contract probe; it is never
        persisted or used by a strategy stage.  Supplying the complete shape
        prevents a model from omitting optional-looking fields when the wire
        Responses schema is validated locally.
        """
        if "$ref" in schema:
            return LunaHealthChecker._schema_example(defs.get(str(schema["$ref"]).split("/")[-1], {}), defs)
        if "const" in schema:
            return schema["const"]
        if schema.get("enum"):
            return schema["enum"][0]
        if schema.get("anyOf"):
            for option in schema["anyOf"]:
                if option.get("type") != "null":
                    return LunaHealthChecker._schema_example(option, defs)
            return None
        kind = schema.get("type")
        if kind == "object":
            return {str(key): LunaHealthChecker._schema_example(value, defs) for key, value in (schema.get("properties") or {}).items()}
        if kind == "array":
            minimum = int(schema.get("minItems", 0) or 0)
            return [LunaHealthChecker._schema_example(schema.get("items") or {}, defs) for _ in range(minimum)]
        if kind == "boolean":
            return False
        if kind == "integer":
            return 1
        if kind == "number":
            return 1.0
        if schema.get("format") == "date-time":
            return "2026-08-17T00:00:00Z"
        pattern = str(schema.get("pattern") or "")
        if "64" in pattern or "Hash" in pattern:
            return "0" * 64
        if pattern.startswith("^E"):
            return "E-HEALTH"
        return "health"

    def check(self) -> dict[str, Any]:
        prompt_id = "workflow.market_analyst"
        composed = self.prompt_runtime.compose(prompt_id)
        schema = dict(self.prompt_runtime.registry["schemas"][composed["output_schema"]])
        schema["$defs"] = self.prompt_runtime.registry["$defs"]
        example = self._schema_example(schema, self.prompt_runtime.registry["$defs"])
        payload, telemetry = self.provider.call({
            "prompt_id": prompt_id,
            "messages": [
                {"role": "system", "content": composed["compiled_prompt"]},
                {"role": "user", "content": "Provider health check. Return exactly this schema-valid JSON object; no external facts are requested:\n" + json.dumps(example, ensure_ascii=False, sort_keys=True)},
            ],
            "output_schema_definition": schema,
            "reasoning_effort": getattr(self.provider, "reasoning_effort", "medium"),
            "max_tokens": 2048,
        })
        errors = self.prompt_runtime.validate(composed["output_schema"], payload)
        if errors:
            raise ValueError("Luna health response failed Stock Agent stage schema")
        return {
            "status": "PASS",
            "model": telemetry.get("model"),
            "latency_ms": telemetry.get("latency_ms"),
            "usage_source": telemetry.get("usage_source"),
            "input_tokens": telemetry.get("input_tokens", 0),
            "cached_input_tokens": telemetry.get("cached_tokens", 0),
            "output_tokens": telemetry.get("output_tokens", 0),
            "reasoning_output_tokens": telemetry.get("reasoning_output_tokens", 0),
        }


class OutcomePITViolation(ValueError):
    """Raised when outcome bars are not bounded by the requested cutoff."""


class OutcomeTracker:
    """Attach later prices without mutating the original point-in-time decision."""

    PITViolation = OutcomePITViolation

    @staticmethod
    def _timestamp(value: Any, *, label: str) -> datetime:
        """Parse a bar/cutoff timestamp without silently coercing bad data."""
        if value in (None, ""):
            raise OutcomeTracker.PITViolation(f"{label} timestamp is missing")
        text = str(value).strip()
        try:
            # Date-only bars represent the UTC start of that trading date.
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise OutcomeTracker.PITViolation(f"{label} timestamp is malformed") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _prepare_bars(
        decision: dict[str, Any],
        bars: Iterable[dict[str, Any]],
        as_of: str | None,
    ) -> tuple[list[dict[str, Any]], datetime]:
        """Validate and materialize one immutable point-in-time bar set.

        A future bar is a data-integrity violation, not a row to ignore.  The
        same validated set is consumed by both return/MFE/MAE and lifecycle
        calculations so a cutoff cannot be applied to only one path.
        """
        materialized: list[dict[str, Any]] = []
        for index, row in enumerate(bars):
            if not isinstance(row, dict):
                raise OutcomeTracker.PITViolation(f"bar {index} is not an object")
            materialized.append(dict(row))
        timestamps: list[tuple[dict[str, Any], datetime]] = []
        for index, row in enumerate(materialized):
            raw_timestamp = row.get("date") or row.get("observed_at")
            timestamp = OutcomeTracker._timestamp(raw_timestamp, label=f"bar {index}")
            timestamps.append((row, timestamp))

        decision_time = OutcomeTracker._timestamp(decision.get("decision_time"), label="decision")
        if as_of is None:
            # Existing callers that predate the explicit cutoff API are still
            # deterministic: their cutoff is the latest supplied observation.
            # Production persistence always supplies as_of explicitly.
            cutoff = max((timestamp for _, timestamp in timestamps), default=decision_time)
        else:
            cutoff = OutcomeTracker._timestamp(as_of, label="outcome_as_of")
        if cutoff < decision_time:
            raise OutcomeTracker.PITViolation("outcome_as_of precedes decision timestamp")
        for index, (row, timestamp) in enumerate(timestamps):
            if timestamp > cutoff:
                raise OutcomeTracker.PITViolation(
                    f"bar {index} at {timestamp.isoformat()} is after outcome_as_of {cutoff.isoformat()}"
                )
            row["_pit_timestamp"] = timestamp
        return materialized, cutoff

    @staticmethod
    def _ordered_bars(
        decision: dict[str, Any],
        bars: Iterable[dict[str, Any]],
        as_of: str | None,
    ) -> list[dict[str, Any]]:
        materialized, _ = OutcomeTracker._prepare_bars(decision, bars, as_of)
        decision_time = OutcomeTracker._timestamp(decision.get("decision_time"), label="decision")
        return sorted(
            (row for row in materialized if row["_pit_timestamp"] > decision_time),
            key=lambda row: row["_pit_timestamp"],
        )

    @staticmethod
    def calculate(
        decision: dict[str, Any], bars: Iterable[dict[str, Any]], *, as_of: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        ordered = OutcomeTracker._ordered_bars(decision, bars, as_of)
        price = decision.get("decision_price")
        if not isinstance(price, (int, float)) or float(price) <= 0:
            return {}
        results: dict[str, dict[str, Any]] = {}
        for horizon in HORIZONS:
            if len(ordered) < horizon:
                continue
            window = ordered[:horizon]
            closes = [float(row["close"]) for row in window]
            highs = [float(row.get("high", row["close"])) for row in window]
            lows = [float(row.get("low", row["close"])) for row in window]
            results[f"{horizon}D"] = {
                "forward_return": closes[-1] / float(price) - 1.0,
                "mfe": max(highs) / float(price) - 1.0,
                "mae": min(lows) / float(price) - 1.0,
                "sessions_observed": horizon,
                "terminal_date": str(window[-1].get("date") or window[-1].get("observed_at"))[:10],
            }
        return results

    @staticmethod
    def shadow_lifecycle(
        decision: dict[str, Any], bars: Iterable[dict[str, Any]], *, as_of: str | None = None,
    ) -> dict[str, Any]:
        entry = decision.get("recommended_entry", decision.get("entry"))
        stop = decision.get("stop")
        target = decision.get("target")
        ordered = OutcomeTracker._ordered_bars(decision, bars, as_of)
        if not isinstance(entry, (int, float)):
            return {"status": "NOT_FILLED", "fill_price": None}
        fill_index: int | None = None
        for index, row in enumerate(ordered):
            if float(row.get("low", row.get("close"))) <= float(entry) <= float(row.get("high", row.get("close"))):
                fill_index = index
                break
        if fill_index is None:
            return {"status": "NOT_FILLED", "fill_price": None}
        for row in ordered[fill_index:]:
            stop_hit = isinstance(stop, (int, float)) and float(row.get("low", row["close"])) <= float(stop)
            target_hit = isinstance(target, (int, float)) and float(row.get("high", row["close"])) >= float(target)
            if stop_hit and target_hit:
                return {"status": "AMBIGUOUS_INTRADAY", "fill_price": float(entry)}
            if stop_hit:
                return {"status": "STOPPED", "fill_price": float(entry), "exit_price": float(stop)}
            if target_hit:
                return {"status": "TARGET_HIT", "fill_price": float(entry), "exit_price": float(target)}
        return {"status": "OPEN", "fill_price": float(entry)}


class DailyShadowRunner:
    """Run existing authoritative engines and archive their immutable truth."""

    _NOT_EVALUATED_ORDER = (
        ("SEC_STALE_DATA", "NOT_EVALUATED_SEC_DATA", "SEC", True),
        ("SEC_PROVIDER_FAILURE", "NOT_EVALUATED_SEC_PROVIDER", "SEC", True),
        ("RESEARCH_PROVIDER_FAILURE", "NOT_EVALUATED_RESEARCH_PROVIDER", "RESEARCH", True),
    )

    _REJECTION_ORDER = (
        ("STAGE_GATE", "REJECTED_DISCOVERY"),
        ("CAPITAL_PRESCREEN_GATE", "REJECTED_PRESCREEN"),
        ("CATALYST_GATE", "REJECTED_CATALYST"),
        ("EXPECTATION_GAP_GATE", "REJECTED_EXPECTATION_GAP"),
        ("FULL_SEC_FORENSIC", "REJECTED_SEC"),
        ("STANDARD_AUDIT", "REJECTED_STANDARD_AUDIT"),
        ("ADVERSARIAL_AUDIT", "REJECTED_ADVERSARIAL_AUDIT"),
    )

    def __init__(
        self,
        agent: Any,
        output_root: str | Path,
        metadata: dict[str, Any],
        *,
        provider_health: Callable[[], dict[str, Any]] | None = None,
        shadow_version: str = SHADOW_VERSION,
    ) -> None:
        self.agent = agent
        self.store = agent.store
        self.vault = SecureVault(output_root)
        self.metadata = dict(metadata)
        self.provider_health = provider_health
        self.shadow_version = shadow_version

    def _stage_rows(self, run_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self.store.connection.execute(
            "SELECT * FROM stage_results WHERE run_id=? ORDER BY created_at,result_id", (run_id,),
        ).fetchall()]

    @staticmethod
    def _decode(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value

    def _decisions(self, shadow_run_id: str, hunt_run_id: str, execution_run_id: str | None) -> list[dict[str, Any]]:
        existing = self.store.list_shadow_decisions(shadow_run_id)
        if existing:
            return existing
        rows = self._stage_rows(hunt_run_id)
        subjects = sorted({str(row["subject_id"]) for row in rows if row.get("subject_id")})
        execution_action: dict[str, dict[str, Any]] = {}
        if execution_run_id:
            for row in self.store.connection.execute("SELECT * FROM final_actions WHERE run_id=?", (execution_run_id,)).fetchall():
                execution_action[str(row["subject_id"])] = dict(row)
        decisions: list[dict[str, Any]] = []
        for subject in subjects:
            stage_values = {
                str(row["stage"]): self._decode(row["result_json"])
                for row in rows
                if str(row.get("subject_id") or "") == subject
                and (row.get("status") == "SUCCEEDED" or str(row.get("stage")) in {"SEC_STALE_DATA", "SEC_PROVIDER_FAILURE", "RESEARCH_PROVIDER_FAILURE"})
            }
            try:
                qualified, _ = self.store.qualified_candidate_status(hunt_run_id, subject, strict=True)
            except Exception:
                qualified = False
            decision = "QUALIFIED" if qualified else "WATCH"
            watch = not qualified
            rejected_stage = None
            rejection_reason = None
            not_evaluated_stage = None
            not_evaluated_reason = None

            # Provider/data failures are operational attribution, never an
            # investment rejection.  Surface them explicitly and emit an
            # idempotent Shadow incident so 30-day rejection statistics remain
            # economically meaningful.
            for stage, label, component, retryable in self._NOT_EVALUATED_ORDER:
                value = stage_values.get(stage)
                if not isinstance(value, dict):
                    continue
                decision = label
                watch = False
                not_evaluated_stage = stage
                not_evaluated_reason = str(value.get("reason") or value.get("decision") or value.get("status") or stage)
                incident_id = f"incident-{canonical_hash([shadow_run_id, subject, stage, not_evaluated_reason])[:24]}"
                existing = self.store.connection.execute(
                    "SELECT 1 FROM shadow_incidents WHERE incident_id=?", (incident_id,)
                ).fetchone()
                if existing is None:
                    stage_row = next((row for row in rows if str(row.get("subject_id") or "") == subject and str(row.get("stage")) == stage), None)
                    self.store.append_shadow_incident(shadow_run_id, {
                        "incident_id": incident_id,
                        "detected_at": (stage_row or {}).get("created_at") or utc_now(),
                        "run_id": shadow_run_id,
                        "ticker": subject,
                        "shadow_version": self.shadow_version,
                        "severity": "S2",
                        "component": component,
                        "stage": stage,
                        "failure_code": stage,
                        "provider": value.get("provider"),
                        "retryable": retryable,
                        "description": not_evaluated_reason[:500],
                        "impact": "CANDIDATE_NOT_EVALUATED",
                        "status": "OPEN",
                    })
                break

            if not not_evaluated_stage:
                for stage, label in self._REJECTION_ORDER:
                    value = stage_values.get(stage)
                    if isinstance(value, dict):
                        if stage == "CATALYST_GATE" and value.get("evaluation_status") == "NOT_EVALUATED_CATALYST_EVIDENCE":
                            decision = "NOT_EVALUATED_CATALYST"
                            watch = False
                            not_evaluated_stage = stage
                            not_evaluated_reason = "NOT_EVALUATED_CATALYST_EVIDENCE"
                            break
                        state = value.get("decision") or value.get("status") or value.get("audit_recommendation")
                        if state in {"REJECT", "INSUFFICIENT_EVIDENCE", "INCOMPLETE", "BLOCK", "DOES_NOT_SUPPORT", "FAILED"}:
                            decision, rejected_stage, rejection_reason = label, stage, str(state)
                            watch = False
                            break
            reverse = stage_values.get("REVERSE_VALUATION") if isinstance(stage_values.get("REVERSE_VALUATION"), dict) else {}
            probability = stage_values.get("CAP_DIRECTIONAL_PROBABILITY") if isinstance(stage_values.get("CAP_DIRECTIONAL_PROBABILITY"), dict) else {}
            dependency_ids: set[str] = set()
            for row in rows:
                if str(row.get("subject_id") or "") == subject:
                    values = self._decode(row.get("dependency_ids_json") or "[]")
                    if isinstance(values, list):
                        dependency_ids.update(str(value) for value in values)
            action = execution_action.get(subject) or {}
            record = {
                "run_id": shadow_run_id,
                "authoritative_hunt_run_id": hunt_run_id,
                "authoritative_execution_run_id": execution_run_id,
                "decision_id": f"decision-{canonical_hash([shadow_run_id, subject])[:24]}",
                "ticker": subject,
                "company": None,
                "decision_time": utc_now(),
                "decision_price": reverse.get("current_price"),
                "market_regime": None,
                "sector": None,
                "sector_rank": None,
                "discovery_rank": None,
                "stage": (stage_values.get("STAGE_GATE") or {}).get("decision") if isinstance(stage_values.get("STAGE_GATE"), dict) else None,
                "fundamental_change_status": (stage_values.get("CAP_FUNDAMENTAL_CHANGE") or {}).get("status") if isinstance(stage_values.get("CAP_FUNDAMENTAL_CHANGE"), dict) else None,
                "fundamental_change_score": None,
                "catalyst_type": None,
                "catalyst_date": None,
                "catalyst_score": None,
                "expectation_gap_score": reverse.get("benchmark_implied_upside_pct"),
                "directional_probability": probability or None,
                "sec_risk": (stage_values.get("FULL_SEC_FORENSIC") or {}).get("status") if isinstance(stage_values.get("FULL_SEC_FORENSIC"), dict) else None,
                "dilution_risk": (stage_values.get("CAPITAL_PRESCREEN_GATE") or {}).get("decision") if isinstance(stage_values.get("CAPITAL_PRESCREEN_GATE"), dict) else None,
                "valuation_bear": probability.get("bear_value"),
                "valuation_base": probability.get("base_value"),
                "valuation_bull": probability.get("bull_value"),
                "probability_bear": probability.get("bear_probability"),
                "probability_base": probability.get("base_probability"),
                "probability_bull": probability.get("bull_probability"),
                "normalized_score": None,
                "grade": None,
                "decision": decision,
                "qualified": bool(qualified),
                "watch": watch,
                "rejected": decision.startswith("REJECTED_"),
                "rejected_stage": rejected_stage,
                "rejection_reason": rejection_reason,
                "not_evaluated": decision.startswith("NOT_EVALUATED_"),
                "not_evaluated_stage": not_evaluated_stage,
                "not_evaluated_reason": not_evaluated_reason,
                "entry": None,
                "recommended_entry": None,
                "stop": None,
                "target": None,
                "risk_reward": None,
                "final_allocation_action": action.get("action"),
                "evidence_ids": sorted(dependency_ids),
            }
            self.store.append_shadow_decision(shadow_run_id, record)
            decisions.append(record)
        return decisions

    def _evidence_manifest(self, run_ids: list[str]) -> list[dict[str, Any]]:
        # Point-in-time eligibility is bounded by the immutable decision
        # timestamp, not by run creation.  Evidence retrieved during a run is
        # valid when it was observed before the decision was materialized.
        # Build subject-specific cutoffs from persisted Shadow decisions; do
        # not fall back to runs.created_at or to the current wall clock.
        requested_run_ids = sorted({str(value) for value in run_ids if value})
        shadow_ids: set[str] = set()
        if requested_run_ids:
            placeholders = ",".join("?" for _ in requested_run_ids)
            shadow_rows = self.store.connection.execute(
                f"SELECT shadow_run_id FROM shadow_runs WHERE hunt_run_id IN ({placeholders}) "
                f"OR execution_run_id IN ({placeholders})",
                tuple(requested_run_ids) + tuple(requested_run_ids),
            ).fetchall()
            shadow_ids.update(str(row["shadow_run_id"]) for row in shadow_rows)
        if not shadow_ids:
            raise RuntimeError("future Evidence cannot be evaluated without an immutable decision cutoff")
        decision_placeholders = ",".join("?" for _ in sorted(shadow_ids))
        decision_rows = self.store.connection.execute(
            f"SELECT decision_json FROM shadow_decisions WHERE shadow_run_id IN ({decision_placeholders})",
            tuple(sorted(shadow_ids)),
        ).fetchall()
        if not decision_rows:
            # A valid live HUNT can finish with no opportunity before any
            # security-level decision is materialized.  There is then no
            # point-in-time Evidence set to export; returning an empty
            # manifest preserves the no-opportunity result without inventing
            # a decision cutoff or treating the run as a runtime failure.
            subject_rows = self.store.connection.execute(
                f"SELECT 1 FROM stage_results WHERE run_id IN ({','.join('?' for _ in requested_run_ids)}) "
                "AND subject_id IS NOT NULL LIMIT 1",
                tuple(requested_run_ids),
            ).fetchall() if requested_run_ids else []
            if not subject_rows:
                return []
            raise RuntimeError("future Evidence cannot be evaluated without a persisted decision cutoff")
        decision_cutoffs: dict[str, datetime] = {}
        all_decision_cutoffs: list[datetime] = []
        for row in decision_rows:
            try:
                decision = json.loads(row["decision_json"] or "{}")
                if not isinstance(decision, dict):
                    raise ValueError("decision payload is not an object")
                cutoff = OutcomeTracker._timestamp(decision.get("decision_time"), label="decision")
            except (TypeError, ValueError, KeyError, OutcomeTracker.PITViolation) as exc:
                raise RuntimeError("future Evidence cannot be evaluated with malformed decision cutoff") from exc
            subject = str(decision.get("security_id") or decision.get("ticker") or "")
            if subject:
                decision_cutoffs[subject] = min(decision_cutoffs.get(subject, cutoff), cutoff)
            all_decision_cutoffs.append(cutoff)
        if not all_decision_cutoffs:
            raise RuntimeError("future Evidence cannot be evaluated without a decision cutoff")
        default_cutoff = min(all_decision_cutoffs)
        evidence_ids: set[str] = set()
        for run_id in requested_run_ids:
            for row in self.store.connection.execute("SELECT dependency_ids_json FROM stage_results WHERE run_id=?", (run_id,)).fetchall():
                values = self._decode(row["dependency_ids_json"])
                if isinstance(values, list):
                    evidence_ids.update(str(value) for value in values)
        manifest: list[dict[str, Any]] = []
        for evidence_id in sorted(evidence_ids):
            row = self.store.connection.execute("SELECT * FROM evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
            if row is None:
                continue
            value = dict(row)
            try:
                evidence_time = OutcomeTracker._timestamp(value.get("observed_at"), label="Evidence observed_at")
            except OutcomeTracker.PITViolation as exc:
                raise RuntimeError("future Evidence has malformed observed_at") from exc
            decision_cutoff = decision_cutoffs.get(str(value.get("subject_id") or ""), default_cutoff)
            if evidence_time > decision_cutoff:
                raise RuntimeError("S0: future Evidence entered point-in-time Shadow decision")
            artifact = None
            if value.get("raw_artifact_id"):
                artifact_row = self.store.connection.execute("SELECT * FROM raw_artifacts WHERE artifact_id=?", (value["raw_artifact_id"],)).fetchone()
                artifact = dict(artifact_row) if artifact_row else None
            manifest.append({
                "evidence_id": evidence_id,
                "ticker": value.get("subject_id"),
                "claim_type": value.get("source_class"),
                "raw_artifact_id": value.get("raw_artifact_id"),
                "source_type": value.get("source_class"),
                "source_url_or_identifier": (self._decode(artifact.get("payload_json")) or {}).get("source_url") if artifact else None,
                "observed_at": value.get("observed_at"),
                "fetched_at": artifact.get("retrieved_at") if artifact else None,
                "content_hash": value.get("payload_hash"),
                "freshness_status": "ACTIVE" if value.get("status") == "ACTIVE" else value.get("status"),
                "lineage_valid": bool(artifact and artifact.get("payload_hash") == value.get("payload_hash")) or value.get("source_class") in {"DERIVED", "PYTHON"},
            })
        return manifest

    def _run_log(self, shadow_run_id: str, hunt_run_id: str, execution_run_id: str | None, health: dict[str, Any], status: str) -> dict[str, Any]:
        run_ids = [value for value in (hunt_run_id, execution_run_id) if value]
        workitems: dict[str, Any] = {}
        usage = {"requests": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "reasoning_effort": {}, "estimated_cost": None}
        for run_id in run_ids:
            workitems[run_id] = self.store.work_item_counts(run_id)
            for row in self.store.connection.execute("SELECT * FROM model_calls WHERE run_id=?", (run_id,)).fetchall():
                value = dict(row)
                usage["requests"] += 1
                usage["input_tokens"] += int(value.get("input_tokens") or 0)
                usage["cached_input_tokens"] += int(value.get("cached_tokens") or 0)
                usage["output_tokens"] += int(value.get("output_tokens") or 0)
                usage["reasoning_output_tokens"] += int(value.get("reasoning_output_tokens") or 0)
                usage["reasoning_effort"][str(value.get("reasoning_effort") or "UNKNOWN")] = usage["reasoning_effort"].get(str(value.get("reasoning_effort") or "UNKNOWN"), 0) + 1
        funnel_rows = self.store.list_funnel(hunt_run_id)
        funnel = {str(row["funnel_stage"]): int(row["count"]) for row in funnel_rows}
        raw_universe_row = next((row for row in funnel_rows if row.get("funnel_stage") == "RAW_UNIVERSE"), None)
        raw_details = self._decode((raw_universe_row or {}).get("details_json") or "{}")
        broad_discovery = bool(
            isinstance(raw_details, dict)
            and str(raw_details.get("provider") or "").lower() in {"composite-live-market", "nasdaq-screener"}
        ) or funnel.get("RAW_UNIVERSE", 0) > 100
        shadow = self.store.get_shadow_run(shadow_run_id)
        hunt_run = self.store.get_run(hunt_run_id)
        market_row = self.store.connection.execute(
            "SELECT result_json FROM stage_results WHERE run_id=? AND stage='MARKET_ANALYSIS' AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1",
            (hunt_run_id,),
        ).fetchone()
        market_context = self._decode(market_row["result_json"]) if market_row else None
        terminal_rows = self.store.connection.execute(
            "SELECT created_at FROM audit_log WHERE run_id IN ({}) ORDER BY created_at DESC LIMIT 1".format(
                ",".join("?" for _ in run_ids)
            ), tuple(run_ids),
        ).fetchone() if run_ids else None
        deterministic_finished_at = terminal_rows["created_at"] if terminal_rows else shadow["started_at"]
        # Keep report keys aligned with the persisted funnel ledger.  The
        # historical aliases (DISCOVERY_CANDIDATES/AUDIT) were never written
        # by Runtime and produced misleading zeroes.
        audited = int(funnel.get("STANDARD_AUDIT", 0)) + int(funnel.get("ADVERSARIAL_AUDIT", 0))
        stage_ready = int(funnel.get("STAGE_DISCOVERY_READY", 0))
        universe_not_evaluated = int(funnel.get("ADV_NOT_EVALUATED", 0))
        return {
            **self.metadata,
            "ruleset_hash": hunt_run.rule_set.rule_set_hash,
            "run_id": shadow_run_id,
            "shadow_version": self.shadow_version,
            "started_at": shadow["started_at"],
            "finished_at": deterministic_finished_at,
            "status": status,
            "authoritative_runs": {"hunt": hunt_run_id, "execution_review": execution_run_id},
            "hunt_contract": {
                "work_items": workitems.get(hunt_run_id, {}),
                "result": hunt_run.outcome,
                "broad_discovery": broad_discovery,
                "status": (
                    "PASS"
                    if hunt_run.outcome == "QUALIFIED_CANDIDATE_POOL" and sum(workitems.get(hunt_run_id, {}).values()) == 11
                    else "SUCCEEDED_NO_CANDIDATE"
                    if hunt_run.outcome == "NO_QUALIFIED_CANDIDATE" and broad_discovery
                    else "NO_OPPORTUNITY_BOUNDED"
                    if hunt_run.outcome == "NO_QUALIFIED_CANDIDATE"
                    else "FAILED"
                ),
            },
            "live_execution": (
                "TRIGGERED"
                if execution_run_id
                else "NOT_TRIGGERED_NO_QUALIFIED_CANDIDATE"
                if hunt_run.outcome == "NO_QUALIFIED_CANDIDATE"
                else "NOT_TRIGGERED"
            ),
            "providers": health,
            "market_context": {"status": health.get("market", {}).get("status", "UNKNOWN"), "analysis": market_context},
            "universe": {
                "raw": funnel.get("RAW_UNIVERSE", 0),
                "eligible": funnel.get("ADV_FILTER", 0),
                "adv_probed": funnel.get("ADV_PROBED", 0),
                "adv_not_evaluated": universe_not_evaluated,
                "discovered": stage_ready,
                "prescreen_passed": funnel.get("CAPITAL_PRESCREEN_PASS", 0),
                "deep_research": funnel.get("DEEP_RESEARCH", 0),
                "audited": audited,
                "qualified": funnel.get("QUALIFIED_CANDIDATE_POOL", 0),
                "not_evaluated": (
                    int(funnel.get("NOT_EVALUATED", 0))
                    + universe_not_evaluated
                    + int(funnel.get("CATALYST_NOT_EVALUATED", 0))
                    + int(funnel.get("RESEARCH_PROVIDER_FAILURE", 0))
                    + int(funnel.get("SEC_STALE_DATA", 0))
                    + int(funnel.get("SEC_PROVIDER_FAILURE", 0))
                ),
                "catalyst_not_evaluated": funnel.get("CATALYST_NOT_EVALUATED", 0),
                "research_provider_failures": funnel.get("RESEARCH_PROVIDER_FAILURE", 0),
                "sec_stale_data": funnel.get("SEC_STALE_DATA", 0),
                "sec_provider_failures": funnel.get("SEC_PROVIDER_FAILURE", 0),
            },
            "workitems": workitems,
            "llm_usage": usage,
            "errors": shadow["error"],
            "warnings": shadow["warning"],
            "broker_write_count": 0,
        }

    @staticmethod
    def _report(log: dict[str, Any], decisions: list[dict[str, Any]]) -> str:
        qualified = [row for row in decisions if row.get("qualified")]
        watch = [row for row in decisions if row.get("watch")]
        rejected = [row for row in decisions if row.get("rejected")]
        not_evaluated = [row for row in decisions if row.get("not_evaluated")]
        final_actions = [row.get("final_allocation_action") for row in decisions if row.get("final_allocation_action")]
        providers = log.get("providers") or {}
        lines = [
            "# Daily Stock Agent Report", "",
            f"Date: {str(log.get('started_at'))[:10]}",
            f"Run ID: {log.get('run_id')}",
            f"Shadow Version: {log.get('shadow_version')}",
            f"Git SHA: {log.get('code_git_sha')}", "",
            f"Git Diff Hash: `{log.get('git_diff_hash', 'UNKNOWN')}`",
            f"Source Tree Hash: `{log.get('source_tree_hash', 'UNKNOWN')}`",
            f"Git Dirty: `{log.get('git_dirty', 'UNKNOWN')}`", "",
            "## 1. Runtime Health", "",
        ]
        for name in ("market", "sec", "research", "luna", "portfolio", "evidence", "gate_integrity"):
            lines.append(f"- {name}: {(providers.get(name) or {}).get('status', 'UNKNOWN')}")
        lines.extend(["", "## 2. Market Regime", "", "```json", json.dumps((log.get("market_context") or {}).get("analysis"), ensure_ascii=False, sort_keys=True, indent=2), "```", "", "## 3. Sector Ranking", "", "SQLite Sector StageResult를 기준으로 기록했습니다.", "", "## 4. Discovery Funnel", ""])
        for key, value in (log.get("universe") or {}).items():
            lines.append(f"- {key}: {value}")
        lines.extend(["", "## 5. Qualified Candidates", ""])
        lines.extend([f"- {row['ticker']}: {row.get('decision')} / evidence={len(row.get('evidence_ids') or [])}" for row in qualified] or ["- NONE"])
        lines.extend(["", "## 6. Watch Candidates", ""])
        lines.extend([f"- {row['ticker']}: {row.get('rejection_reason') or 'insufficient execution-grade evidence'}" for row in watch] or ["- NONE"])
        lines.extend(["", "## 7. Not Evaluated Candidates", ""])
        lines.extend([f"- {row['ticker']}: {row.get('not_evaluated_stage')} / {row.get('not_evaluated_reason')}" for row in not_evaluated] or ["- NONE"])
        lines.extend(["", "## 8. Important Rejected Candidates", ""])
        lines.extend([f"- {row['ticker']}: {row.get('rejected_stage')} / {row.get('rejection_reason')}" for row in rejected] or ["- NONE"])
        lines.extend(["", "## 9. Existing Portfolio Review", "", f"- status: {(providers.get('portfolio') or {}).get('status', 'UNKNOWN')}", "", "## 10. FinalAllocation", ""])
        lines.append(f"- {', '.join(str(value) for value in final_actions) if final_actions else 'NO_TRADE'}")
        lines.extend(["", "## 11. Today's Conclusion", "", f"- {final_actions[0] if final_actions else 'NO_TRADE'}", "- ORDER_EXECUTED = NO", ""])
        return "\n".join(lines)

    def _write_artifacts(self, shadow_run_id: str, log: dict[str, Any], decisions: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, str]:
        day = str(log["started_at"])[:10]
        base = Path(day) / shadow_run_id
        incidents = [json.loads(row["incident_json"]) for row in self.store.connection.execute(
            "SELECT incident_json FROM shadow_incidents WHERE shadow_run_id=? ORDER BY created_at", (shadow_run_id,),
        ).fetchall()]
        report_text = self._report(log, decisions)
        validate_report_provenance(log, report_text)
        contents = {
            "DAILY_REPORT": (base / "DAILY_REPORT.md", report_text),
            "RUN_LOG": (base / "RUN_LOG.json", json.dumps(log, ensure_ascii=False, sort_keys=True, indent=2) + "\n"),
            "DECISIONS": (base / "DECISIONS.jsonl", "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in decisions)),
            "INCIDENTS": (base / "INCIDENTS.jsonl", "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in incidents)),
            "EVIDENCE_MANIFEST": (base / "EVIDENCE_MANIFEST.jsonl", "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in evidence)),
        }
        paths: dict[str, str] = {}
        for artifact_type, (relative, content) in contents.items():
            target = self.vault.write_text(relative, content)
            self.store.record_shadow_artifact(shadow_run_id, artifact_type, relative.as_posix(), content_digest(content))
            paths[artifact_type] = str(target)
        return paths

    def run(self, data: dict[str, Any], *, run_date: str | None = None, resume_run_id: str | None = None, stop_after_hunt: bool = False) -> ShadowRunResult:
        date = run_date or datetime.now(timezone.utc).date().isoformat()
        shadow = self.store.reserve_shadow_run(date, self.shadow_version, self.metadata, resume_run_id=resume_run_id)
        shadow_run_id = shadow["shadow_run_id"]
        hunt_run_id = shadow.get("hunt_run_id")
        execution_run_id = shadow.get("execution_run_id")
        if shadow.get("status") == "SUCCEEDED" and shadow.get("checkpoint") == "COMPLETE":
            artifacts = {
                str(row["artifact_type"]): str(self.vault.path(str(row["relative_path"])))
                for row in self.store.connection.execute(
                    "SELECT artifact_type,relative_path FROM shadow_artifacts WHERE shadow_run_id=?",
                    (shadow_run_id,),
                ).fetchall()
            }
            return ShadowRunResult(shadow_run_id, "SUCCEEDED", hunt_run_id, execution_run_id, artifacts, 0)
        errors: list[dict[str, Any]] = []
        health: dict[str, Any] = {}
        try:
            persisted_health = json.loads(shadow.get("health_json") or "{}")
            if persisted_health:
                health.update(persisted_health)
            elif self.provider_health:
                health["luna"] = self.provider_health()
            else:
                health["luna"] = {"status": "NOT_CONFIGURED"}
            if health["luna"].get("status") != "PASS":
                raise RuntimeError("Luna provider health contract did not pass")
            for name in ("market", "sec", "research", "portfolio", "evidence", "gate_integrity"):
                health.setdefault(name, {"status": "PENDING"})
            self.store.update_shadow_run(shadow_run_id, checkpoint="HEALTH_DONE", health=health, broker_write_count=0)
            if not hunt_run_id:
                hunt = self.agent.run(RunMode.HUNT_ONLY, dict(data))
                hunt_run_id = hunt.run_id
                hunt_actions = self.store.connection.execute("SELECT COUNT(*) AS n FROM final_actions WHERE run_id=?", (hunt_run_id,)).fetchone()["n"]
                if int(hunt_actions) != 0:
                    raise RuntimeError("S0: HUNT_ONLY produced a FinalAction")
                self.store.update_shadow_run(shadow_run_id, checkpoint="HUNT_DONE", hunt_run_id=hunt_run_id, broker_write_count=0)
                if hunt.outcome.startswith("BLOCKED"):
                    errors.append({"component": "HUNT", "error": hunt.blocked_reason or hunt.outcome})
                elif hunt.outcome == "QUALIFIED_CANDIDATE_POOL":
                    hunt_count = sum(self.store.work_item_counts(hunt_run_id).values())
                    if hunt_count != 11:
                        raise RuntimeError(f"S0: canonical HUNT WorkItem count changed: {hunt_count}")
            # Candidate-level research failures are deliberately recorded by
            # the strict runtime instead of aborting a broad universe run.
            # Surface them as a degraded data/provider condition so a partial
            # run is never reported as a clean opportunity result.
            research_failure_row = self.store.connection.execute(
                "SELECT count, details_json FROM discovery_funnel WHERE run_id=? AND funnel_stage='RESEARCH_PROVIDER_FAILURE' ORDER BY rowid DESC LIMIT 1",
                (hunt_run_id,),
            ).fetchone()
            if research_failure_row and int(research_failure_row["count"] or 0) > 0:
                details = self._decode(research_failure_row["details_json"] or "{}")
                errors.append({
                    "component": "RESEARCH",
                    "error": "candidate-level provider failures",
                    "count": int(research_failure_row["count"]),
                    "details": details if isinstance(details, dict) else {},
                })
            sec_failure_row = self.store.connection.execute(
                "SELECT count, details_json FROM discovery_funnel WHERE run_id=? AND funnel_stage='SEC_PROVIDER_FAILURE' ORDER BY rowid DESC LIMIT 1",
                (hunt_run_id,),
            ).fetchone()
            if sec_failure_row and int(sec_failure_row["count"] or 0) > 0:
                details = self._decode(sec_failure_row["details_json"] or "{}")
                errors.append({
                    "component": "SEC",
                    "error": "candidate-level SEC provider failures",
                    "count": int(sec_failure_row["count"]),
                    "details": details if isinstance(details, dict) else {},
                })
            if stop_after_hunt:
                raise InterruptedError("test checkpoint after HUNT")
            hunt_state = self.store.get_run(hunt_run_id)
            should_execute = hunt_state.outcome == "QUALIFIED_CANDIDATE_POOL"
            if should_execute and not execution_run_id:
                execution = self.agent.run(RunMode.HUNT_AND_EXECUTION_REVIEW, dict(data))
                execution_run_id = execution.run_id
                self.store.update_shadow_run(shadow_run_id, checkpoint="EXECUTION_DONE", execution_run_id=execution_run_id, broker_write_count=0)
                if execution.outcome.startswith("BLOCKED"):
                    errors.append({"component": "EXECUTION_REVIEW", "error": execution.blocked_reason or execution.outcome})
                else:
                    execution_count = sum(self.store.work_item_counts(execution_run_id).values())
                    if execution_count != 18:
                        raise RuntimeError(f"S0: canonical Execution Review WorkItem count changed: {execution_count}")
            # Reflect the stages that actually ran in the human report.  A
            # HUNT-only run has no portfolio snapshot, so portfolio remains
            # PENDING; successful market/evidence/provider checks must not be
            # mislabeled PENDING merely because a different candidate later
            # failed a SEC freshness fence.
            component_errors = {str(error.get("component") or "").upper() for error in errors}
            if hunt_run_id and "HUNT" not in component_errors:
                health["market"] = {**dict(health.get("market") or {}), "status": "PASS"}
                health["evidence"] = {**dict(health.get("evidence") or {}), "status": "PASS"}
                health["gate_integrity"] = {**dict(health.get("gate_integrity") or {}), "status": "PASS"}
            if "SEC" not in component_errors:
                health["sec"] = {**dict(health.get("sec") or {}), "status": "PASS"}
            if "RESEARCH" not in component_errors:
                health["research"] = {**dict(health.get("research") or {}), "status": "PASS"}
            if execution_run_id and "EXECUTION_REVIEW" not in component_errors:
                health["portfolio"] = {**dict(health.get("portfolio") or {}), "status": "PASS"}
            decisions = self._decisions(shadow_run_id, hunt_run_id, execution_run_id)
            if errors:
                affected_components: set[str] = set()
                for error in errors:
                    component = str(error.get("component") or "").upper()
                    if component in {"SEC", "RESEARCH", "MARKET", "PORTFOLIO", "EVIDENCE"}:
                        affected_components.add(component.lower())
                    elif component == "EXECUTION_REVIEW":
                        affected_components.update({"portfolio", "gate_integrity"})
                    elif component == "HUNT":
                        affected_components.add("gate_integrity")
                for name in affected_components:
                    current = dict(health.get(name) or {})
                    current["status"] = "DEGRADED"
                    health[name] = current
            status = "DEGRADED" if errors else "SUCCEEDED"
            self.store.update_shadow_run(shadow_run_id, status=status, checkpoint="LOGGING", errors=errors, health=health, broker_write_count=0)
            log = self._run_log(shadow_run_id, hunt_run_id, execution_run_id, health, status)
            evidence = self._evidence_manifest([hunt_run_id, execution_run_id])
            paths = self._write_artifacts(shadow_run_id, log, decisions, evidence)
            self.store.update_shadow_run(shadow_run_id, status=status, checkpoint="COMPLETE", errors=errors, broker_write_count=0, finished=True)
            return ShadowRunResult(shadow_run_id, status, hunt_run_id, execution_run_id, paths, 0)
        except InterruptedError:
            self.store.update_shadow_run(shadow_run_id, status="DEGRADED", checkpoint="HUNT_DONE", errors=[{"component": "ORCHESTRATOR", "error": "INTERRUPTED"}], broker_write_count=0, finished=True)
            raise
        except Exception as exc:
            incident = {
                "incident_id": f"incident-{canonical_hash([shadow_run_id, type(exc).__name__, str(exc)])[:24]}",
                "detected_at": utc_now(),
                "run_id": shadow_run_id,
                "shadow_version": self.shadow_version,
                "severity": "S0" if "S0:" in str(exc) else "S1",
                "component": "DAILY_SHADOW",
                "description": str(exc)[:500],
                "root_cause": None,
                "impact": "RUN_FAILED",
                "status": "OPEN",
                "fixed_in_version": None,
                "fixed_in_sha": None,
                "regression_test": None,
            }
            self.store.append_shadow_incident(shadow_run_id, incident)
            self.store.update_shadow_run(shadow_run_id, status="FAILED", errors=[{"component": "DAILY_SHADOW", "error": str(exc)[:500]}], broker_write_count=0, finished=True)
            raise


def persist_outcomes(store: Any, decision: dict[str, Any], bars: Iterable[dict[str, Any]], *, as_of: str) -> list[str]:
    """Persist append-only outcome snapshots and never modify the decision."""
    # Materialize and validate before the first append, preventing a future
    # bar from leaving a partially persisted outcome set behind.
    prepared, _ = OutcomeTracker._prepare_bars(decision, bars, as_of)
    outcome_ids: list[str] = []
    for horizon, payload in OutcomeTracker.calculate(decision, prepared, as_of=as_of).items():
        outcome_ids.append(store.append_shadow_outcome(str(decision["decision_id"]), horizon, as_of, payload))
    lifecycle = OutcomeTracker.shadow_lifecycle(decision, prepared, as_of=as_of)
    outcome_ids.append(store.append_shadow_outcome(str(decision["decision_id"]), "LIFECYCLE", as_of, lifecycle))
    return outcome_ids
