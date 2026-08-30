"""Authoritative, run-bound report projection.

The report layer is intentionally read-only. It may render only data already
persisted for the requested run in SQLite; caller-supplied tickers, status,
actions, targets, URLs, or narrative are never authoritative inputs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReportContractError(ValueError):
    """Raised when a report cannot be proven to come from one run."""


class AuthoritativeHuntReportRenderer:
    """Render a deterministic Markdown projection of one SQLite run."""

    _POOL_STAGES = (
        "STAGE_GATE",
        "CAPITAL_PRESCREEN_GATE",
        "CATALYST_GATE",
        "EXPECTATION_GAP_GATE",
        "DEEP_RESEARCH",
        "FULL_SEC_FORENSIC",
        "ADVERSARIAL_AUDIT",
    )

    def __init__(self, store: Any) -> None:
        self.store = store

    def _run(self, run_id: str | None) -> Any:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ReportContractError("authoritative report requires run_id")
        try:
            run = self.store.get_run(run_id)
        except KeyError as exc:
            raise ReportContractError(f"unknown run_id: {run_id}") from exc
        if run.status == "RUNNING":
            raise ReportContractError("cannot render a non-terminal run")
        return run

    def _rows(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [dict(row) for row in self.store.connection.execute(sql, params).fetchall()]

    @staticmethod
    def _json(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, ValueError):
                return value
        return value

    def _candidate_projection(self, run_id: str, subject_id: str) -> dict[str, Any]:
        stage_rows = self._rows(
            "SELECT * FROM stage_results WHERE run_id=? AND subject_id=? ORDER BY created_at",
            (run_id, subject_id),
        )
        if not stage_rows:
            raise ReportContractError(f"UNBACKED_REPORT_FIELD: candidate {subject_id} has no StageResult")

        def result(stage: str) -> dict[str, Any] | None:
            for row in reversed(stage_rows):
                if row.get("stage") == stage and row.get("status") == "SUCCEEDED":
                    parsed = self._json(row.get("result_json"))
                    return parsed if isinstance(parsed, dict) else None
            return None

        evidence_ids: set[str] = set()
        provenance: list[dict[str, Any]] = []
        for row in stage_rows:
            ids = self._json(row.get("dependency_ids_json") or "[]")
            if isinstance(ids, list):
                evidence_ids.update(str(item) for item in ids)
            provenance.append(
                {
                    "source_table": "stage_results",
                    "row_id": row.get("result_id"),
                    "stage_name": row.get("stage"),
                    "result_id": row.get("result_id"),
                    "content_hash": row.get("dependency_hash"),
                    "authoritative": row.get("status") == "SUCCEEDED",
                }
            )

        evidence_rows: list[dict[str, Any]] = []
        if evidence_ids:
            marks = ",".join("?" for _ in evidence_ids)
            evidence_rows = self._rows(
                f"SELECT * FROM evidence WHERE evidence_id IN ({marks}) ORDER BY evidence_id",
                tuple(sorted(evidence_ids)),
            )
        evidence_by_id = {str(row["evidence_id"]): row for row in evidence_rows}
        missing_evidence = sorted(evidence_ids - set(evidence_by_id))
        if missing_evidence:
            raise ReportContractError(
                f"UNBACKED_REPORT_FIELD: candidate {subject_id} missing Evidence receipt(s): {missing_evidence}"
            )

        artifacts: list[dict[str, Any]] = []
        reverse_valuation: dict[str, Any] | None = None
        for evidence in evidence_rows:
            matches = self._rows(
                "SELECT artifact_id,provider,artifact_type,subject_id,payload_hash,source_observed_at,retrieved_at "
                "FROM raw_artifacts WHERE payload_hash=? AND (subject_id=? OR subject_id IS NULL) "
                "ORDER BY created_at DESC LIMIT 1",
                (evidence["payload_hash"], subject_id),
            )
            if matches:
                artifact = matches[0]
                artifacts.append(artifact)
                if artifact.get("artifact_type") in {"REVERSE_VALUATION", "EXPECTATION_GAP"}:
                    payload_row = self.store.connection.execute(
                        "SELECT payload_json FROM raw_artifacts WHERE artifact_id=?",
                        (artifact["artifact_id"],),
                    ).fetchone()
                    if payload_row:
                        parsed = self._json(payload_row["payload_json"])
                        if isinstance(parsed, dict):
                            reverse_valuation = parsed
                provenance.append(
                    {
                        "source_table": "raw_artifacts",
                        "row_id": artifact["artifact_id"],
                        "stage_name": "EVIDENCE",
                        "artifact_id": artifact["artifact_id"],
                        "evidence_id": evidence["evidence_id"],
                        "content_hash": artifact["payload_hash"],
                        "authoritative": evidence.get("status") == "ACTIVE",
                    }
                )
            elif evidence.get("source_class") not in {"DERIVED", "PYTHON"}:
                raise ReportContractError(
                    f"UNBACKED_REPORT_FIELD: evidence {evidence['evidence_id']} has no RawArtifact"
                )

        return {
            "security_id": subject_id,
            "stage": result("STAGE_GATE"),
            "capital": result("CAPITAL_PRESCREEN_GATE"),
            "catalyst_gate": result("CATALYST_GATE"),
            "expectation_gap_gate": result("EXPECTATION_GAP_GATE"),
            "research": result("DEEP_RESEARCH"),
            "full_sec": result("FULL_SEC_FORENSIC"),
            "audit": result("ADVERSARIAL_AUDIT"),
            "evidence_ids": sorted(evidence_ids),
            "artifact_ids": sorted(str(item["artifact_id"]) for item in artifacts),
            # Keep the historical key for downstream report consumers while
            # sourcing it from the new authoritative REVERSE_VALUATION artifact.
            "expectation_gap": reverse_valuation,
            "reverse_valuation": reverse_valuation,
            "provenance": provenance,
        }

    @staticmethod
    def _strict_projection_is_qualified(candidate: dict[str, Any]) -> tuple[bool, list[str]]:
        missing: list[str] = []
        stage = candidate.get("stage") or {}
        capital = candidate.get("capital") or {}
        catalyst = candidate.get("catalyst_gate") or {}
        expectation = candidate.get("expectation_gap_gate") or {}
        research = candidate.get("research") or {}
        full_sec = candidate.get("full_sec") or {}
        audit = candidate.get("audit") or {}
        if stage.get("decision") != "PASS":
            missing.append("STAGE_GATE_PASS")
        if capital.get("decision") not in {"PASS", "PASS_WITH_CONSTRAINTS"}:
            missing.append("CAPITAL_PRESCREEN_GATE_PASS")
        if catalyst.get("decision") != "PASS":
            missing.append("CATALYST_GATE_PASS")
        if expectation.get("decision") != "PASS":
            missing.append("EXPECTATION_GAP_GATE_PASS")
        if research.get("research_status") != "COMPLETE":
            missing.append("DEEP_RESEARCH_COMPLETE")
        if full_sec.get("status") != "COMPLETE":
            missing.append("FULL_SEC_COMPLETE")
        if audit.get("audit_recommendation") not in {"SUPPORTS_CONTINUATION", "SUPPORTS_WITH_CONDITIONS"}:
            missing.append("ADVERSARIAL_AUDIT_SUPPORT")
        if not isinstance(candidate.get("reverse_valuation"), dict):
            missing.append("REVERSE_VALUATION_RECEIPT")
        return not missing, missing

    def render(self, run_id: str | None, *, strict: bool = True) -> str:
        run = self._run(run_id)
        assert run_id is not None
        stage_rows = self._rows("SELECT * FROM stage_results WHERE run_id=? ORDER BY created_at", (run_id,))
        subjects = sorted(
            {
                str(row["subject_id"])
                for row in stage_rows
                if row.get("subject_id") and row.get("stage") == "STAGE_GATE"
            }
        )
        candidates: list[dict[str, Any]] = []
        for subject in subjects:
            try:
                qualified, _ = self.store.qualified_candidate_status(run_id, subject)
            except Exception as exc:  # pragma: no cover - defensive DB boundary
                raise ReportContractError(f"candidate qualification query failed: {exc}") from exc
            if not qualified:
                continue
            projection = self._candidate_projection(run_id, subject)
            if strict:
                projection_ok, missing = self._strict_projection_is_qualified(projection)
                if not projection_ok:
                    raise ReportContractError(
                        f"UNBACKED_REPORT_FIELD: candidate {subject} lacks authoritative HUNT prerequisites: {missing}"
                    )
            candidates.append(projection)

        outcome = str(run.outcome or "")
        if run.mode.value == "HUNT_ONLY" and outcome == "QUALIFIED_CANDIDATE_POOL" and not candidates:
            raise ReportContractError("run outcome claims a pool but no SQLite-qualified candidate exists")
        if strict and run.mode.value == "HUNT_ONLY" and outcome not in {
            "QUALIFIED_CANDIDATE_POOL",
            "NO_QUALIFIED_CANDIDATE",
            "BLOCKED_BY_CRITICAL_ISSUE",
            "BLOCKED_BY_EVIDENCE_GAP",
        }:
            raise ReportContractError(f"unsupported authoritative HUNT terminal outcome: {outcome}")

        funnel = self.store.list_funnel(run_id) if hasattr(self.store, "list_funnel") else []
        payload = {
            "run_id": run.run_id,
            "mode": run.mode.value,
            "terminal_status": run.status,
            "outcome": outcome,
            "rule_set_hash": run.rule_set.rule_set_hash,
            "context_manifest_hash": run.context_manifest_hash,
            "candidates": candidates,
            "funnel": funnel,
        }
        lines = [
            "# Authoritative Stock Agent HUNT Report",
            "",
            f"- run_id: `{run.run_id}`",
            f"- mode: `{run.mode.value}`",
            f"- terminal status: `{run.status}`",
            f"- authoritative outcome: `{outcome}`",
            f"- rule_set_hash: `{run.rule_set.rule_set_hash}`",
            f"- context_manifest_hash: `{run.context_manifest_hash}`",
            "",
            "## Candidate pool",
            "",
        ]
        if not candidates:
            lines.append("`NO_QUALIFIED_CANDIDATE` (SQLite-derived)")
        for index, candidate in enumerate(candidates, start=1):
            reverse = candidate.get("reverse_valuation")
            lines.extend(
                [
                    f"### {index}. `{candidate['security_id']}`",
                    "",
                    "- authoritative qualification: `true`",
                    f"- evidence receipts: `{', '.join(candidate['evidence_ids'])}`",
                    f"- raw artifacts: `{', '.join(candidate['artifact_ids']) or 'NONE'}`",
                    f"- reverse valuation / expectation gap: `{json.dumps(reverse, ensure_ascii=False, sort_keys=True) if reverse else 'UNKNOWN (no evidence-linked Python receipt)'}`",
                ]
            )
            lines.append("- StageResult/Gate provenance:")
            for receipt in candidate["provenance"]:
                lines.append("  - " + json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            lines.append("")
        lines.extend(["## Funnel ledger", ""])
        if funnel:
            for row in funnel:
                lines.append(f"- `{row['funnel_stage']}`: `{row['count']}`")
        else:
            lines.append("`UNBACKED_REPORT_FIELD`: no funnel ledger rows persisted")
        lines.extend(
            [
                "",
                "## Machine-readable projection",
                "",
                "```json",
                json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
                "```",
                "",
            ]
        )
        return "\n".join(lines)

    def write(self, run_id: str | None, output_path: str | Path, *, strict: bool = True) -> Path:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        content = self.render(run_id, strict=strict)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        return target

