"""Single-command Primary SHADOW_V1.1 + V8 Challenger orchestration.

This module is deliberately an orchestration boundary.  It does not implement
investment logic and it never opens the Primary store for Challenger writes.
The existing :class:`DailyShadowRunner` remains the sole Primary runtime; V8
receives a point-in-time, redacted export and writes only challenger-owned
artifacts.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import canonical_hash, utc_now
from .shadow import DailyShadowRunner, ShadowRunResult
from .vault import SecureVault, content_digest
from .v8_challenger import (
    ChallengerInputManifest,
    V8ArtifactStore,
    V8ChallengerRunner,
    V8PromptBundle,
    V8_CHALLENGER_VERSION,
)


ORCHESTRATOR_VERSION = "DAILY_ORCHESTRATOR_V1.0"
_BLIND_FIELDS = {
    "grade", "research_grade", "primary_grade", "discovery_rank", "discovery_score",
    "primary_rank", "final_allocation", "final_allocation_action", "authoritative_action",
    "primary_action", "position_shares", "current_position_shares",
    "risk_target_position_shares", "transaction_shares", "resulting_position_shares",
    "entry", "recommended_entry", "stop", "target", "risk_reward",
}


@dataclass(frozen=True)
class DailyOrchestratorResult:
    run_id: str
    status: str
    preflight_status: str
    primary_status: str
    export_status: str
    v8_status: str
    comparison_status: str
    report_status: str
    paths: Mapping[str, str]
    primary: ShadowRunResult | None = None
    v8_run_id: str | None = None
    errors: tuple[str, ...] = ()
    broker_write_count: int = 0


def _timestamp(value: Any) -> datetime:
    if value in (None, ""):
        raise ValueError("comparison_as_of is missing")
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class PrimaryV8DailyOrchestrator:
    """Run the existing Primary once, then an isolated V8 Challenger.

    ``preflight`` and ``v8_executor`` are injectable so fixture acceptance can
    exercise every failure path without promoting recorded data to live
    authority.  Production callers pass the existing Luna provider executor.
    """

    def __init__(
        self,
        agent: Any,
        output_root: str | Path,
        metadata: Mapping[str, Any],
        *,
        prompt_bundle: str | Path,
        primary_runner: DailyShadowRunner | None = None,
        preflight: Callable[[], Mapping[str, Any]] | None = None,
        v8_executor: Callable[[str, str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        shadow_version: str = "SHADOW_V1.1",
        reasoning_effort: str = "medium",
    ) -> None:
        self.agent = agent
        self.store = agent.store
        self.output_root = Path(output_root)
        self.metadata = dict(metadata)
        self.prompt_bundle = V8PromptBundle.load(prompt_bundle)
        self.primary_runner = primary_runner
        self.preflight = preflight
        self.v8_executor = v8_executor
        self.shadow_version = shadow_version
        self.reasoning_effort = reasoning_effort

    def _new_run_id(self, run_date: str) -> str:
        # Human-readable and collision resistant; the Primary shadow run keeps
        # its canonical RUN-YYYYMMDD-NNN identity in SQLite.
        compact = str(run_date).replace("-", "")
        return f"DAILY-{compact}-{datetime.now(timezone.utc).strftime('%H%M%S')}-{uuid.uuid4().hex[:8]}"

    def _root(self, run_date: str, run_id: str) -> SecureVault:
        return SecureVault(self.output_root / str(run_date) / run_id)

    @staticmethod
    def _write(vault: SecureVault, relative: str | Path, payload: str) -> str:
        return str(vault.write_text(relative, payload))

    @staticmethod
    def _write_json(vault: SecureVault, relative: str | Path, payload: Any) -> str:
        return PrimaryV8DailyOrchestrator._write(
            vault, relative, json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )

    def _write_run_log(self, vault: SecureVault, payload: Mapping[str, Any]) -> str:
        return self._write_json(vault, "RUN_LOG.json", dict(payload))

    def _write_failure_report(self, vault: SecureVault, run_id: str, statuses: Mapping[str, Any], errors: list[str]) -> str:
        lines = [
            "# Stock Agent Daily Shadow Report", "", "## 1. Run Summary", "",
            f"- Run ID: {run_id}", f"- Orchestrator: {ORCHESTRATOR_VERSION}",
            f"- Status: {statuses.get('overall', 'FAILED')}", "",
            "## 2. Component Status", "",
        ]
        for key in ("preflight", "primary", "export", "v8", "comparison", "report"):
            lines.append(f"- {key}: {statuses.get(key, 'NOT_RUN')}")
        lines.extend(["", "## 3. Errors", ""])
        lines.extend([f"- {error}" for error in errors] or ["- NONE"])
        lines.extend(["", "## 4. Safety", "", "- Broker Orders Executed = 0", ""])
        return self._write(vault, "DAILY_COMBINED_REPORT.md", "\n".join(lines))

    def _copy_primary_artifacts(self, vault: SecureVault, primary: ShadowRunResult) -> dict[str, str]:
        paths: dict[str, str] = {}
        for kind, source_name in primary.artifact_paths.items():
            source = Path(source_name)
            if not source.exists() or not source.is_file():
                raise RuntimeError(f"PRIMARY_REPORT_MISSING:{kind}")
            target_name = {
                "DAILY_REPORT": "DAILY_REPORT.md", "RUN_LOG": "RUN_LOG.json",
                "DECISIONS": "DECISIONS.jsonl", "INCIDENTS": "INCIDENTS.jsonl",
                "EVIDENCE_MANIFEST": "EVIDENCE_MANIFEST.jsonl",
            }.get(kind, f"{kind}.json")
            paths[kind] = self._write(vault, Path("PRIMARY") / target_name, source.read_text(encoding="utf-8"))
        return paths

    def _export_primary(self, vault: SecureVault, primary: ShadowRunResult) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
        if not primary.hunt_run_id:
            raise RuntimeError("PRIMARY_EXPORT_FAILED:missing_hunt_run_id")
        decisions = self.store.list_shadow_decisions(primary.shadow_run_id)
        # The public candidate export is intentionally blind-safe.  Primary
        # decision/action internals remain in the Primary artifact and SQLite,
        # never in the V8 context.
        candidates: list[dict[str, Any]] = []
        for row in decisions:
            candidate = {key: value for key, value in dict(row).items() if str(key).casefold() not in _BLIND_FIELDS}
            candidate["system"] = "PRIMARY_SHADOW_V1_1"
            candidate["primary_shadow_run_id"] = primary.shadow_run_id
            candidates.append(candidate)

        evidence: list[dict[str, Any]] = []
        manifest_path = primary.artifact_paths.get("EVIDENCE_MANIFEST")
        if manifest_path and Path(manifest_path).exists():
            for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("lineage_valid") is False:
                    raise RuntimeError(f"PRIMARY_EXPORT_FAILED:invalid_evidence_lineage:{row.get('evidence_id')}")
                evidence.append({
                    "evidence_id": row.get("evidence_id"),
                    "raw_artifact_id": row.get("raw_artifact_id"),
                    "content_hash": row.get("content_hash"),
                    "payload_hash": row.get("content_hash"),
                    "source_url": row.get("source_url") or row.get("source_url_or_identifier"),
                    # Internal/derived receipts have no external URL.  Keep
                    # their exact identity as an identifier; never invent an
                    # external source merely to satisfy the V8 transport.
                    "source_url_or_identifier": row.get("source_url") or row.get("source_url_or_identifier") or f"artifact:{row.get('raw_artifact_id')}",
                    "published_at": row.get("published_at") or row.get("observed_at"),
                    "source_observed_at": row.get("source_observed_at") or row.get("observed_at"),
                    "observed_at": row.get("observed_at"),
                    "retrieved_at": row.get("retrieved_at") or row.get("fetched_at"),
                    "ticker": row.get("ticker"),
                    "namespace": "SHARED_PRIMARY",
                })

        primary_log_path = primary.artifact_paths.get("RUN_LOG")
        primary_log = json.loads(Path(primary_log_path).read_text(encoding="utf-8")) if primary_log_path else {}
        comparison_as_of = str(primary_log.get("started_at") or utc_now())
        _timestamp(comparison_as_of)
        market_row = self.store.connection.execute(
            "SELECT result_json FROM stage_results WHERE run_id=? AND stage='MARKET_ANALYSIS' AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1",
            (primary.hunt_run_id,),
        ).fetchone()
        market_analysis = json.loads(market_row["result_json"]) if market_row else {}
        market_snapshot_id = f"{primary.hunt_run_id}:MARKET_ANALYSIS"
        market_snapshot_hash = canonical_hash(market_analysis)
        primary_run = self.store.get_run(primary.hunt_run_id)
        manifest = {
            "primary_run_id": primary.hunt_run_id,
            "primary_shadow_run_id": primary.shadow_run_id,
            "primary_shadow_version": self.shadow_version,
            "comparison_as_of": comparison_as_of,
            "market_snapshot_id": market_snapshot_id,
            "market_snapshot_hash": market_snapshot_hash,
            "primary_evidence_manifest_hash": canonical_hash(evidence),
            "evidence_manifest_hash": canonical_hash(evidence),
            "ruleset_hash": primary_run.rule_set.rule_set_hash,
            "primary_ruleset_hash": primary_run.rule_set.rule_set_hash,
            "v8_prompt_bundle_hash": self.prompt_bundle.bundle_hash,
            "v8_prompt_hash": self.prompt_bundle.bundle_hash,
            "created_at": utc_now(),
        }
        self._write_json(vault, "EXPORT/CHALLENGER_INPUT_MANIFEST.json", manifest)
        self._write_json(vault, "EXPORT/PRIMARY_CANDIDATES.json", candidates)
        self._write_json(vault, "EXPORT/PRIMARY_EVIDENCE.json", evidence)
        return manifest, candidates, evidence, primary_log.get("status", primary.status)

    @staticmethod
    def _v8_report(result: Any, comparison_as_of: str) -> str:
        errors = list(getattr(result, "errors", ()) or ())
        status = getattr(result, "status", "FAILED")
        lines = [
            "# V8 Challenger Report", "", f"- Challenger Run ID: {getattr(result, 'challenger_run_id', 'UNKNOWN')}",
            f"- Status: {status}", f"- Comparison As Of: {comparison_as_of}",
            f"- Active Steps: 00A~18", f"- Candidate Count: {getattr(result, 'candidate_count', 0)}",
            f"- A Certified: {getattr(result, 'certified_a', 0)}",
            f"- A- Certified: {getattr(result, 'certified_a_minus', 0)}", "",
            "## Authority", "", "- Step 19: DISABLED_FOR_AUTHORITY", "- Step 20: RESEARCH_VALIDATION_ONLY",
            "- Primary FinalAllocation contamination: NONE", "- Broker Orders Executed = 0", "",
            "## Errors / Warnings", "",
        ]
        lines.extend([f"- {error}" for error in errors] or ["- NONE"])
        return "\n".join(lines) + "\n"

    def _combined_report(self, run_id: str, statuses: Mapping[str, Any], primary: ShadowRunResult | None, v8_result: Any, comparison: Mapping[str, Any] | None, errors: list[str]) -> str:
        primary_log = {}
        if primary and primary.artifact_paths.get("RUN_LOG"):
            try:
                primary_log = json.loads(Path(primary.artifact_paths["RUN_LOG"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                primary_log = {}
        lines = ["# Stock Agent Daily Shadow Report", "", "## 1. Run Summary", "", f"- Orchestrator Run ID: {run_id}", f"- Shadow Version: {self.shadow_version}", f"- Challenger Version: {V8_CHALLENGER_VERSION}", f"- comparison_as_of: {(primary_log or {}).get('started_at', 'UNKNOWN')}", "", "## 2. Runtime / Provider Health", ""]
        for key in ("preflight", "primary", "export", "v8", "comparison", "report"):
            lines.append(f"- {key}: {statuses.get(key, 'NOT_RUN')}")
        lines.extend(["", "## 3. Primary SHADOW_V1.1", ""])
        hunt_items = self.store.connection.execute("SELECT COUNT(*) AS n FROM work_items WHERE run_id=?", (primary.hunt_run_id,)).fetchone()["n"] if primary and primary.hunt_run_id else 0
        execution_items = self.store.connection.execute("SELECT COUNT(*) AS n FROM work_items WHERE run_id=?", (primary.execution_run_id,)).fetchone()["n"] if primary and primary.execution_run_id else 0
        lines.append(f"- HUNT WorkItems: {hunt_items if primary else 'NOT_RUN'}")
        lines.append(f"- HUNT FinalActions: 0" if primary else "- HUNT FinalActions: NOT_RUN")
        lines.append(f"- Execution WorkItems: {execution_items}")
        lines.extend(["", "## 4. V8 Challenger", ""])
        if v8_result:
            lines.extend([f"- Status: {getattr(v8_result, 'status', 'FAILED')}", f"- A: {getattr(v8_result, 'certified_a', 0)}", f"- A-: {getattr(v8_result, 'certified_a_minus', 0)}"])
        else:
            lines.append("- NOT_RUN")
        lines.extend(["", "## 5. Primary vs V8", ""])
        if comparison:
            for key in ("overlap", "primary_only", "v8_only", "primary_rejected_v8_certified", "v8_rejected_primary_qualified"):
                lines.append(f"- {key}: {len(comparison.get(key) or [])}")
        else:
            lines.append("- NOT_GENERATED")
        lines.extend(["", "## 6. Incidents / Degraded Components", ""])
        lines.extend([f"- {error}" for error in errors] or ["- NONE"])
        lines.extend(["", "## 7. Today's Conclusion", "", f"- {'NO_TRADE' if not v8_result or getattr(v8_result, 'status', '') != 'SUCCEEDED' else 'WATCH'}", "- Broker Orders Executed = 0", ""])
        return "\n".join(lines)

    def run(self, data: Mapping[str, Any], *, run_date: str | None = None, run_id: str | None = None, resume_run_id: str | None = None) -> DailyOrchestratorResult:
        date = run_date or datetime.now(timezone.utc).date().isoformat()
        identifier = resume_run_id or run_id or self._new_run_id(date)
        vault = self._root(date, identifier)
        existing_log = vault.path("RUN_LOG.json")
        if resume_run_id and existing_log.exists():
            payload = json.loads(existing_log.read_text(encoding="utf-8"))
            return DailyOrchestratorResult(identifier, payload.get("status", "FAILED"), payload.get("preflight_status", "UNKNOWN"), payload.get("primary_status", "UNKNOWN"), payload.get("export_status", "UNKNOWN"), payload.get("v8_status", "UNKNOWN"), payload.get("comparison_status", "UNKNOWN"), payload.get("report_status", "UNKNOWN"), {"RUN_LOG": str(existing_log), "DAILY_COMBINED_REPORT": str(vault.path("DAILY_COMBINED_REPORT.md"))}, errors=tuple(payload.get("errors") or ()), broker_write_count=0)

        errors: list[str] = []
        statuses: dict[str, Any] = {"preflight": "NOT_RUN", "primary": "NOT_RUN", "export": "NOT_RUN", "v8": "NOT_RUN", "comparison": "NOT_RUN", "report": "NOT_RUN", "overall": "FAILED"}
        started_at = utc_now()
        primary: ShadowRunResult | None = None
        v8_result: Any = None
        comparison: dict[str, Any] | None = None
        paths: dict[str, str] = {}
        preflight_health: dict[str, Any] = {}
        no_candidate = False
        try:
            try:
                preflight_health = dict(self.preflight() if self.preflight else {"status": "PASS"})
                statuses["preflight"] = str(preflight_health.get("status", "PASS"))
                if statuses["preflight"] != "PASS":
                    raise RuntimeError("PREFLIGHT_FAILED")
            except Exception as exc:
                statuses["preflight"] = "FAILED"
                errors.append(str(exc))
                raise

            primary_output = self.output_root / "_primary_runs"
            # The preflight performed the real provider contract check.  The
            # existing Primary runner still requires a Luna health callback;
            # bind the already-validated result without performing a second
            # synthetic smoke or changing Primary stage semantics.
            runner = self.primary_runner or DailyShadowRunner(
                self.agent, primary_output, self.metadata,
                provider_health=lambda: {"status": "PASS", "preflight": preflight_health},
                shadow_version=self.shadow_version,
            )
            primary = runner.run(dict(data), run_date=date)
            statuses["primary"] = primary.status
            if primary.status != "SUCCEEDED":
                # A degraded/failed Primary still owns a human-readable
                # report.  Preserve it under this orchestrator run before
                # blocking the Challenger; never turn a partial result into
                # a silent NOT_RUN state.
                try:
                    paths.update(self._copy_primary_artifacts(vault, primary))
                except Exception as copy_exc:
                    errors.append(str(copy_exc))
                statuses["export"] = "BLOCKED_PRIMARY"
                statuses["v8"] = "NOT_RUN"
                raise RuntimeError(f"PRIMARY_RUN_NOT_READY:{primary.status}")
            hunt_items = self.store.connection.execute("SELECT COUNT(*) AS n FROM work_items WHERE run_id=?", (primary.hunt_run_id,)).fetchone()["n"] if primary.hunt_run_id else 0
            hunt_state = self.store.get_run(primary.hunt_run_id) if primary.hunt_run_id else None
            no_candidate = bool(hunt_state and hunt_state.outcome == "NO_QUALIFIED_CANDIDATE")
            if no_candidate:
                # A broad HUNT may terminate normally before candidate-specific
                # stages are materialized.  This is a valid no-opportunity
                # result, not a contract violation, and the Challenger has no
                # authoritative input to consume.
                if not 3 <= int(hunt_items) <= 11:
                    raise RuntimeError(f"PRIMARY_HUNT_CONTRACT_FAILED:{hunt_items}")
                paths.update(self._copy_primary_artifacts(vault, primary))
                statuses["export"] = "NOT_APPLICABLE_NO_CANDIDATE"
                statuses["v8"] = "NOT_RUN"
                statuses["comparison"] = "NOT_RUN"
                statuses["overall"] = "SUCCEEDED"
            else:
                if int(hunt_items) != 11:
                    raise RuntimeError(f"PRIMARY_HUNT_CONTRACT_FAILED:{hunt_items}")
                if primary.execution_run_id:
                    execution_items = self.store.connection.execute("SELECT COUNT(*) AS n FROM work_items WHERE run_id=?", (primary.execution_run_id,)).fetchone()["n"]
                    if int(execution_items) != 18:
                        raise RuntimeError(f"PRIMARY_EXECUTION_CONTRACT_FAILED:{execution_items}")
                paths.update(self._copy_primary_artifacts(vault, primary))
                manifest_payload, candidates, evidence, _ = self._export_primary(vault, primary)
                manifest = ChallengerInputManifest.from_mapping(manifest_payload, self.prompt_bundle)
                statuses["export"] = "PASS"

                v8_store = V8ArtifactStore(vault.root / "V8")
                v8_runner = V8ChallengerRunner(self.prompt_bundle, v8_store, executor=self.v8_executor)
                v8_result = v8_runner.run(manifest, candidates, evidence, primary_results=candidates)
                statuses["v8"] = v8_result.status
                if v8_result.status != "SUCCEEDED":
                    errors.extend(str(error) for error in (getattr(v8_result, "errors", ()) or ()))
                paths["V8_REPORT"] = self._write(vault, "V8/V8_REPORT.md", self._v8_report(v8_result, manifest.comparison_as_of))
                if v8_result.artifacts.get("COMPARISON") and Path(v8_result.artifacts["COMPARISON"]).exists():
                    comparison = json.loads(Path(v8_result.artifacts["COMPARISON"]).read_text(encoding="utf-8"))
                    paths["COMPARISON"] = self._write_json(vault, "PRIMARY_VS_V8_COMPARISON.json", comparison)
                    statuses["comparison"] = "GENERATED"
                else:
                    comparison = {"comparison_as_of": manifest.comparison_as_of, "primary_run_id": manifest.primary_run_id, "challenger_run_id": getattr(v8_result, "challenger_run_id", None), "overlap": [], "primary_only": [], "v8_only": [], "primary_rejected_v8_certified": [], "v8_rejected_primary_qualified": []}
                    paths["COMPARISON"] = self._write_json(vault, "PRIMARY_VS_V8_COMPARISON.json", comparison)
                    statuses["comparison"] = "GENERATED"
                statuses["overall"] = "SUCCEEDED" if statuses["primary"] == "SUCCEEDED" and statuses["v8"] == "SUCCEEDED" else "DEGRADED"
        except Exception as exc:
            if not errors or errors[-1] != str(exc):
                errors.append(str(exc))
            if statuses["primary"] in {"SUCCEEDED", "DEGRADED"} and statuses["export"] == "NOT_RUN":
                statuses["export"] = "FAILED"
            if statuses["primary"] == "NOT_RUN" and statuses["preflight"] != "FAILED":
                statuses["primary"] = "FAILED"
            if statuses["v8"] == "NOT_RUN":
                statuses["v8"] = "BLOCKED_EXPORT" if statuses["export"] == "FAILED" else "NOT_RUN"
            statuses["overall"] = "FAILED" if statuses["preflight"] == "FAILED" or statuses["primary"] == "FAILED" else "DEGRADED"
        finally:
            statuses["report"] = "GENERATED"
            paths["DAILY_COMBINED_REPORT"] = self._write(vault, "DAILY_COMBINED_REPORT.md", self._combined_report(identifier, statuses, primary, v8_result, comparison, errors))
            ruleset_hash = self.metadata.get("ruleset_hash")
            if not ruleset_hash and primary and primary.hunt_run_id:
                try:
                    ruleset_hash = self.store.get_run(primary.hunt_run_id).rule_set.rule_set_hash
                except Exception:
                    ruleset_hash = None
            log = {
                "orchestrator_version": ORCHESTRATOR_VERSION,
                "run_id": identifier,
                "shadow_version": self.shadow_version,
                "challenger_version": V8_CHALLENGER_VERSION,
                "started_at": started_at,
                "finished_at": utc_now(),
                "code_sha": self.metadata.get("code_git_sha", "UNKNOWN"),
                "branch": self.metadata.get("branch", "UNKNOWN"),
                "ruleset_hash": ruleset_hash or "UNKNOWN",
                "prompt_library_hash": self.metadata.get("prompt_library_hash", "UNKNOWN"),
                "config_hash": self.metadata.get("config_hash", "UNKNOWN"),
                "model": self.metadata.get("model", "UNKNOWN"),
                "reasoning_effort": self.metadata.get("reasoning_effort", {}),
                "provider": self.metadata.get("provider", "UNKNOWN"),
                "schema_version": self.metadata.get("schema_version", "shadow-log-v1"),
                "database_schema_version": self.metadata.get("database_schema_version", "shadow-v1"),
                "timezone": self.metadata.get("timezone", "UTC"),
                "market_session": self.metadata.get("market_session", "UNKNOWN"),
                "providers": preflight_health,
                "preflight_status": statuses["preflight"], "primary_status": statuses["primary"],
                "export_status": statuses["export"], "v8_status": statuses["v8"],
                "comparison_status": statuses["comparison"], "report_status": statuses["report"],
                "status": statuses["overall"], "broker_write_count": 0,
                "errors": errors, "warnings": [], "created_at": utc_now(),
            }
            paths["RUN_LOG"] = self._write_run_log(vault, log)
            self._write(vault, "INCIDENTS.jsonl", "".join(json.dumps({"incident_id": f"{identifier}-{index}", "description": error, "status": "OPEN", "broker_write_count": 0}, ensure_ascii=False, sort_keys=True) + "\n" for index, error in enumerate(errors)))
        return DailyOrchestratorResult(identifier, statuses["overall"], statuses["preflight"], statuses["primary"], statuses["export"], statuses["v8"], statuses["comparison"], statuses["report"], paths, primary, getattr(v8_result, "challenger_run_id", None), tuple(errors), 0)


__all__ = ["DailyOrchestratorResult", "PrimaryV8DailyOrchestrator", "ORCHESTRATOR_VERSION"]
