from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Claim, ClaimEvidenceLink, EffectiveRuleSet, Evidence, Run, RunMode, WorkItem, WorkStatus, canonical_hash, utc_now


def _expiry(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SQLiteStore:
    """Durable WAL-backed state store and work queue."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self._initialize()

    def close(self) -> None:
        if getattr(self, "connection", None) is not None:
            try:
                self.connection.close()
            except sqlite3.ProgrammingError:
                pass
            self.connection = None

    def __enter__(self) -> "SQLiteStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - interpreter cleanup
        try:
            self.close()
        except Exception:
            pass

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def _initialize(self) -> None:
        with self.transaction() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, rule_set_json TEXT NOT NULL,
                    rule_set_hash TEXT NOT NULL, context_manifest_hash TEXT NOT NULL,
                    evidence_epoch INTEGER NOT NULL, status TEXT NOT NULL, outcome TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_items (
                    work_item_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                    stage TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0, lease_token TEXT, leased_by TEXT,
                    lease_until TEXT, dependency_hash TEXT NOT NULL, evidence_epoch INTEGER NOT NULL,
                    rule_set_hash TEXT NOT NULL, context_manifest_hash TEXT NOT NULL,
                    last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS work_items_ready ON work_items(status, lease_until, created_at);
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, source_class TEXT NOT NULL,
                    observed_at TEXT NOT NULL, epoch INTEGER NOT NULL, payload_hash TEXT NOT NULL,
                    grade TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE', raw_artifact_id TEXT
                );
                CREATE TABLE IF NOT EXISTS raw_artifacts (
                    artifact_id TEXT PRIMARY KEY, provider TEXT NOT NULL, artifact_type TEXT NOT NULL,
                    subject_id TEXT, observed_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, source_observed_at TEXT, retrieved_at TEXT, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS raw_artifacts_subject ON raw_artifacts(subject_id,artifact_type,observed_at);
                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, statement TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS claim_evidence (
                    claim_id TEXT NOT NULL REFERENCES claims(claim_id), evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
                    support_status TEXT NOT NULL, PRIMARY KEY(claim_id, evidence_id)
                );
                CREATE TABLE IF NOT EXISTS results (
                    result_id TEXT PRIMARY KEY, work_item_id TEXT NOT NULL REFERENCES work_items(work_item_id),
                    result_json TEXT NOT NULL, dependency_hash TEXT NOT NULL, evidence_epoch INTEGER NOT NULL,
                    rule_set_hash TEXT NOT NULL, context_manifest_hash TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS final_allocations (
                    allocation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
                    action TEXT NOT NULL, allocation_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, work_item_id TEXT,
                    event TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stage_results (
                    result_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, work_item_id TEXT,
                    stage TEXT NOT NULL, subject_id TEXT, result_json TEXT NOT NULL,
                    dependency_ids_json TEXT NOT NULL DEFAULT '[]', dependency_hash TEXT NOT NULL,
                    evidence_epoch INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS stage_results_lookup ON stage_results(run_id,stage,subject_id,status);
                CREATE TABLE IF NOT EXISTS execution_contexts (
                    run_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, context_json TEXT NOT NULL,
                    context_manifest_hash TEXT NOT NULL, dependency_hash TEXT NOT NULL,
                    dependency_ids_json TEXT NOT NULL DEFAULT '[]',
                    evidence_epoch INTEGER NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS debate_issues (
                    issue_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, subject_id TEXT NOT NULL,
                    severity TEXT NOT NULL, status TEXT NOT NULL, finding TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rule_overrides (
                    override_id TEXT PRIMARY KEY, version TEXT NOT NULL, content_hash TEXT NOT NULL,
                    rules_json TEXT NOT NULL, scope_json TEXT NOT NULL, effective_from TEXT NOT NULL,
                    effective_until TEXT, active INTEGER NOT NULL, authorized_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cost_reservations (
                    reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
                    prompt_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                    estimated_cost REAL NOT NULL, status TEXT NOT NULL, input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0, cached_tokens INTEGER DEFAULT 0, retry_count INTEGER DEFAULT 0,
                    latency_ms REAL DEFAULT 0, actual_cost REAL DEFAULT 0, created_at TEXT NOT NULL, settled_at TEXT,
                    reasoning_effort TEXT, wire_api TEXT, endpoint TEXT, billing_source TEXT, usage_source TEXT,
                    reasoning_output_tokens INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS model_calls (
                    call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, work_item_id TEXT NOT NULL,
                    prompt_id TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cached_tokens INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL, latency_ms REAL NOT NULL, finish_reason TEXT,
                    actual_cost REAL NOT NULL, created_at TEXT NOT NULL,
                    reasoning_effort TEXT, wire_api TEXT, endpoint TEXT, router_profile TEXT,
                    billing_source TEXT, usage_source TEXT, reasoning_output_tokens INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS knowledge_projections (
                    projection_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, target_path TEXT NOT NULL,
                    source_hash TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reference_registry (
                    reference_id TEXT NOT NULL, version TEXT NOT NULL, status TEXT NOT NULL,
                    content_hash TEXT NOT NULL, obsidian_path TEXT NOT NULL,
                    source_receipts_json TEXT NOT NULL, validated_at TEXT NOT NULL,
                    supersedes TEXT, kind TEXT NOT NULL, content TEXT NOT NULL,
                    PRIMARY KEY(reference_id, version)
                );
                CREATE TABLE IF NOT EXISTS final_actions (
                    action_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, subject_id TEXT NOT NULL,
                    action TEXT NOT NULL, action_scope TEXT NOT NULL, shares INTEGER NOT NULL,
                    capital_pct REAL NOT NULL, positive_commitment INTEGER NOT NULL,
                    dependency_hash TEXT NOT NULL, evidence_epoch INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discovery_funnel (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    funnel_stage TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, funnel_stage)
                );
                CREATE TABLE IF NOT EXISTS shadow_runs (
                    shadow_run_id TEXT PRIMARY KEY,
                    shadow_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    metadata_json TEXT NOT NULL,
                    checkpoint TEXT NOT NULL DEFAULT 'CREATED',
                    hunt_run_id TEXT,
                    execution_run_id TEXT,
                    error_json TEXT NOT NULL DEFAULT '[]',
                    warning_json TEXT NOT NULL DEFAULT '[]',
                    health_json TEXT NOT NULL DEFAULT '{}',
                    broker_write_count INTEGER NOT NULL DEFAULT 0,
                    original_shadow_run_id TEXT,
                    idempotency_key TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS shadow_decisions (
                    decision_id TEXT PRIMARY KEY,
                    shadow_run_id TEXT NOT NULL REFERENCES shadow_runs(shadow_run_id),
                    ticker TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    decision_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS shadow_decisions_run ON shadow_decisions(shadow_run_id,ticker);
                CREATE TABLE IF NOT EXISTS shadow_incidents (
                    incident_id TEXT PRIMARY KEY,
                    shadow_run_id TEXT NOT NULL REFERENCES shadow_runs(shadow_run_id),
                    severity TEXT NOT NULL,
                    component TEXT NOT NULL,
                    incident_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS shadow_outcomes (
                    outcome_id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL REFERENCES shadow_decisions(decision_id),
                    horizon TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    outcome_json TEXT NOT NULL,
                    outcome_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(decision_id,horizon,as_of)
                );
                CREATE TABLE IF NOT EXISTS shadow_artifacts (
                    shadow_run_id TEXT NOT NULL REFERENCES shadow_runs(shadow_run_id),
                    artifact_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(shadow_run_id,artifact_type)
                );
                """
            )
            # Lightweight migration for databases created by the earlier fixture runtime.
            for name, ddl in (
                ("dependency_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("prerequisite_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("max_attempts", "INTEGER NOT NULL DEFAULT 3"),
                ("priority", "INTEGER NOT NULL DEFAULT 100"),
                ("available_after", "TEXT"),
                ("idempotency_key", "TEXT"),
                ("parent_work_item_id", "TEXT"),
                ("generation", "INTEGER NOT NULL DEFAULT 0"),
            ):
                try:
                    db.execute(f"ALTER TABLE work_items ADD COLUMN {name} {ddl}")
                except sqlite3.OperationalError:
                    pass
            try:
                db.execute("ALTER TABLE evidence ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'")
            except sqlite3.OperationalError:
                pass
            try:
                db.execute("ALTER TABLE evidence ADD COLUMN raw_artifact_id TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                db.execute("ALTER TABLE execution_contexts ADD COLUMN dependency_ids_json TEXT NOT NULL DEFAULT '[]'")
            except sqlite3.OperationalError:
                pass
            for name, ddl in (("source_observed_at", "TEXT"), ("retrieved_at", "TEXT")):
                try:
                    db.execute(f"ALTER TABLE raw_artifacts ADD COLUMN {name} {ddl}")
                except sqlite3.OperationalError:
                    pass
            for table, name, ddl in (
                ("cost_reservations", "reasoning_effort", "TEXT"),
                ("cost_reservations", "wire_api", "TEXT"),
                ("cost_reservations", "endpoint", "TEXT"),
                ("model_calls", "reasoning_effort", "TEXT"),
                ("model_calls", "wire_api", "TEXT"),
                ("model_calls", "endpoint", "TEXT"),
                ("model_calls", "router_profile", "TEXT"),
                ("cost_reservations", "billing_source", "TEXT"),
                ("cost_reservations", "usage_source", "TEXT"),
                ("cost_reservations", "reasoning_output_tokens", "INTEGER DEFAULT 0"),
                ("model_calls", "billing_source", "TEXT"),
                ("model_calls", "usage_source", "TEXT"),
                ("model_calls", "reasoning_output_tokens", "INTEGER DEFAULT 0"),
            ):
                try:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                except sqlite3.OperationalError:
                    pass

    def _log(self, db: sqlite3.Connection, event: str, details: dict[str, Any], run_id: str | None = None, work_item_id: str | None = None) -> None:
        db.execute("INSERT INTO audit_log(run_id,work_item_id,event,details_json,created_at) VALUES(?,?,?,?,?)", (run_id, work_item_id, event, json.dumps(details, sort_keys=True), utc_now()))

    def reserve_shadow_run(
        self,
        run_date: str,
        shadow_version: str,
        metadata: dict[str, Any],
        *,
        resume_run_id: str | None = None,
        original_shadow_run_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically allocate or resume one operator-triggered Shadow run."""
        compact_date = str(run_date).replace("-", "")
        if len(compact_date) != 8 or not compact_date.isdigit():
            raise ValueError("shadow run_date must be YYYY-MM-DD")
        with self.transaction() as db:
            if resume_run_id:
                row = db.execute("SELECT * FROM shadow_runs WHERE shadow_run_id=?", (resume_run_id,)).fetchone()
                if row is None:
                    raise KeyError(resume_run_id)
                if str(row["shadow_version"]) != str(shadow_version):
                    raise ValueError("resume cannot change shadow version")
                if row["status"] == "SUCCEEDED":
                    return dict(row)
                db.execute("UPDATE shadow_runs SET status='RUNNING', finished_at=NULL WHERE shadow_run_id=?", (resume_run_id,))
                return dict(db.execute("SELECT * FROM shadow_runs WHERE shadow_run_id=?", (resume_run_id,)).fetchone())
            rows = db.execute(
                "SELECT shadow_run_id FROM shadow_runs WHERE shadow_run_id LIKE ? ORDER BY shadow_run_id",
                (f"RUN-{compact_date}-%",),
            ).fetchall()
            sequence = max([int(str(row["shadow_run_id"]).rsplit("-", 1)[-1]) for row in rows] or [0]) + 1
            shadow_run_id = f"RUN-{compact_date}-{sequence:03d}"
            idempotency_key = f"{shadow_run_id}:{shadow_version}"
            started = utc_now()
            db.execute(
                "INSERT INTO shadow_runs(shadow_run_id,shadow_version,status,started_at,metadata_json,original_shadow_run_id,idempotency_key) VALUES(?,?,?,?,?,?,?)",
                (shadow_run_id, shadow_version, "RUNNING", started, json.dumps(metadata, sort_keys=True), original_shadow_run_id, idempotency_key),
            )
            return dict(db.execute("SELECT * FROM shadow_runs WHERE shadow_run_id=?", (shadow_run_id,)).fetchone())

    def update_shadow_run(
        self,
        shadow_run_id: str,
        *,
        status: str | None = None,
        checkpoint: str | None = None,
        hunt_run_id: str | None = None,
        execution_run_id: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        health: dict[str, Any] | None = None,
        broker_write_count: int | None = None,
        finished: bool = False,
    ) -> None:
        allowed = {"RUNNING", "SUCCEEDED", "DEGRADED", "FAILED"}
        if status is not None and status not in allowed:
            raise ValueError("invalid shadow run status")
        if broker_write_count is not None and int(broker_write_count) != 0:
            raise ValueError("shadow runtime forbids broker writes")
        assignments: list[str] = []
        values: list[Any] = []
        for column, value in (
            ("status", status), ("checkpoint", checkpoint), ("hunt_run_id", hunt_run_id),
            ("execution_run_id", execution_run_id),
            ("error_json", json.dumps(errors, sort_keys=True) if errors is not None else None),
            ("warning_json", json.dumps(warnings, sort_keys=True) if warnings is not None else None),
            ("health_json", json.dumps(health, sort_keys=True) if health is not None else None),
            ("broker_write_count", int(broker_write_count) if broker_write_count is not None else None),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(value)
        if finished:
            assignments.append("finished_at=?")
            values.append(utc_now())
        if not assignments:
            return
        values.append(shadow_run_id)
        with self.transaction() as db:
            cursor = db.execute(f"UPDATE shadow_runs SET {','.join(assignments)} WHERE shadow_run_id=?", tuple(values))
            if cursor.rowcount != 1:
                raise KeyError(shadow_run_id)

    def get_shadow_run(self, shadow_run_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM shadow_runs WHERE shadow_run_id=?", (shadow_run_id,)).fetchone()
        if row is None:
            raise KeyError(shadow_run_id)
        value = dict(row)
        for key in ("metadata_json", "error_json", "warning_json", "health_json"):
            value[key[:-5] if key.endswith("_json") else key] = json.loads(value[key])
        return value

    def append_shadow_decision(self, shadow_run_id: str, decision: dict[str, Any]) -> str:
        payload = dict(decision)
        decision_id = str(payload.get("decision_id") or f"decision-{uuid.uuid4().hex}")
        payload["decision_id"] = decision_id
        payload_hash = canonical_hash(payload)
        ticker = str(payload.get("ticker") or payload.get("security_id") or "UNKNOWN")
        with self.transaction() as db:
            existing = db.execute("SELECT decision_hash FROM shadow_decisions WHERE decision_id=?", (decision_id,)).fetchone()
            if existing:
                if str(existing["decision_hash"]) != payload_hash:
                    raise ValueError("immutable shadow decision conflict")
                return decision_id
            db.execute(
                "INSERT INTO shadow_decisions VALUES(?,?,?,?,?,?)",
                (decision_id, shadow_run_id, ticker, json.dumps(payload, sort_keys=True), payload_hash, utc_now()),
            )
        return decision_id

    def list_shadow_decisions(self, shadow_run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT decision_json FROM shadow_decisions WHERE shadow_run_id=? ORDER BY created_at,decision_id",
            (shadow_run_id,),
        ).fetchall()
        return [json.loads(row["decision_json"]) for row in rows]

    def append_shadow_incident(self, shadow_run_id: str, incident: dict[str, Any]) -> str:
        payload = dict(incident)
        incident_id = str(payload.get("incident_id") or f"incident-{uuid.uuid4().hex}")
        payload["incident_id"] = incident_id
        with self.transaction() as db:
            existing = db.execute("SELECT incident_json FROM shadow_incidents WHERE incident_id=?", (incident_id,)).fetchone()
            encoded = json.dumps(payload, sort_keys=True)
            if existing:
                if str(existing["incident_json"]) != encoded:
                    raise ValueError("immutable shadow incident conflict")
                return incident_id
            db.execute(
                "INSERT INTO shadow_incidents VALUES(?,?,?,?,?,?)",
                (incident_id, shadow_run_id, str(payload.get("severity") or "S1"), str(payload.get("component") or "UNKNOWN"), encoded, utc_now()),
            )
        return incident_id

    def append_shadow_outcome(self, decision_id: str, horizon: str, as_of: str, outcome: dict[str, Any]) -> str:
        payload = dict(outcome)
        payload.update({"decision_id": decision_id, "horizon": horizon, "as_of": as_of})
        payload_hash = canonical_hash(payload)
        outcome_id = f"outcome-{canonical_hash([decision_id, horizon, as_of])[:24]}"
        with self.transaction() as db:
            existing = db.execute("SELECT outcome_hash FROM shadow_outcomes WHERE outcome_id=?", (outcome_id,)).fetchone()
            if existing:
                if str(existing["outcome_hash"]) != payload_hash:
                    raise ValueError("immutable point-in-time outcome conflict")
                return outcome_id
            db.execute(
                "INSERT INTO shadow_outcomes VALUES(?,?,?,?,?,?,?)",
                (outcome_id, decision_id, horizon, as_of, json.dumps(payload, sort_keys=True), payload_hash, utc_now()),
            )
        return outcome_id

    def record_shadow_artifact(self, shadow_run_id: str, artifact_type: str, relative_path: str, content_hash: str) -> None:
        with self.transaction() as db:
            existing = db.execute(
                "SELECT relative_path,content_hash FROM shadow_artifacts WHERE shadow_run_id=? AND artifact_type=?",
                (shadow_run_id, artifact_type),
            ).fetchone()
            if existing:
                if existing["relative_path"] != relative_path or existing["content_hash"] != content_hash:
                    raise ValueError("immutable shadow artifact conflict")
                return
            db.execute(
                "INSERT INTO shadow_artifacts VALUES(?,?,?,?,?)",
                (shadow_run_id, artifact_type, relative_path, content_hash, utc_now()),
            )

    def upsert_reference(self, record: Any) -> None:
        """Persist a validated reusable reference, separate from run evidence."""
        values = record.as_dict() if hasattr(record, "as_dict") else dict(record)
        with self.transaction() as db:
            db.execute(
                """INSERT INTO reference_registry(
                    reference_id,version,status,content_hash,obsidian_path,
                    source_receipts_json,validated_at,supersedes,kind,content
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(reference_id,version) DO UPDATE SET
                    status=excluded.status, content_hash=excluded.content_hash,
                    obsidian_path=excluded.obsidian_path,
                    source_receipts_json=excluded.source_receipts_json,
                    validated_at=excluded.validated_at, supersedes=excluded.supersedes,
                    kind=excluded.kind, content=excluded.content""",
                (
                    values["reference_id"], values["version"], values["status"],
                    values["content_hash"], values["obsidian_path"],
                    json.dumps(values.get("source_receipts", []), sort_keys=True),
                    values["validated_at"], values.get("supersedes"), values["kind"], values["content"],
                ),
            )

    def get_active_reference(self, reference_id: str, version: str | None = None) -> Any | None:
        """Return an ACTIVE ReferenceRecord without promoting it to Evidence."""
        query = "SELECT * FROM reference_registry WHERE reference_id=? AND status='ACTIVE'"
        params: list[Any] = [reference_id]
        if version is not None:
            query += " AND version=?"
            params.append(version)
        query += " ORDER BY version DESC LIMIT 1"
        row = self.connection.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        from .references import ReferenceRecord
        return ReferenceRecord(
            reference_id=row["reference_id"], version=row["version"], status=row["status"],
            content_hash=row["content_hash"], obsidian_path=row["obsidian_path"],
            source_receipts=tuple(json.loads(row["source_receipts_json"])),
            validated_at=row["validated_at"], supersedes=row["supersedes"],
            kind=row["kind"], content=row["content"],
        )

    def save_raw_artifact(self, artifact: Any) -> None:
        # Repository-owned integrity fence: callers/providers cannot persist a
        # payload under a forged hash or claim an observation after retrieval.
        # This is deliberately enforced at the SQLite boundary as well as in
        # individual adapters, because every downstream receipt depends on
        # the stored artifact identity.
        expected_hash = canonical_hash(getattr(artifact, "payload", None))
        if str(getattr(artifact, "payload_hash", "")) != expected_hash:
            raise ValueError("raw artifact payload_hash does not match payload")
        with self.transaction() as db:
            source_time = getattr(artifact, "source_observed_at", None)
            retrieved_time = getattr(artifact, "retrieved_at", None) or utc_now()
            if source_time:
                try:
                    source_dt = datetime.fromisoformat(str(source_time).replace("Z", "+00:00"))
                    if source_dt.tzinfo is None:
                        source_dt = source_dt.replace(tzinfo=timezone.utc)
                    retrieved_dt = datetime.fromisoformat(str(retrieved_time).replace("Z", "+00:00"))
                    if retrieved_dt.tzinfo is None:
                        retrieved_dt = retrieved_dt.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError) as exc:
                    raise ValueError("raw artifact timestamp is invalid") from exc
                if source_dt > retrieved_dt + timedelta(minutes=5):
                    raise ValueError("raw artifact source_observed_at is after retrieved_at")
                if source_dt > datetime.now(timezone.utc) + timedelta(minutes=5):
                    raise ValueError("raw artifact source_observed_at is in the future")
            db.execute("INSERT OR REPLACE INTO raw_artifacts(artifact_id,provider,artifact_type,subject_id,observed_at,payload_json,payload_hash,source_observed_at,retrieved_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (artifact.artifact_id, artifact.provider, artifact.artifact_type, artifact.subject_id, artifact.observed_at, json.dumps(artifact.payload, sort_keys=True), artifact.payload_hash, source_time, retrieved_time, utc_now()))

    def record_model_call(self, run_id: str, work_item_id: str, prompt_id: str, telemetry: dict[str, Any], retry_count: int = 0) -> str:
        call_id = f"call-{uuid.uuid4().hex}"
        with self.transaction() as db:
            db.execute("INSERT INTO model_calls(call_id,run_id,work_item_id,prompt_id,provider,model,input_tokens,output_tokens,cached_tokens,retry_count,latency_ms,finish_reason,actual_cost,created_at,reasoning_effort,wire_api,endpoint,router_profile,billing_source,usage_source,reasoning_output_tokens) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (call_id, run_id, work_item_id, prompt_id, telemetry.get("provider", "unknown"), telemetry.get("model", "unknown"), int(telemetry.get("input_tokens", 0)), int(telemetry.get("output_tokens", 0)), int(telemetry.get("cached_tokens", 0)), int(retry_count), float(telemetry.get("latency_ms", 0)), telemetry.get("finish_reason"), float(telemetry.get("actual_cost", telemetry.get("estimated_cost", 0))), utc_now(), telemetry.get("reasoning_effort"), telemetry.get("wire_api"), telemetry.get("endpoint"), telemetry.get("router_profile"), telemetry.get("billing_source"), telemetry.get("usage_source"), int(telemetry.get("reasoning_output_tokens", 0))))
        return call_id

    def create_run(self, mode: RunMode, rule_set: EffectiveRuleSet, context_manifest_hash: str, evidence_epoch: int) -> Run:
        run_id = f"run-{uuid.uuid4().hex}"
        with self.transaction() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)", (run_id, mode.value, json.dumps(rule_set.__dict__, sort_keys=True), rule_set.rule_set_hash, context_manifest_hash, evidence_epoch, "RUNNING", None, utc_now()))
            self._log(db, "RUN_CREATED", {"mode": mode.value, "evidence_epoch": evidence_epoch}, run_id=run_id)
        return Run(run_id, mode, rule_set, context_manifest_hash, evidence_epoch)

    def get_run(self, run_id: str) -> Run:
        row = self.connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        values = json.loads(row["rule_set_json"])
        return Run(run_id, RunMode(row["mode"]), EffectiveRuleSet(**values), row["context_manifest_hash"], row["evidence_epoch"], row["status"], row["outcome"], row["created_at"])

    def finish_run(self, run_id: str, outcome: str) -> None:
        with self.transaction() as db:
            terminal_status = "FAILED" if str(outcome).startswith("BLOCKED") else "SUCCEEDED"
            db.execute("UPDATE runs SET status=?, outcome=? WHERE run_id=? AND status='RUNNING'", (terminal_status, outcome, run_id))
            db.execute("UPDATE work_items SET status='CANCELLED', lease_token=NULL, leased_by=NULL, lease_until=NULL, last_error='CANCELLED_BY_TERMINAL_RUN', updated_at=? WHERE run_id=? AND status IN ('QUEUED','LEASED')", (utc_now(), run_id))
            self._log(db, "RUN_FINISHED", {"outcome": outcome}, run_id=run_id)

    def enqueue(self, run: Run, stage: str, payload: dict[str, Any], dependency_hash: str) -> WorkItem:
        item = WorkItem(f"work-{uuid.uuid4().hex}", run.run_id, stage, payload, dependency_hash=dependency_hash, evidence_epoch=run.evidence_epoch, rule_set_hash=run.rule_set.rule_set_hash, context_manifest_hash=run.context_manifest_hash)
        with self.transaction() as db:
            db.execute("INSERT INTO work_items(work_item_id,run_id,stage,payload_json,status,attempt,lease_token,leased_by,lease_until,dependency_hash,evidence_epoch,rule_set_hash,context_manifest_hash,last_error,created_at,updated_at,dependency_ids_json,prerequisite_json,max_attempts,priority,available_after,idempotency_key,parent_work_item_id,generation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (item.work_item_id, item.run_id, item.stage, json.dumps(payload, sort_keys=True), item.status.value, 0, None, None, None, item.dependency_hash, item.evidence_epoch, item.rule_set_hash, item.context_manifest_hash, None, utc_now(), utc_now(), json.dumps(payload.get("dependency_ids", [])), json.dumps(payload.get("prerequisites", {})), int(payload.get("max_attempts", 3)), int(payload.get("priority", 100)), payload.get("available_after"), payload.get("idempotency_key"), payload.get("parent_work_item_id"), int(payload.get("generation", 0))))
            self._log(db, "WORK_ENQUEUED", {"stage": stage}, run_id=run.run_id, work_item_id=item.work_item_id)
        return item

    def upsert_evidence(self, evidence: Evidence) -> int:
        """Insert or refresh evidence and return the current monotonic epoch."""
        with self.transaction() as db:
            if evidence.raw_artifact_id:
                artifact_row = db.execute(
                    "SELECT payload_hash FROM raw_artifacts WHERE artifact_id=?",
                    (evidence.raw_artifact_id,),
                ).fetchone()
                if artifact_row is None:
                    raise ValueError("evidence raw_artifact_id does not reference a persisted RawArtifact")
                if str(artifact_row["payload_hash"]) != str(evidence.payload_hash):
                    raise ValueError("evidence payload_hash does not match its RawArtifact")
            row = db.execute("SELECT MAX(epoch) AS epoch FROM evidence").fetchone()
            current_epoch = int(row["epoch"] or 0)
            existing = db.execute("SELECT payload_hash,epoch FROM evidence WHERE evidence_id=?", (evidence.evidence_id,)).fetchone()
            epoch = existing["epoch"] if existing and existing["payload_hash"] == evidence.payload_hash else current_epoch + 1
            db.execute("INSERT INTO evidence(evidence_id,subject_id,source_class,observed_at,epoch,payload_hash,grade,status,raw_artifact_id) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(evidence_id) DO UPDATE SET subject_id=excluded.subject_id,source_class=excluded.source_class,observed_at=excluded.observed_at,epoch=excluded.epoch,payload_hash=excluded.payload_hash,grade=excluded.grade,status=excluded.status,raw_artifact_id=excluded.raw_artifact_id", (evidence.evidence_id, evidence.subject_id, evidence.source_class, evidence.observed_at, epoch, evidence.payload_hash, evidence.grade, evidence.status, evidence.raw_artifact_id))
            if existing and existing["payload_hash"] != evidence.payload_hash:
                # Evidence refresh invalidates the dependent graph, not just
                # the global epoch.  Parse dependency JSON so an ID cannot
                # accidentally invalidate a similarly prefixed evidence ID.
                dependent_hashes: set[str] = set()
                for stage_row in db.execute("SELECT result_id,dependency_ids_json,dependency_hash FROM stage_results WHERE status='SUCCEEDED'").fetchall():
                    if evidence.evidence_id in json.loads(stage_row["dependency_ids_json"] or "[]"):
                        db.execute("UPDATE stage_results SET status='INVALIDATED' WHERE result_id=?", (stage_row["result_id"],)); dependent_hashes.add(stage_row["dependency_hash"])
                for result_row in db.execute("SELECT result_id,work_item_id,dependency_hash,status FROM results WHERE status='SUCCEEDED'").fetchall():
                    if result_row["dependency_hash"] in dependent_hashes:
                        db.execute("UPDATE results SET status='INVALIDATED' WHERE result_id=?", (result_row["result_id"],))
                db.execute("DELETE FROM final_actions WHERE dependency_hash IN (SELECT dependency_hash FROM stage_results WHERE status='INVALIDATED')")
                db.execute("UPDATE execution_contexts SET dependency_hash='STALE_ON_ARRIVAL:' || dependency_hash WHERE dependency_hash IN (SELECT dependency_hash FROM stage_results WHERE status='INVALIDATED')")
                self._log(db, "DEPENDENTS_INVALIDATED", {"evidence_id": evidence.evidence_id, "new_epoch": epoch})
            self._log(db, "EVIDENCE_UPSERTED", {"evidence_id": evidence.evidence_id, "epoch": epoch})
            return epoch

    def current_evidence_epoch(self) -> int:
        row = self.connection.execute("SELECT MAX(epoch) AS epoch FROM evidence").fetchone()
        return int(row["epoch"] or 0)

    def current_evidence_epoch_for(self, dependency_ids: list[str]) -> int:
        if not dependency_ids:
            return self.current_evidence_epoch()
        marks = ",".join("?" for _ in dependency_ids)
        row = self.connection.execute(f"SELECT MAX(epoch) AS epoch FROM evidence WHERE evidence_id IN ({marks})", dependency_ids).fetchone()
        return int(row["epoch"] or 0)

    def save_claim(self, claim: Claim) -> None:
        with self.transaction() as db:
            db.execute("INSERT INTO claims VALUES(?,?,?,?) ON CONFLICT(claim_id) DO UPDATE SET subject_id=excluded.subject_id,statement=excluded.statement,status=excluded.status", (claim.claim_id, claim.subject_id, claim.statement, claim.status))

    def link_claim_evidence(self, link: ClaimEvidenceLink) -> None:
        with self.transaction() as db:
            db.execute("INSERT INTO claim_evidence VALUES(?,?,?) ON CONFLICT(claim_id,evidence_id) DO UPDATE SET support_status=excluded.support_status", (link.claim_id, link.evidence_id, link.support_status))

    def dependency_hash(self, dependency_ids: list[str], rule_set_hash: str, context_manifest_hash: str) -> str:
        if dependency_ids:
            placeholders = ",".join("?" for _ in dependency_ids)
            rows = self.connection.execute(f"SELECT evidence_id,payload_hash,epoch FROM evidence WHERE evidence_id IN ({placeholders}) ORDER BY evidence_id", dependency_ids).fetchall()
            found = {row["evidence_id"] for row in rows}
            evidence_state = [dict(row) for row in rows]
            evidence_state.extend({"evidence_id": eid, "missing": True} for eid in sorted(set(dependency_ids) - found))
        else:
            evidence_state = []
        return canonical_hash({"evidence": evidence_state, "rule_set_hash": rule_set_hash, "context_manifest_hash": context_manifest_hash})

    def record_stage_result(self, run_id: str, work_item_id: str | None, stage: str, subject_id: str | None, result: dict[str, Any], dependency_ids: list[str], dependency_hash: str, evidence_epoch: int, status: str = "SUCCEEDED") -> str:
        result_id = f"stage-result-{uuid.uuid4().hex}"
        with self.transaction() as db:
            db.execute("INSERT INTO stage_results VALUES(?,?,?,?,?,?,?,?,?,?,?)", (result_id, run_id, work_item_id, stage, subject_id, json.dumps(result, sort_keys=True), json.dumps(dependency_ids, sort_keys=True), dependency_hash, evidence_epoch, status, utc_now()))
        return result_id

    def get_stage_result(self, run_id: str, stage: str, subject_id: str | None = None) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM stage_results WHERE run_id=? AND stage=? AND (subject_id=? OR (? IS NULL AND subject_id IS NULL)) ORDER BY created_at DESC LIMIT 1", (run_id, stage, subject_id, subject_id)).fetchone()
        return dict(row) if row else None

    def record_execution_context(
        self,
        run_id: str,
        subject_id: str,
        context: dict[str, Any],
        context_manifest_hash: str,
        dependency_hash: str,
        evidence_epoch: int,
        dependency_ids: list[str] | None = None,
    ) -> None:
        ids = sorted(set(str(item) for item in (dependency_ids or [])))
        with self.transaction() as db:
            db.execute(
                "INSERT INTO execution_contexts(run_id,subject_id,context_json,context_manifest_hash,dependency_hash,dependency_ids_json,evidence_epoch,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET subject_id=excluded.subject_id,context_json=excluded.context_json,"
                "context_manifest_hash=excluded.context_manifest_hash,dependency_hash=excluded.dependency_hash,"
                "dependency_ids_json=excluded.dependency_ids_json,evidence_epoch=excluded.evidence_epoch,updated_at=excluded.updated_at",
                (run_id, subject_id, json.dumps(context, sort_keys=True), context_manifest_hash, dependency_hash, json.dumps(ids), evidence_epoch, utc_now()),
            )

    def get_execution_context(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM execution_contexts WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def record_debate_issue(self, run_id: str, subject_id: str, severity: str, finding: str, status: str = "OPEN") -> str:
        issue_id = f"issue-{uuid.uuid4().hex}"
        with self.transaction() as db:
            db.execute("INSERT INTO debate_issues VALUES(?,?,?,?,?,?,?)", (issue_id, run_id, subject_id, severity, status, finding, utc_now()))
        return issue_id

    def list_debate_issues(self, run_id: str, subject_id: str | None = None) -> list[dict[str, Any]]:
        if subject_id is None:
            rows = self.connection.execute("SELECT * FROM debate_issues WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM debate_issues WHERE run_id=? AND subject_id=? ORDER BY created_at", (run_id, subject_id)).fetchall()
        return [dict(row) for row in rows]

    def unresolved_critical(self, run_id: str, subject_id: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM debate_issues WHERE run_id=? AND subject_id=? AND severity='CRITICAL' AND status NOT IN ('RESOLVED','CLOSED') LIMIT 1", (run_id, subject_id)).fetchone()
        return row is not None

    def register_rule_override(self, override_id: str, version: str, rules: dict[str, Any], scope: dict[str, Any], effective_from: str, effective_until: str | None, authorized_by: str, active: bool = True) -> str:
        if not override_id or not version or not authorized_by or not isinstance(scope, dict):
            raise ValueError("RuleOverride requires id, version, scope and authorization")
        content_hash = canonical_hash(rules)
        with self.transaction() as db:
            db.execute("INSERT OR REPLACE INTO rule_overrides VALUES(?,?,?,?,?,?,?,?,?)", (override_id, version, content_hash, json.dumps(rules, sort_keys=True), json.dumps(scope, sort_keys=True), effective_from, effective_until, int(active), authorized_by))
        return content_hash

    def resolve_rule_set(self, override_id: str | None = None) -> EffectiveRuleSet:
        if not override_id:
            return EffectiveRuleSet()
        row = self.connection.execute("SELECT * FROM rule_overrides WHERE override_id=? AND active=1", (override_id,)).fetchone()
        if not row:
            raise ValueError("unregistered or inactive RuleOverride")
        now = utc_now()
        if now < row["effective_from"] or (row["effective_until"] and now > row["effective_until"]):
            raise ValueError("RuleOverride is outside its effective period")
        rules = json.loads(row["rules_json"])
        if canonical_hash(rules) != row["content_hash"] or not row["authorized_by"]:
            raise ValueError("RuleOverride integrity or authorization check failed")
        strategy_min = int(rules.get("strategy_min_days", 7)); strategy_max = int(rules.get("strategy_max_days", 56))
        if strategy_min <= 0 or strategy_min > strategy_max:
            raise ValueError("invalid strategy horizon in RuleOverride")
        freshness = {key: int(rules[key]) for key in ("max_age_market_context_hours", "max_market_context_sync_spread_hours", "max_age_market_execution_minutes", "max_age_portfolio_minutes", "max_age_sec_hours", "max_age_research_hours", "max_age_universe_hours", "max_future_skew_seconds") if key in rules}
        risk_budget = float(rules.get("per_position_risk_budget_pct", EffectiveRuleSet.per_position_risk_budget_pct))
        max_risk_budget = float(rules.get("max_per_position_risk_budget_pct", risk_budget))
        if risk_budget <= 0 or max_risk_budget <= 0 or risk_budget > max_risk_budget:
            raise ValueError("invalid Python-owned risk budget in RuleOverride")
        return EffectiveRuleSet(
            strategy_min_days=strategy_min,
            strategy_max_days=strategy_max,
            per_position_risk_budget_pct=risk_budget,
            max_per_position_risk_budget_pct=max_risk_budget,
            override_id=override_id,
            override_content_hash=row["content_hash"],
            authorization=row["authorized_by"],
            **freshness,
        )

    def reserve_cost(self, run_id: str, work_item_id: str, prompt_id: str, provider: str, model: str, estimated_cost: float = 0.0, reasoning_effort: str | None = None, wire_api: str | None = None, endpoint: str | None = None) -> str:
        rid = f"reservation-{uuid.uuid4().hex}"
        with self.transaction() as db:
            db.execute("INSERT INTO cost_reservations(reservation_id,run_id,work_item_id,prompt_id,provider,model,estimated_cost,status,created_at,reasoning_effort,wire_api,endpoint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (rid, run_id, work_item_id, prompt_id, provider, model, estimated_cost, "RESERVED", utc_now(), reasoning_effort, wire_api, endpoint))
        return rid

    def settle_cost(self, reservation_id: str, telemetry: dict[str, Any], retry_count: int = 0) -> None:
        with self.transaction() as db:
            db.execute("UPDATE cost_reservations SET status='SETTLED',input_tokens=?,output_tokens=?,cached_tokens=?,retry_count=?,latency_ms=?,actual_cost=?,settled_at=?,billing_source=?,usage_source=?,reasoning_output_tokens=? WHERE reservation_id=?", (int(telemetry.get("input_tokens", 0)), int(telemetry.get("output_tokens", 0)), int(telemetry.get("cached_tokens", 0)), retry_count, float(telemetry.get("latency_ms", 0)), float(telemetry.get("actual_cost", telemetry.get("estimated_cost", 0))), utc_now(), telemetry.get("billing_source"), telemetry.get("usage_source"), int(telemetry.get("reasoning_output_tokens", 0)), reservation_id))

    def lease_next(self, worker_id: str, lease_seconds: int = 30) -> WorkItem | None:
        now = utc_now()
        with self.transaction() as db:
            rows = db.execute("SELECT w.* FROM work_items w JOIN runs r ON r.run_id=w.run_id WHERE r.status='RUNNING' AND ((w.status='QUEUED' AND (w.available_after IS NULL OR w.available_after <= ?)) OR (w.status='LEASED' AND w.lease_until < ?)) ORDER BY w.priority,w.created_at", (now, now)).fetchall()
            row = None
            for candidate in rows:
                prerequisites = json.loads(candidate["prerequisite_json"] or "{}") if "prerequisite_json" in candidate.keys() else {}
                required_ids = prerequisites.get("work_item_ids", prerequisites if isinstance(prerequisites, list) else [])
                if required_ids:
                    marks = ",".join("?" for _ in required_ids)
                    complete = db.execute(f"SELECT COUNT(*) AS n FROM work_items WHERE work_item_id IN ({marks}) AND status='SUCCEEDED'", list(required_ids)).fetchone()["n"]
                    if int(complete) != len(required_ids):
                        continue
                row = candidate
                break
            if row is None:
                return None
            token = uuid.uuid4().hex
            attempt = int(row["attempt"]) + 1
            until = _expiry(lease_seconds)
            db.execute("UPDATE work_items SET status='LEASED',attempt=?,lease_token=?,leased_by=?,lease_until=?,updated_at=? WHERE work_item_id=?", (attempt, token, worker_id, until, now, row["work_item_id"]))
            self._log(db, "WORK_LEASED", {"attempt": attempt, "worker_id": worker_id}, run_id=row["run_id"], work_item_id=row["work_item_id"])
            row = db.execute("SELECT * FROM work_items WHERE work_item_id=?", (row["work_item_id"],)).fetchone()
        return self._row_to_work(row)

    @staticmethod
    def _row_to_work(row: sqlite3.Row) -> WorkItem:
        return WorkItem(row["work_item_id"], row["run_id"], row["stage"], json.loads(row["payload_json"]), WorkStatus(row["status"]), row["attempt"], row["lease_token"], row["leased_by"], row["lease_until"], row["dependency_hash"], row["evidence_epoch"], row["rule_set_hash"], row["context_manifest_hash"])

    def heartbeat(self, work_item_id: str, lease_token: str, lease_seconds: int = 30) -> bool:
        with self.transaction() as db:
            changed = db.execute("UPDATE work_items SET lease_until=?,updated_at=? WHERE work_item_id=? AND status='LEASED' AND lease_token=?", (_expiry(lease_seconds), utc_now(), work_item_id, lease_token)).rowcount
            if changed:
                self._log(db, "WORK_HEARTBEAT", {}, work_item_id=work_item_id)
            return bool(changed)

    def complete(self, item: WorkItem, result: dict[str, Any], current_dependency_hash: str, current_evidence_epoch: int, current_rule_set_hash: str, current_context_manifest_hash: str) -> WorkStatus:
        with self.transaction() as db:
            row = db.execute("SELECT * FROM work_items WHERE work_item_id=?", (item.work_item_id,)).fetchone()
            if row is None or row["status"] != WorkStatus.LEASED.value or row["lease_token"] != item.lease_token:
                raise RuntimeError("lease token is invalid")
            # Repository-owned fence: recompute active state from SQLite at commit time.
            run_row = db.execute("SELECT rule_set_hash,context_manifest_hash FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
            dependency_ids = json.loads(row["dependency_ids_json"] or "[]") if "dependency_ids_json" in row.keys() else []
            if dependency_ids:
                marks = ",".join("?" for _ in dependency_ids)
                evidence_rows = db.execute(f"SELECT evidence_id,payload_hash,epoch FROM evidence WHERE evidence_id IN ({marks}) ORDER BY evidence_id", dependency_ids).fetchall()
                found = {r["evidence_id"] for r in evidence_rows}
                evidence_state = [dict(r) for r in evidence_rows] + [{"evidence_id": e, "missing": True} for e in sorted(set(dependency_ids) - found)]
                live_epoch = max((int(r["epoch"]) for r in evidence_rows), default=0)
            else:
                # Zero-dependency WorkItems use the deterministic zero epoch.
                # Rule-set presence is not an evidence freshness signal.
                evidence_state, live_epoch = [], 0
            live_hash = canonical_hash({"evidence": evidence_state, "rule_set_hash": run_row["rule_set_hash"] if run_row else "", "context_manifest_hash": run_row["context_manifest_hash"] if run_row else ""})
            stale = any([row["dependency_hash"] != live_hash, row["evidence_epoch"] != live_epoch, row["rule_set_hash"] != (run_row["rule_set_hash"] if run_row else ""), row["context_manifest_hash"] != (run_row["context_manifest_hash"] if run_row else "")])
            status = WorkStatus.STALE_ON_ARRIVAL if stale else WorkStatus.SUCCEEDED
            db.execute("UPDATE work_items SET status=?,updated_at=?,last_error=? WHERE work_item_id=?", (status.value, utc_now(), "STALE_ON_ARRIVAL" if stale else None, item.work_item_id))
            db.execute("INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)", (f"result-{uuid.uuid4().hex}", item.work_item_id, json.dumps(result, sort_keys=True), live_hash, live_epoch, run_row["rule_set_hash"] if run_row else "", run_row["context_manifest_hash"] if run_row else "", status.value, utc_now()))
            self._log(db, status.value, {"attempt": item.attempt}, run_id=item.run_id, work_item_id=item.work_item_id)
            return status

    def retry(self, work_item_id: str, error: str) -> None:
        with self.transaction() as db:
            row = db.execute("SELECT w.attempt,w.max_attempts,r.status AS run_status FROM work_items w JOIN runs r ON r.run_id=w.run_id WHERE w.work_item_id=?", (work_item_id,)).fetchone()
            terminal = row is not None and int(row["attempt"]) >= int(row["max_attempts"])
            next_status = "FAILED" if terminal or row is None or row["run_status"] != "RUNNING" else "QUEUED"
            db.execute("UPDATE work_items SET status=?,lease_token=NULL,leased_by=NULL,lease_until=NULL,last_error=?,updated_at=? WHERE work_item_id=? AND status='LEASED'", (next_status, error, utc_now(), work_item_id))

    def reclaim_expired_work_items(self) -> int:
        """Crash recovery: expired leases become retryable queue items."""
        now = utc_now()
        with self.transaction() as db:
            changed = db.execute("UPDATE work_items SET status=CASE WHEN attempt >= max_attempts THEN 'FAILED' ELSE 'QUEUED' END,lease_token=NULL,leased_by=NULL,lease_until=NULL,last_error='LEASE_EXPIRED',updated_at=? WHERE status='LEASED' AND lease_until < ? AND run_id IN (SELECT run_id FROM runs WHERE status='RUNNING')", (now, now)).rowcount
            if changed:
                self._log(db, "WORK_LEASES_RECLAIMED", {"count": int(changed)})
            return int(changed)

    def work_item_counts(self, run_id: str) -> dict[str, int]:
        rows = self.connection.execute("SELECT status,COUNT(*) AS n FROM work_items WHERE run_id=? GROUP BY status", (run_id,)).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def latest_succeeded_work_item(self, run_id: str) -> str | None:
        row = self.connection.execute("SELECT work_item_id FROM work_items WHERE run_id=? AND status='SUCCEEDED' ORDER BY updated_at DESC LIMIT 1", (run_id,)).fetchone()
        return str(row["work_item_id"]) if row else None

    def work_item_id_for_stage(self, run_id: str, stage: str) -> str | None:
        row = self.connection.execute("SELECT work_item_id FROM work_items WHERE run_id=? AND stage=? AND status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1", (run_id, stage)).fetchone()
        return str(row["work_item_id"]) if row else None

    def stage_is_fresh(self, run_id: str, stage: str, subject_id: str, dependency_ids: list[str] | None = None) -> bool:
        row = self.connection.execute("SELECT dependency_hash,evidence_epoch,status FROM stage_results WHERE run_id=? AND stage=? AND subject_id=? ORDER BY created_at DESC LIMIT 1", (run_id, stage, subject_id)).fetchone()
        if not row or row["status"] != "SUCCEEDED":
            return False
        ids = dependency_ids if dependency_ids is not None else json.loads(self.connection.execute("SELECT dependency_ids_json FROM stage_results WHERE run_id=? AND stage=? AND subject_id=? ORDER BY created_at DESC LIMIT 1", (run_id, stage, subject_id)).fetchone()[0] or "[]")
        run = self.get_run(run_id)
        return row["dependency_hash"] == self.dependency_hash(ids, run.rule_set.rule_set_hash, run.context_manifest_hash) and int(row["evidence_epoch"]) == self.current_evidence_epoch_for(ids)

    def qualified_candidate_status(self, run_id: str, subject_id: str, *, strict: bool = True) -> tuple[bool, list[str]]:
        """Derive QualifiedCandidatePool eligibility from the complete persisted DAG.

        Every mandatory gate/capability is required; absence is fail-closed.
        """
        required = [
            "STAGE_GATE",
            "CAPITAL_PRESCREEN_GATE",
            "DEEP_RESEARCH",
            "FULL_SEC_FORENSIC",
            "ADVERSARIAL_AUDIT",
        ]
        if strict:
            required[2:2] = [
                "CATALYST_GATE",
                "CAP_FUNDAMENTAL_CHANGE",
                "CAP_CATALYST_EXPECTATION_RESEARCH",
                "CAP_DIRECTIONAL_PROBABILITY",
            ]
            required.insert(-1, "STANDARD_AUDIT")
            required.insert(-1, "EXPECTATION_GAP_GATE")
        missing: list[str] = []
        run = self.get_run(run_id)
        if run is None:
            return False, ["RUN_NOT_FOUND"]
        for stage in required:
            row = self.connection.execute(
                "SELECT result_json,dependency_ids_json,dependency_hash,evidence_epoch,status "
                "FROM stage_results WHERE run_id=? AND stage=? AND subject_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (run_id, stage, subject_id),
            ).fetchone()
            if not row or row["status"] != "SUCCEEDED":
                missing.append(stage)
                continue
            try:
                result = json.loads(row["result_json"] or "{}")
                ids = json.loads(row["dependency_ids_json"] or "[]")
            except (TypeError, ValueError):
                missing.append(f"INVALID_{stage}")
                continue
            if row["dependency_hash"] != self.dependency_hash(ids, run.rule_set.rule_set_hash, run.context_manifest_hash):
                missing.append(f"STALE_{stage}")
            if int(row["evidence_epoch"]) != self.current_evidence_epoch_for(ids):
                missing.append(f"STALE_EPOCH_{stage}")
            if stage == "STAGE_GATE" and result.get("decision") != "PASS":
                missing.append("STAGE_GATE_PASS")
            elif stage == "CAPITAL_PRESCREEN_GATE" and result.get("decision") not in {"PASS", "PASS_WITH_CONSTRAINTS"}:
                missing.append("CAPITAL_PRESCREEN_GATE_PASS")
            elif stage == "CATALYST_GATE" and result.get("decision") != "PASS":
                missing.append("CATALYST_GATE_PASS")
            elif stage == "EXPECTATION_GAP_GATE" and result.get("decision") != "PASS":
                missing.append("EXPECTATION_GAP_GATE_PASS")
            elif stage == "DEEP_RESEARCH" and result.get("research_status") != "COMPLETE":
                missing.append("DEEP_RESEARCH_COMPLETE")
            elif stage == "FULL_SEC_FORENSIC" and result.get("status") != "COMPLETE":
                missing.append("FULL_SEC_COMPLETE")
            elif stage in {"CAP_FUNDAMENTAL_CHANGE", "CAP_CATALYST_EXPECTATION_RESEARCH", "CAP_DIRECTIONAL_PROBABILITY", "STANDARD_AUDIT"}:
                if result.get("status") in {"INCOMPLETE", "CONTEXT_INCOMPLETE", "BLOCKED", "REJECT"}:
                    missing.append(f"{stage}_COMPLETE")
            elif stage == "ADVERSARIAL_AUDIT" and result.get("audit_recommendation") in {"CHALLENGES_CONTINUATION", "AUDIT_EVIDENCE_INCOMPLETE"}:
                missing.append("AUDIT_REJECT")
        if self.unresolved_critical(run_id, subject_id):
            missing.append("UNRESOLVED_CRITICAL")
        return not missing, sorted(set(missing))

    def require_strengthening_evidence(self, subject_id: str, evidence_ids: list[str]) -> None:
        """Require active, same-security Evidence with immutable RawArtifact provenance."""
        ids = sorted(set(str(item) for item in evidence_ids))
        if not ids:
            raise ValueError("ADD requires non-empty strengthening evidence")
        marks = ",".join("?" for _ in ids)
        rows = self.connection.execute(
            f"SELECT evidence_id,subject_id,status,payload_hash,observed_at "
            f"FROM evidence WHERE evidence_id IN ({marks})", ids
        ).fetchall()
        if len(rows) != len(ids) or any(
            row["subject_id"] != subject_id or row["status"] != "ACTIVE"
            or not row["payload_hash"] for row in rows
        ):
            raise ValueError("strengthening evidence is missing, stale, or belongs to another security")
        for row in rows:
            artifact = self.connection.execute(
                "SELECT artifact_id,subject_id,payload_hash,source_observed_at,retrieved_at "
                "FROM raw_artifacts WHERE payload_hash=? AND (subject_id=? OR subject_id IS NULL) "
                "ORDER BY created_at DESC LIMIT 1",
                (row["payload_hash"], subject_id),
            ).fetchone()
            if not artifact or artifact["payload_hash"] != row["payload_hash"]:
                raise ValueError("strengthening evidence lacks RawArtifact provenance")
            if not artifact["source_observed_at"] or not artifact["retrieved_at"]:
                raise ValueError("strengthening evidence lacks source/retrieval timestamps")

    def validate_economic_receipt(self, run_id: str, subject_id: str, receipt: dict[str, Any], candidate_evidence_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
        """Validate receipt arithmetic and prove its result/evidence lineage."""
        from .gates import validate_economic_assessment
        validated = validate_economic_assessment(receipt, subject_id, candidate_evidence_ids)
        source_ids = [str(item) for item in validated.get("source_result_ids") or []]
        marks = ",".join("?" for _ in source_ids)
        if not marks:
            raise ValueError("economic receipt has no source result ids")
        stage_rows = self.connection.execute(f"SELECT result_id FROM stage_results WHERE run_id=? AND status='SUCCEEDED' AND result_id IN ({marks})", [run_id, *source_ids]).fetchall()
        result_rows = self.connection.execute(f"SELECT result_id FROM results WHERE status='SUCCEEDED' AND result_id IN ({marks})", source_ids).fetchall()
        found = {str(row["result_id"]) for row in stage_rows} | {str(row["result_id"]) for row in result_rows}
        if set(source_ids) - found:
            raise ValueError("economic receipt references missing or non-authoritative result ids")
        return validated

    def list_stage_results(self, run_id: str, subject_id: str | None = None) -> list[dict[str, Any]]:
        if subject_id is None:
            rows = self.connection.execute("SELECT * FROM stage_results WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM stage_results WHERE run_id=? AND subject_id=? ORDER BY created_at", (run_id, subject_id)).fetchall()
        return [dict(row) for row in rows]

    def record_funnel(self, run_id: str, funnel_stage: str, count: int, details: dict[str, Any] | None = None) -> None:
        """Persist deterministic discovery-funnel counts for one run."""
        if not str(funnel_stage).strip() or int(count) < 0:
            raise ValueError("funnel stage and non-negative count are required")
        with self.transaction() as db:
            db.execute(
                """INSERT INTO discovery_funnel(run_id,funnel_stage,count,details_json,created_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(run_id,funnel_stage) DO UPDATE SET
                   count=excluded.count, details_json=excluded.details_json,
                   created_at=excluded.created_at""",
                (run_id, str(funnel_stage), int(count), json.dumps(details or {}, sort_keys=True), utc_now()),
            )

    def list_funnel(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM discovery_funnel WHERE run_id=? ORDER BY rowid", (run_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def commit_final_allocation(self, run: Run, action: str, allocation: dict[str, Any], positive_commitments: int | None = None) -> str:
        """Python-only final writer. Existing-position reductions may be many; fresh money is 0..1."""
        if run.mode != RunMode.HUNT_AND_EXECUTION_REVIEW:
            raise ValueError("HUNT_ONLY cannot create ExecutionAction")
        action_enum = str(action)
        if action_enum not in {"NO_TRADE", "WATCH", "STARTER", "ADD", "FULL", "TRIM", "EXIT"}:
            raise ValueError("legacy or unknown ExecutionAction")
        from .gates import validate_final_allocation_contract
        from .models import ExecutionAction
        validate_final_allocation_contract(ExecutionAction(action_enum), allocation, run.rule_set)
        shares = int(allocation.get("shares", 0))
        capital_pct = float(allocation.get("capital_pct", 0))
        positive = int(action_enum in {"STARTER", "ADD", "FULL"} and shares > 0 and capital_pct > 0)
        # The optional argument is retained for compatibility but is never
        # used to decide cardinality.  The transaction queries final_actions.
        if positive_commitments is not None and int(positive_commitments) < 0:
            raise ValueError("positive_commitments cannot be negative")
        action_id = f"allocation-{uuid.uuid4().hex}"
        subject_id = str(allocation.get("security_id", ""))
        if not subject_id:
            raise ValueError("position identity required")
        with self.transaction() as db:
            context = db.execute("SELECT * FROM execution_contexts WHERE run_id=?", (run.run_id,)).fetchone()
            if not context or context["subject_id"] != subject_id or context["context_manifest_hash"] != run.context_manifest_hash:
                raise ValueError("fresh execution context is required")
            context_payload = json.loads(context["context_json"])
            synthesis = context_payload.get("synthesis") or {}
            actionable = {"STARTER", "ADD", "FULL", "TRIM", "EXIT"}
            if action_enum in actionable and (synthesis.get("recommendation_status") != "READY" or synthesis.get("recommended_action") != action_enum):
                raise ValueError("Final Synthesis is not READY for authoritative action")
            economic = allocation.get("economic_assessment")
            if economic is not None:
                self.validate_economic_receipt(run.run_id, subject_id, economic, [str(row["evidence_id"]) for row in db.execute("SELECT evidence_id FROM evidence WHERE subject_id=? AND status='ACTIVE'", (subject_id,)).fetchall()])
            for required_stage in ("STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE", "CAP_FUNDAMENTAL_CHANGE", "CAP_CATALYST_EXPECTATION_RESEARCH", "CAP_DIRECTIONAL_PROBABILITY", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "STANDARD_AUDIT", "ADVERSARIAL_AUDIT", "EXPECTATION_GAP_GATE"):
                stage_row = db.execute("SELECT 1 FROM stage_results WHERE run_id=? AND stage=? AND subject_id=? AND status='SUCCEEDED' LIMIT 1", (run.run_id, required_stage, subject_id)).fetchone()
                if not stage_row:
                    raise ValueError(f"mandatory stage missing: {required_stage}")
            qualified, missing = self.qualified_candidate_status(run.run_id, subject_id)
            if not qualified:
                raise ValueError(f"qualified candidate prerequisites failed: {missing}")
            dependency_ids = json.loads(context["dependency_ids_json"] or "[]")
            if not dependency_ids:
                raise ValueError("execution dependency set is missing")
            marks = ",".join("?" for _ in dependency_ids)
            dep_rows = db.execute(
                f"SELECT evidence_id,payload_hash,epoch FROM evidence WHERE evidence_id IN ({marks}) ORDER BY evidence_id",
                dependency_ids,
            ).fetchall()
            found = {str(row["evidence_id"]) for row in dep_rows}
            evidence_state = [dict(row) for row in dep_rows]
            evidence_state.extend({"evidence_id": eid, "missing": True} for eid in sorted(set(dependency_ids) - found))
            live_dep_hash = canonical_hash({"evidence": evidence_state, "rule_set_hash": run.rule_set.rule_set_hash, "context_manifest_hash": run.context_manifest_hash})
            if context["dependency_hash"] != live_dep_hash:
                raise ValueError("execution dependency is stale")
            if self._has_live_critical(db, run.run_id, subject_id):
                raise ValueError("unresolved CRITICAL issue")
            if positive:
                existing = db.execute("SELECT COUNT(*) AS n FROM final_actions WHERE run_id=? AND positive_commitment=1", (run.run_id,)).fetchone()["n"]
                if int(existing) + 1 > run.rule_set.fresh_money_max_positive_commitments:
                    raise ValueError("Fresh Money positive commitment cardinality exceeded")
            db.execute("INSERT INTO final_actions VALUES(?,?,?,?,?,?,?,?,?,?,?)", (action_id, run.run_id, subject_id, action_enum, str(allocation.get("action_scope", "CANDIDATE")), shares, capital_pct, positive, context["dependency_hash"], context["evidence_epoch"], utc_now()))
            self._log(db, "FINAL_ALLOCATION_COMMITTED", {"action": action_enum, "positive_commitment": positive}, run_id=run.run_id)
        return action_id

    @staticmethod
    def _has_live_critical(db: sqlite3.Connection, run_id: str, subject_id: str) -> bool:
        row = db.execute("SELECT 1 FROM debate_issues WHERE run_id=? AND subject_id=? AND severity='CRITICAL' AND status NOT IN ('RESOLVED','CLOSED') LIMIT 1", (run_id, subject_id)).fetchone()
        return row is not None
