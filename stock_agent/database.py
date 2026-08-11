from __future__ import annotations

import json
import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from .schemas import (CertificationResult, CompanyState, EvidenceItem,
                      InvestmentDecision, MarketSnapshot, now_iso)


OUTPUT_TABLES = {"research_outputs", "critic_outputs", "risk_outputs", "chairman_outputs"}


class Database:
    SCHEMA_VERSION = 26

    def __init__(self, path: str, busy_timeout_ms: int = 5000, wal: bool = True):
        self.path = Path(path)
        self.busy_timeout_ms = max(1000, int(busy_timeout_ms))
        self.wal = bool(wal)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as c:
            if self.wal:
                c.execute("PRAGMA journal_mode=WAL")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS analysis_runs (
                run_id TEXT PRIMARY KEY, ticker TEXT, requested_at TEXT, started_at TEXT,
                finished_at TEXT, status TEXT, mode TEXT, research_provider TEXT,
                research_model TEXT, critic_provider TEXT, critic_model TEXT,
                input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
                estimated_cost REAL DEFAULT 0, prompt_version TEXT, risk_rule_version TEXT,
                final_decision TEXT, final_confidence INTEGER, error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS market_snapshots (
                run_id TEXT, ticker TEXT, timestamp TEXT, payload_json TEXT,
                PRIMARY KEY (run_id, ticker)
            );
            CREATE TABLE IF NOT EXISTS evidence_metadata (
                evidence_id TEXT PRIMARY KEY, ticker TEXT, source_type TEXT,
                document_type TEXT, published_at TEXT, evidence_grade TEXT, payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence_items (
                evidence_id TEXT PRIMARY KEY, ticker TEXT, source_type TEXT,
                document_type TEXT, published_at TEXT, evidence_grade TEXT, payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS research_outputs (run_id TEXT PRIMARY KEY, ticker TEXT, payload_json TEXT);
            CREATE TABLE IF NOT EXISTS critic_outputs (run_id TEXT PRIMARY KEY, ticker TEXT, payload_json TEXT);
            CREATE TABLE IF NOT EXISTS risk_outputs (run_id TEXT PRIMARY KEY, ticker TEXT, payload_json TEXT);
            CREATE TABLE IF NOT EXISTS chairman_outputs (run_id TEXT PRIMARY KEY, ticker TEXT, payload_json TEXT);
            CREATE TABLE IF NOT EXISTS investment_decisions (
                run_id TEXT PRIMARY KEY, ticker TEXT, timestamp TEXT, decision TEXT,
                confidence INTEGER, payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS final_decisions (
                run_id TEXT PRIMARY KEY, ticker TEXT, timestamp TEXT, decision TEXT,
                confidence INTEGER, payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS run_manifests (
                run_id TEXT PRIMARY KEY, ticker TEXT, payload_json TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS market_regimes (
                run_id TEXT PRIMARY KEY, regime TEXT, payload_json TEXT, observed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS company_states (
                ticker TEXT PRIMARY KEY, updated_at TEXT, payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS portfolio_positions (
                ticker TEXT PRIMARY KEY, quantity REAL NOT NULL, average_price REAL NOT NULL,
                updated_at TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'PAPER'
            );
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, timestamp TEXT,
                side TEXT, quantity REAL, price REAL, mode TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ticker TEXT, timestamp TEXT,
                side TEXT, quantity REAL, price REAL, mode TEXT DEFAULT 'PAPER'
            );
            CREATE TABLE IF NOT EXISTS paper_performance (
                run_id TEXT, horizon_days INTEGER, return_pct REAL, qqq_alpha REAL,
                iwm_alpha REAL, sector_alpha REAL, mfe REAL, mae REAL,
                stop_hit INTEGER, target1_hit INTEGER, target2_hit INTEGER,
                measured_at TEXT, PRIMARY KEY(run_id, horizon_days)
            );
            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ticker TEXT, provider TEXT,
                purpose TEXT, timestamp TEXT, input_tokens INTEGER, output_tokens INTEGER,
                cached_tokens INTEGER DEFAULT 0, latency_ms INTEGER DEFAULT 0, success INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS model_costs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, provider TEXT, model TEXT,
                estimated_cost REAL, currency TEXT, timestamp TEXT
            );
            CREATE TABLE IF NOT EXISTS system_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, level TEXT,
                event TEXT, payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS user_requests (
                request_id TEXT PRIMARY KEY, discord_message_id TEXT UNIQUE,
                discord_user_id TEXT, received_at TEXT, original_text TEXT,
                intent TEXT, tickers_json TEXT, time_horizon TEXT, focus_json TEXT,
                comparison_mode TEXT, parser_type TEXT, parser_confidence REAL,
                missing_fields_json TEXT, status TEXT, run_id TEXT, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pending_clarifications (
                pending_request_id TEXT PRIMARY KEY, discord_user_id TEXT,
                channel_id TEXT, original_text TEXT, missing_fields_json TEXT,
                expires_at TEXT, payload_json TEXT, status TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS processed_discord_messages (
                discord_message_id TEXT PRIMARY KEY, discord_user_id TEXT,
                channel_id TEXT, received_at TEXT, request_id TEXT
            );
            CREATE TABLE IF NOT EXISTS run_stage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, stage TEXT,
                status TEXT, timestamp TEXT, payload_json TEXT
            );
            CREATE TABLE IF NOT EXISTS report_artifacts (
                run_id TEXT PRIMARY KEY, ticker_label TEXT, markdown_path TEXT,
                publish_status TEXT, publish_attempts INTEGER DEFAULT 0,
                last_error TEXT, created_at TEXT
            );
            """)
            self._ensure_run_columns(c)
            self._ensure_api_columns(c)
            self._migrate_v11(c)
            self._migrate_v21_certification(c)
            self._migrate_v21_financial_integrity(c)
            self._migrate_v21_evidence_lineage(c)
            self._migrate_v21_evidence_review(c)
            self._migrate_v21_debate_identity(c)
            self._migrate_v21_paper_policy(c)
            self._migrate_v21_telemetry(c)
            self._migrate_v21_account_identity_and_financial_cancellation(c)
            self._migrate_v22_risk_provenance(c)
            self._migrate_v23_discovery(c)
            self._migrate_v24_discovery_mvp(c)
            self._migrate_v25_discovery_audit(c)
            self._migrate_v26_discovery_funnel(c)

    @staticmethod
    def _ensure_run_columns(c: sqlite3.Connection) -> None:
        existing = {row[1] for row in c.execute("PRAGMA table_info(analysis_runs)")}
        additions = {
            "started_at": "TEXT", "finished_at": "TEXT", "research_provider": "TEXT",
            "research_model": "TEXT", "critic_provider": "TEXT", "critic_model": "TEXT",
            "input_tokens": "INTEGER DEFAULT 0", "output_tokens": "INTEGER DEFAULT 0",
            "estimated_cost": "REAL DEFAULT 0", "prompt_version": "TEXT", "risk_rule_version": "TEXT",
        }
        for name, sql_type in additions.items():
            if name not in existing:
                c.execute(f"ALTER TABLE analysis_runs ADD COLUMN {name} {sql_type}")

    @staticmethod
    def _ensure_api_columns(c: sqlite3.Connection) -> None:
        existing = {row[1] for row in c.execute("PRAGMA table_info(api_usage)")}
        for name, sql_type in {"cached_tokens": "INTEGER DEFAULT 0", "latency_ms": "INTEGER DEFAULT 0",
                               "success": "INTEGER DEFAULT 1"}.items():
            if name not in existing:
                c.execute(f"ALTER TABLE api_usage ADD COLUMN {name} {sql_type}")

    @staticmethod
    def _ensure_columns(c: sqlite3.Connection, table: str,
                        additions: dict[str, str]) -> None:
        existing = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in additions.items():
            if name not in existing:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def _migrate_v11(self, c: sqlite3.Connection) -> None:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS llm_calls (
            api_call_id TEXT PRIMARY KEY, run_id TEXT, request_id TEXT, ticker TEXT,
            role TEXT NOT NULL, round_no INTEGER NOT NULL DEFAULT 0, phase TEXT NOT NULL,
            provider TEXT, model TEXT, reasoning_effort TEXT, started_at TEXT, finished_at TEXT,
            latency_ms INTEGER DEFAULT 0, input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0, reasoning_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0, cache_write_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0, api_calls INTEGER DEFAULT 1,
            estimated_cost_usd REAL DEFAULT 0, repair_attempt INTEGER DEFAULT 0,
            completed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, error_type TEXT,
            prompt_chars INTEGER DEFAULT 0, response_chars INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS stage_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
            role TEXT NOT NULL, round_no INTEGER NOT NULL, phase TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(run_id, role, round_no, phase)
        );
        CREATE TABLE IF NOT EXISTS debate_states (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, intensity TEXT NOT NULL,
            min_rounds INTEGER NOT NULL, max_rounds INTEGER NOT NULL, current_round INTEGER DEFAULT 0,
            research_stance TEXT, critic_stance TEXT, research_confidence INTEGER,
            critic_confidence INTEGER, provisional_consensus INTEGER DEFAULT 0,
            stress_test_completed INTEGER DEFAULT 0, final_consensus INTEGER DEFAULT 0,
            deadlock_reason TEXT, payload_json TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS debate_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, round_no INTEGER NOT NULL,
            phase TEXT NOT NULL, research_json TEXT, critic_json TEXT, consensus_json TEXT,
            prompt_chars INTEGER DEFAULT 0, created_at TEXT NOT NULL,
            UNIQUE(run_id, round_no, phase)
        );
        CREATE TABLE IF NOT EXISTS debate_issues (
            issue_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, topic TEXT NOT NULL, severity TEXT NOT NULL,
            research_position TEXT, critic_position TEXT, supporting_evidence_json TEXT,
            opposing_evidence_json TEXT, status TEXT NOT NULL, resolution_basis TEXT,
            opened_round INTEGER, last_updated_round INTEGER, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS thesis_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, round_no INTEGER NOT NULL,
            role TEXT NOT NULL, from_decision TEXT, to_decision TEXT, from_confidence INTEGER,
            to_confidence INTEGER, reason TEXT, evidence_ids_json TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence_requests (
            request_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, issue_id TEXT, question TEXT NOT NULL,
            severity TEXT, source_scope_json TEXT, target_forms_json TEXT, keywords_json TEXT,
            date_from TEXT, date_to TEXT, company_fact_targets_json TEXT, must_answer INTEGER,
            requesting_role TEXT, requested_round INTEGER, status TEXT, result_json TEXT,
            created_at TEXT, completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS evidence_conflicts (
            conflict_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, topic TEXT NOT NULL,
            evidence_ids_json TEXT NOT NULL, description TEXT NOT NULL, severity TEXT,
            status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS company_facts (
            fact_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, taxonomy TEXT, concept TEXT NOT NULL,
            unit TEXT, form TEXT, fy INTEGER, fp TEXT, start TEXT, end TEXT, filed TEXT,
            accn TEXT, value REAL, period_type TEXT, payload_json TEXT, ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS capital_structure_snapshots (
            snapshot_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
            as_of TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_accounts (
            account_id TEXT PRIMARY KEY, cash REAL NOT NULL, equity REAL NOT NULL,
            reserved_cash REAL NOT NULL DEFAULT 0, realized_pnl REAL NOT NULL DEFAULT 0,
            risk_budget_pct REAL NOT NULL DEFAULT 0.75, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_cash_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT NOT NULL, run_id TEXT,
            timestamp TEXT NOT NULL, event_type TEXT NOT NULL, amount REAL NOT NULL,
            balance_after REAL NOT NULL, note TEXT
        );
        CREATE TABLE IF NOT EXISTS paper_orders (
            order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, run_id TEXT NOT NULL,
            ticker TEXT NOT NULL, side TEXT NOT NULL, order_type TEXT NOT NULL,
            status TEXT NOT NULL, quantity REAL NOT NULL, trigger_price REAL,
            limit_price REAL, reserved_cash REAL NOT NULL DEFAULT 0, valid_until TEXT,
            invalidation_price REAL, sector TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS paper_predictions (
            prediction_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
            decision TEXT NOT NULL, confidence INTEGER NOT NULL, reference_price REAL NOT NULL,
            horizon TEXT, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_queue (
            job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, run_id TEXT, payload_json TEXT NOT NULL,
            status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100, created_at TEXT NOT NULL,
            started_at TEXT, heartbeat_at TEXT, finished_at TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0, attempt INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        );
        CREATE TABLE IF NOT EXISTS run_cancellations (
            run_id TEXT PRIMARY KEY, status TEXT NOT NULL, requested_at TEXT,
            acknowledged_at TEXT, reason TEXT
        );
        CREATE TABLE IF NOT EXISTS knowledge_sync (
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ticker TEXT NOT NULL,
            status TEXT NOT NULL, vault_path TEXT, error TEXT, synced_at TEXT NOT NULL
        );
        """)
        self._ensure_columns(c, "analysis_runs", {
            "request_id": "TEXT", "analysis_intensity": "TEXT DEFAULT 'NORMAL'",
            "debate_status": "TEXT", "round_count": "INTEGER DEFAULT 0",
            "delivery_status": "TEXT DEFAULT 'PENDING'",
            "delivered_at": "TEXT",
            "cancellation_status": "TEXT DEFAULT 'ACTIVE'",
            "reasoning_tokens": "INTEGER DEFAULT 0", "cached_tokens": "INTEGER DEFAULT 0",
            "total_latency_ms": "INTEGER DEFAULT 0", "llm_call_count": "INTEGER DEFAULT 0",
        })
        self._ensure_columns(c, "user_requests", {
            "analysis_intensity": "TEXT DEFAULT 'NORMAL'", "min_debate_rounds": "INTEGER DEFAULT 3",
            "max_debate_rounds": "INTEGER DEFAULT 5", "intensity_explicit": "INTEGER DEFAULT 0",
            "reasoning_profile": "TEXT DEFAULT 'high'", "evidence_depth": "TEXT DEFAULT 'STANDARD'",
            "max_evidence_refreshes": "INTEGER DEFAULT 2",
            "consensus_stress_test_required": "INTEGER DEFAULT 0",
        })
        self._ensure_columns(c, "portfolio_positions", {
            "account_id": "TEXT DEFAULT 'PAPER_DEFAULT'", "sector": "TEXT DEFAULT 'UNKNOWN'",
            "status": "TEXT DEFAULT 'OPEN'", "market_value": "REAL DEFAULT 0",
            "unrealized_pnl": "REAL DEFAULT 0", "position_risk_usd": "REAL DEFAULT 0",
            "risk_provenance_json": "TEXT NOT NULL DEFAULT '{}'", "risk_as_of": "TEXT",
        })
        self._ensure_columns(c, "report_artifacts", {
            "delivered_at": "TEXT", "next_retry_at": "TEXT",
        })
        c.executescript("""
        CREATE INDEX IF NOT EXISTS idx_runs_status ON analysis_runs(status, requested_at);
        CREATE INDEX IF NOT EXISTS idx_requests_status ON user_requests(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_evidence_ticker_date ON evidence_items(ticker, published_at);
        CREATE INDEX IF NOT EXISTS idx_llm_calls_run_round ON llm_calls(run_id, role, round_no);
        CREATE INDEX IF NOT EXISTS idx_queue_status ON job_queue(status, priority, created_at);
        CREATE INDEX IF NOT EXISTS idx_reports_delivery ON report_artifacts(publish_status, created_at);
        CREATE INDEX IF NOT EXISTS idx_paper_orders_status ON paper_orders(account_id, status, ticker);
        """)
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)",
                  (12, now_iso(), "v1.1 delivery timestamp compatibility"))

    def _migrate_v21_certification(self, c: sqlite3.Connection) -> None:
        self._ensure_columns(c, "analysis_runs", {
            "execution_status": "TEXT",
            "analysis_status": "TEXT",
            "certification_status": "TEXT",
            "side_effect_status": "TEXT",
            "certified_action": "TEXT",
            "certification_reason": "TEXT",
        })
        c.executescript("""
        CREATE TABLE IF NOT EXISTS certification_records (
            run_id TEXT PRIMARY KEY,
            execution_status TEXT NOT NULL,
            analysis_status TEXT NOT NULL,
            certification_status TEXT NOT NULL,
            side_effect_status TEXT NOT NULL,
            certified_action TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL,
            required_data_failures_json TEXT NOT NULL,
            important_data_warnings_json TEXT NOT NULL,
            decision_confidence INTEGER,
            trade_plan_status TEXT NOT NULL,
            position_sizing_status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            evaluated_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES analysis_runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_runs_certification
            ON analysis_runs(certification_status, requested_at);
        """)
        # Historical v1.1 rows cannot be retroactively certified. Preserve their legacy decision
        # columns, but make the new read path explicitly untrusted rather than inventing provenance.
        c.execute("""UPDATE analysis_runs SET
            execution_status=CASE WHEN status='SUCCESS' THEN 'SUCCESS'
                                  WHEN status='CANCELLED' THEN 'CANCELLED'
                                  WHEN status='RUNNING' THEN 'RUNNING' ELSE 'FAILED' END,
            analysis_status=COALESCE(analysis_status, 'LEGACY_PROVENANCE_UNKNOWN'),
            certification_status=COALESCE(certification_status, 'BLOCKED_SYSTEM_INTEGRITY'),
            side_effect_status=COALESCE(side_effect_status, 'LEGACY_PROVENANCE_UNKNOWN'),
            certified_action=COALESCE(certified_action, 'NO_CERTIFIED_ACTION'),
            certification_reason=COALESCE(certification_reason, 'LEGACY_PROVENANCE_UNKNOWN')
            WHERE execution_status IS NULL""")
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)",
                  (13, now_iso(),
                   "v2.1 independent execution/analysis/certification/side-effect statuses"))

    def _migrate_v21_financial_integrity(self, c: sqlite3.Connection) -> None:
        self._ensure_columns(c, "job_queue", {
            "stable_key": "TEXT", "lease_owner": "TEXT", "lease_until": "TEXT",
        })
        self._ensure_columns(c, "paper_transactions", {
            "financial_operation_key": "TEXT", "cost_basis_method": "TEXT DEFAULT 'WEIGHTED_AVERAGE'",
        })
        self._ensure_columns(c, "paper_cash_ledger", {
            "financial_operation_key": "TEXT",
        })
        self._ensure_columns(c, "paper_orders", {
            "financial_operation_key": "TEXT",
        })
        c.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_job_queue_stable_key
            ON job_queue(stable_key) WHERE stable_key IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_tx_financial_operation
            ON paper_transactions(financial_operation_key)
            WHERE financial_operation_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS financial_operations (
            operation_key TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            committed_at TEXT,
            cancellation_status TEXT
        );
        CREATE TABLE IF NOT EXISTS financial_journal (
            journal_id TEXT PRIMARY KEY,
            financial_operation_key TEXT NOT NULL,
            account_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            ticker TEXT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            cash_delta REAL NOT NULL DEFAULT 0,
            quantity_delta REAL NOT NULL DEFAULT 0,
            price REAL,
            balance_after REAL,
            position_quantity_after REAL,
            cost_basis_method TEXT NOT NULL DEFAULT 'WEIGHTED_AVERAGE',
            payload_json TEXT NOT NULL,
            FOREIGN KEY(financial_operation_key) REFERENCES financial_operations(operation_key)
        );
        CREATE TABLE IF NOT EXISTS paper_reservations (
            reservation_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL UNIQUE,
            account_id TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            released_at TEXT
        );
        CREATE TABLE IF NOT EXISTS outbox_events (
            event_id TEXT PRIMARY KEY,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            published_at TEXT,
            last_error TEXT,
            UNIQUE(aggregate_id, event_type)
        );
        """)
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            14, now_iso(),
            "v2.1 queue CAS, financial operation idempotency, journal, reservation, outbox"))

    def _migrate_v21_evidence_lineage(self, c: sqlite3.Connection) -> None:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS raw_filings (
            raw_filing_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
            ticker TEXT NOT NULL, accession TEXT, document_type TEXT, source_url TEXT,
            content_hash TEXT, lifecycle_status TEXT NOT NULL, payload_json TEXT NOT NULL,
            collected_at TEXT NOT NULL, UNIQUE(run_id,evidence_id)
        );
        CREATE TABLE IF NOT EXISTS evidence_processing_receipts (
            run_id TEXT NOT NULL, evidence_id TEXT NOT NULL, content_hash TEXT,
            collected_at TEXT, parsed_at TEXT, selected_for_context_at TEXT,
            research_seen_round INTEGER, critic_seen_round INTEGER, chairman_seen INTEGER DEFAULT 0,
            lifecycle_status TEXT NOT NULL, exhibits_resolved INTEGER DEFAULT 0,
            validated_at TEXT, ready_for_analysis_at TEXT,
            PRIMARY KEY(run_id,evidence_id)
        );
        CREATE TABLE IF NOT EXISTS raw_companyfacts (
            raw_companyfacts_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
            cik TEXT, content_hash TEXT NOT NULL, payload_json TEXT NOT NULL, fetched_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS raw_xbrl_facts (
            fact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
            taxonomy TEXT, concept TEXT NOT NULL, unit TEXT, payload_json TEXT NOT NULL,
            ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS normalized_facts (
            normalized_fact_id TEXT PRIMARY KEY, raw_fact_id TEXT NOT NULL, run_id TEXT NOT NULL,
            ticker TEXT NOT NULL, concept TEXT NOT NULL, unit TEXT, fact_nature TEXT NOT NULL,
            form TEXT, fy INTEGER, fp TEXT, start TEXT, end TEXT, duration_days INTEGER,
            filed TEXT, accn TEXT, frame TEXT, value REAL, status TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(raw_fact_id) REFERENCES raw_xbrl_facts(fact_id)
        );
        CREATE TABLE IF NOT EXISTS derived_metrics (
            derived_metric_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, ticker TEXT NOT NULL,
            metric_name TEXT NOT NULL, value REAL, status TEXT NOT NULL, formula TEXT,
            source_fact_ids_json TEXT NOT NULL, as_of TEXT, method TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_receipts_ready
            ON evidence_processing_receipts(run_id,lifecycle_status);
        CREATE INDEX IF NOT EXISTS idx_normalized_facts_lookup
            ON normalized_facts(ticker,concept,end,filed);
        """)
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            15, now_iso(),
            "v2.1 raw filing/XBRL, normalized fact, derived metric, evidence receipt lineage"))

    def _migrate_v21_evidence_review(self, c: sqlite3.Connection) -> None:
        self._ensure_columns(c, "evidence_requests", {
            "resolved_by_evidence_ids_json": "TEXT DEFAULT '[]'",
            "reviewed_by_research": "INTEGER DEFAULT 0",
            "reviewed_by_critic": "INTEGER DEFAULT 0",
            "material_generation": "INTEGER DEFAULT 0",
        })
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            16, now_iso(),
            "v2.1 evidence request review lifecycle and processing receipts"))

    def _migrate_v21_debate_identity(self, c: sqlite3.Connection) -> None:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS debate_issue_instances (
            issue_instance_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            semantic_issue_key TEXT NOT NULL,
            parent_issue_id TEXT,
            topic TEXT NOT NULL,
            severity TEXT NOT NULL,
            materiality TEXT NOT NULL,
            research_position TEXT,
            critic_position TEXT,
            supporting_evidence_json TEXT NOT NULL,
            opposing_evidence_json TEXT NOT NULL,
            status TEXT NOT NULL,
            resolution_basis TEXT,
            opened_round INTEGER NOT NULL,
            last_updated_round INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id,semantic_issue_key)
        );
        CREATE TABLE IF NOT EXISTS debate_round_information_gain (
            run_id TEXT NOT NULL, round_no INTEGER NOT NULL,
            new_material_evidence INTEGER NOT NULL,
            material_thesis_change INTEGER NOT NULL,
            material_issue_closed INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(run_id,round_no)
        );
        CREATE TABLE IF NOT EXISTS thesis_change_events (
            change_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, round_no INTEGER NOT NULL,
            role TEXT NOT NULL, from_decision TEXT, to_decision TEXT,
            from_confidence INTEGER, to_confidence INTEGER, reason TEXT,
            evidence_ids_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            17, now_iso(),
            "v2.1 issue instance/semantic identity and debate information gain"))

    def _migrate_v21_paper_policy(self, c: sqlite3.Connection) -> None:
        self._ensure_columns(c, "portfolio_positions", {
            "latest_mark": "REAL", "mark_timestamp": "TEXT", "mark_source": "TEXT",
            "mark_status": "TEXT NOT NULL DEFAULT 'NO_MARK'",
        })
        self._ensure_columns(c, "paper_orders", {
            "status_reason": "TEXT", "triggered_at": "TEXT", "revalidated_at": "TEXT",
            "filled_at": "TEXT",
        })
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            18, now_iso(),
            "v2.1 PAPER canonical validation, mark provenance, conditional lifecycle"))

    def _migrate_v21_telemetry(self, c: sqlite3.Connection) -> None:
        self._ensure_columns(c, "llm_calls", {
            "call_id": "TEXT", "parent_call_id": "TEXT", "attempt": "INTEGER DEFAULT 1",
            "usage_known": "INTEGER NOT NULL DEFAULT 0", "exception_type": "TEXT",
        })
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_call_id ON llm_calls(call_id)")
        c.execute("UPDATE llm_calls SET call_id=api_call_id WHERE call_id IS NULL")
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            19, now_iso(),
            "v2.1 canonical internal GenAI telemetry identity and usage-known status"))

    def _migrate_v21_account_identity_and_financial_cancellation(
            self, c: sqlite3.Connection) -> None:
        pk = [row[1] for row in c.execute("PRAGMA table_info(portfolio_positions)") if row[5]]
        if pk != ["ticker", "account_id"]:
            c.executescript("""
            CREATE TABLE portfolio_positions_v21 (
                ticker TEXT NOT NULL, quantity REAL NOT NULL, average_price REAL NOT NULL,
                updated_at TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'PAPER',
                account_id TEXT NOT NULL DEFAULT 'PAPER_DEFAULT', sector TEXT DEFAULT 'UNKNOWN',
                status TEXT DEFAULT 'OPEN', market_value REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0, position_risk_usd REAL DEFAULT 0,
                risk_provenance_json TEXT NOT NULL DEFAULT '{}', risk_as_of TEXT,
                latest_mark REAL, mark_timestamp TEXT,
                mark_source TEXT, mark_status TEXT NOT NULL DEFAULT 'NO_MARK',
                PRIMARY KEY(ticker,account_id)
            );
            INSERT INTO portfolio_positions_v21(
                ticker,quantity,average_price,updated_at,mode,account_id,sector,status,
                market_value,unrealized_pnl,position_risk_usd,risk_provenance_json,risk_as_of,
                latest_mark,mark_timestamp,mark_source,mark_status)
            SELECT ticker,quantity,average_price,updated_at,mode,
                COALESCE(account_id,'PAPER_DEFAULT'),COALESCE(sector,'UNKNOWN'),
                COALESCE(status,'OPEN'),COALESCE(market_value,0),COALESCE(unrealized_pnl,0),
                COALESCE(position_risk_usd,0),COALESCE(risk_provenance_json,'{}'),risk_as_of,
                latest_mark,mark_timestamp,mark_source,COALESCE(mark_status,'NO_MARK')
            FROM portfolio_positions;
            DROP TABLE portfolio_positions;
            ALTER TABLE portfolio_positions_v21 RENAME TO portfolio_positions;
            CREATE INDEX IF NOT EXISTS idx_positions_account_status
                ON portfolio_positions(account_id,status,ticker);
            """)
        c.executescript("""
        CREATE TABLE IF NOT EXISTS financial_cancellation_requests (
            financial_operation_key TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            resolved_at TEXT,
            reason TEXT
        );
        """)
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            21, now_iso(),
            "v2.1 composite PAPER account position identity and cancellation boundary"))

    @staticmethod
    def _migrate_v22_risk_provenance(c: sqlite3.Connection) -> None:
        """Persist stop-based risk provenance as an explicit schema migration."""
        Database._ensure_columns(c, "portfolio_positions", {
            "position_risk_usd": "REAL DEFAULT 0",
            "risk_provenance_json": "TEXT NOT NULL DEFAULT '{}'",
            "risk_as_of": "TEXT",
        })
        Database._ensure_columns(c, "paper_orders", {
            "risk_per_share": "REAL NOT NULL DEFAULT 0",
            "risk_provenance_json": "TEXT NOT NULL DEFAULT '{}'",
        })
        c.execute("""UPDATE portfolio_positions
            SET risk_provenance_json='{"status":"UNKNOWN_LEGACY"}'
            WHERE status='OPEN' AND quantity>0
              AND (risk_provenance_json IS NULL OR risk_provenance_json IN ('','{}'))""")
        c.execute("""UPDATE paper_orders
            SET risk_provenance_json='{"status":"UNKNOWN_LEGACY"}'
            WHERE status IN ('PENDING','TRIGGERED','REVALIDATING')
              AND (risk_provenance_json IS NULL OR risk_provenance_json IN ('','{}'))""")
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            22, now_iso(), "v2.2 PAPER position and pending-order risk provenance"))

    @staticmethod
    def _migrate_v23_discovery(c: sqlite3.Connection) -> None:
        """Additive Discovery source-of-truth tables; never mutates PAPER state."""
        c.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_runs (
            discovery_run_id TEXT PRIMARY KEY, request_id TEXT, mode TEXT NOT NULL,
            requested_sector TEXT, intensity TEXT NOT NULL, as_of TEXT NOT NULL,
            rule_version TEXT NOT NULL, feature_version TEXT NOT NULL, code_sha TEXT,
            universe_snapshot_id TEXT, status TEXT NOT NULL, certification_status TEXT NOT NULL,
            coverage_pct REAL, started_at TEXT NOT NULL, finished_at TEXT,
            error_code TEXT, payload_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS universe_securities (
            universe_snapshot_id TEXT NOT NULL, security_id TEXT NOT NULL, ticker TEXT NOT NULL,
            payload_json TEXT NOT NULL, PRIMARY KEY(universe_snapshot_id, security_id)
        );
        CREATE TABLE IF NOT EXISTS universe_snapshots (
            universe_snapshot_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, mode TEXT NOT NULL,
            raw_count INTEGER NOT NULL, accepted_count INTEGER NOT NULL, rejected_json TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS daily_bars (
            ticker TEXT NOT NULL, session_date TEXT NOT NULL, open REAL, high REAL, low REAL,
            close REAL, adjusted_close REAL, volume INTEGER, source TEXT NOT NULL,
            observed_at TEXT NOT NULL, ingested_at TEXT NOT NULL, quality_status TEXT NOT NULL,
            PRIMARY KEY(ticker, session_date, source)
        );
        CREATE TABLE IF NOT EXISTS sector_snapshots (
            discovery_run_id TEXT NOT NULL, sector_or_theme TEXT NOT NULL,
            feature_json TEXT NOT NULL, rotation_score REAL, rotation_phase TEXT NOT NULL,
            coverage_pct REAL, PRIMARY KEY(discovery_run_id, sector_or_theme)
        );
        CREATE TABLE IF NOT EXISTS candidate_feature_snapshots (
            discovery_run_id TEXT NOT NULL, ticker TEXT NOT NULL, as_of TEXT NOT NULL,
            feature_version TEXT NOT NULL, payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
            PRIMARY KEY(discovery_run_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS scanner_hits (
            discovery_run_id TEXT NOT NULL, ticker TEXT NOT NULL, scanner TEXT NOT NULL,
            scanner_version TEXT NOT NULL, hit INTEGER NOT NULL, strength REAL NOT NULL,
            reason_codes_json TEXT NOT NULL, signal_families_json TEXT NOT NULL,
            unknown_fields_json TEXT NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(discovery_run_id, ticker, scanner)
        );
        CREATE TABLE IF NOT EXISTS catalyst_events (
            event_id TEXT PRIMARY KEY, ticker TEXT NOT NULL, event_type TEXT NOT NULL,
            event_at TEXT, first_seen_at TEXT, expiry_at TEXT, half_life_days REAL,
            source_evidence_id TEXT, materiality TEXT, status TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS discovery_candidates (
            discovery_run_id TEXT NOT NULL, ticker TEXT NOT NULL, stage TEXT NOT NULL,
            eligibility TEXT NOT NULL, discovery_bucket TEXT NOT NULL, composite_score REAL,
            reason_codes_json TEXT NOT NULL, unknown_fields_json TEXT NOT NULL,
            risk_flags_json TEXT NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(discovery_run_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS discovery_packets (
            discovery_run_id TEXT PRIMARY KEY, packet_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS discovery_analysis_links (
            discovery_run_id TEXT NOT NULL, ticker TEXT NOT NULL, analysis_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(discovery_run_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS backtest_runs (
            backtest_run_id TEXT PRIMARY KEY, rule_version TEXT NOT NULL,
            feature_version TEXT NOT NULL, as_of_start TEXT, as_of_end TEXT,
            status TEXT NOT NULL, survivorship_bias_risk TEXT NOT NULL, payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS backtest_results (
            backtest_run_id TEXT NOT NULL, ticker TEXT NOT NULL, as_of TEXT NOT NULL,
            horizon_days INTEGER NOT NULL, payload_json TEXT NOT NULL,
            PRIMARY KEY(backtest_run_id, ticker, as_of, horizon_days)
        );
        CREATE INDEX IF NOT EXISTS idx_discovery_runs_status ON discovery_runs(status, started_at);
        CREATE INDEX IF NOT EXISTS idx_discovery_candidates_bucket ON discovery_candidates(discovery_run_id, discovery_bucket);
        CREATE INDEX IF NOT EXISTS idx_daily_bars_cutoff ON daily_bars(ticker, session_date);
        """)
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            23, now_iso(), "v2.3 additive Discovery universe, cache, screening, audit and backtest schema"))

    @staticmethod
    def _migrate_v24_discovery_mvp(c: sqlite3.Connection) -> None:
        Database._ensure_columns(c, "discovery_runs", {
            "identity_coverage_pct": "REAL DEFAULT 0",
            "feature_coverage_pct": "REAL DEFAULT 0",
            "sector_coverage_pct": "REAL DEFAULT 0",
            "fundamental_enrichment_coverage_pct": "REAL DEFAULT 0",
            "capital_preflight_coverage_pct": "REAL DEFAULT 0",
            "final_selection": "TEXT DEFAULT 'NONE'",
            "final_selection_status": "TEXT DEFAULT 'NONE'",
            "final_selection_reason_codes_json": "TEXT NOT NULL DEFAULT '[]'",
            "budget_json": "TEXT NOT NULL DEFAULT '{}'",
        })
        Database._ensure_columns(c, "discovery_candidates", {
            "screen_layer": "TEXT DEFAULT 'MARKET'",
            "preflight_status": "TEXT DEFAULT 'NOT_FETCHED'",
            "analysis_status": "TEXT DEFAULT 'NOT_REQUESTED'",
            "certification_status": "TEXT DEFAULT 'NOT_APPLICABLE'",
        })
        c.executescript("""
        CREATE TABLE IF NOT EXISTS discovery_provider_calls (
            discovery_run_id TEXT NOT NULL, provider TEXT NOT NULL, operation TEXT NOT NULL,
            batch_no INTEGER NOT NULL DEFAULT 0, requested_count INTEGER NOT NULL DEFAULT 0,
            returned_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
            cache_hits INTEGER NOT NULL DEFAULT 0, started_at TEXT NOT NULL, finished_at TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(discovery_run_id, provider, operation, batch_no)
        );
        CREATE TABLE IF NOT EXISTS periodic_readiness (
            run_id TEXT NOT NULL, ticker TEXT NOT NULL, evidence_id TEXT NOT NULL,
            document_type TEXT NOT NULL, accession TEXT, readiness_state TEXT NOT NULL,
            reason_codes_json TEXT NOT NULL, numeric_claims_status TEXT NOT NULL,
            source_hash TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY(run_id, evidence_id)
        );
        CREATE TABLE IF NOT EXISTS offering_semantic_events (
            run_id TEXT NOT NULL, ticker TEXT NOT NULL, evidence_id TEXT NOT NULL,
            offering_type TEXT NOT NULL, status TEXT NOT NULL, economic_effect TEXT,
            new_share_creation_possible INTEGER, issuer_receives_proceeds INTEGER,
            remaining_capacity REAL, used_amount REAL, confidence INTEGER NOT NULL DEFAULT 0,
            reason_codes_json TEXT NOT NULL, source_accession TEXT, filed_at TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(run_id, evidence_id)
        );
        CREATE INDEX IF NOT EXISTS idx_discovery_provider_calls_run ON discovery_provider_calls(discovery_run_id);
        CREATE INDEX IF NOT EXISTS idx_periodic_readiness_ticker ON periodic_readiness(ticker, readiness_state);
        """)
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            24, now_iso(), "v2.4 Discovery bootstrap, enrichment, readiness and final-selection provenance"))

    @staticmethod
    def _migrate_v25_discovery_audit(c: sqlite3.Connection) -> None:
        """Additive v3 Discovery provenance; never rewrite prior migrations or PAPER."""
        Database._ensure_columns(c, "discovery_runs", {
            "market_scan_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "enrichment_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "deep_handoff_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "actual_llm_calls": "INTEGER NOT NULL DEFAULT 0",
            "actual_input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "actual_output_tokens": "INTEGER NOT NULL DEFAULT 0",
            "actual_cost_usd": "REAL NOT NULL DEFAULT 0",
        })
        Database._ensure_columns(c, "discovery_candidates", {
            "preliminary_priority_score": "REAL",
            "size_bucket": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "fundamental_rank": "INTEGER",
            "capital_preflight_rank": "INTEGER",
            "promotion_status": "TEXT NOT NULL DEFAULT 'NOT_REQUESTED'",
            "promotion_reason_codes_json": "TEXT NOT NULL DEFAULT '[]'",
        })
        Database._ensure_columns(c, "discovery_analysis_links", {
            "promotion_requested_at": "TEXT",
            "analysis_started_at": "TEXT",
            "analysis_finished_at": "TEXT",
            "actual_llm_calls": "INTEGER NOT NULL DEFAULT 0",
            "actual_cost_usd": "REAL NOT NULL DEFAULT 0",
        })
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            25, now_iso(), "v2.5 Discovery MVP financial, fairness, promotion and usage provenance"))

    @staticmethod
    def _migrate_v26_discovery_funnel(c: sqlite3.Connection) -> None:
        """Persist the distinction between preflight coverage and scope."""
        Database._ensure_columns(c, "discovery_runs", {
            "capital_preflight_scope_pct": "REAL NOT NULL DEFAULT 0",
        })
        c.execute("INSERT OR IGNORE INTO schema_migrations VALUES(?,?,?)", (
            26, now_iso(), "v2.6 Discovery staged fuel, funnel and preflight scope semantics"))

    def start_run(self, run_id: str, ticker: str, mode: str, request_id: str = "",
                  analysis_intensity: str = "NORMAL") -> str:
        timestamp = now_iso()
        with self.connect() as c:
            c.execute("""INSERT OR IGNORE INTO analysis_runs
                (run_id,ticker,requested_at,started_at,status,mode,request_id,analysis_intensity,
                 debate_status,cancellation_status,execution_status,analysis_status,
                 certification_status,side_effect_status,certified_action)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, ticker, timestamp, timestamp, "RUNNING", mode, request_id,
                 analysis_intensity, "IN_PROGRESS", "ACTIVE", "RUNNING", "IN_PROGRESS",
                 None, "NOT_AUTHORIZED", "NO_CERTIFIED_ACTION"))
            row = c.execute("SELECT requested_at FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()
            if request_id:
                c.execute("UPDATE llm_calls SET run_id=?,ticker=? WHERE request_id=? AND role='command_parser'",
                          (run_id, ticker, request_id))
        return row[0]

    def complete_run(self, run_id: str, decision: InvestmentDecision, research: Any,
                     critic: Any, risk_rule_version: str) -> None:
        with self.connect() as c:
            c.execute("""UPDATE analysis_runs SET finished_at=?, status='SUCCESS',
                research_provider=?, research_model=?, critic_provider=?, critic_model=?,
                prompt_version=?, risk_rule_version=?, final_decision=?, final_confidence=?
                WHERE run_id=?""", (now_iso(), research.provider, research.model,
                critic.provider, critic.model, f"{research.prompt_version}|{critic.prompt_version}",
                risk_rule_version, decision.decision, decision.confidence, run_id))

    def fail_run(self, run_id: str, error_message: str, status: str = "SYSTEM_ERROR") -> None:
        with self.connect() as c:
            c.execute("""UPDATE analysis_runs SET finished_at=?, status=?, error_message=?,
                execution_status='FAILED',analysis_status='FAILED',certification_status='FAILED',
                side_effect_status='WITHHELD',certified_action='NO_CERTIFIED_ACTION',
                certification_reason=? WHERE run_id=?""",
                (now_iso(), status, error_message, status, run_id))

    def save_certification(self, result: CertificationResult) -> None:
        with self.connect() as c:
            self._upsert_certification(c, result)

    @staticmethod
    def _upsert_certification(c: sqlite3.Connection, result: CertificationResult) -> None:
        payload = asdict(result)
        c.execute("""INSERT INTO certification_records(
                run_id,execution_status,analysis_status,certification_status,side_effect_status,
                certified_action,reason_codes_json,required_data_failures_json,
                important_data_warnings_json,decision_confidence,trade_plan_status,
                position_sizing_status,payload_json,evaluated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                execution_status=excluded.execution_status,
                analysis_status=excluded.analysis_status,
                certification_status=excluded.certification_status,
                side_effect_status=excluded.side_effect_status,
                certified_action=excluded.certified_action,
                reason_codes_json=excluded.reason_codes_json,
                required_data_failures_json=excluded.required_data_failures_json,
                important_data_warnings_json=excluded.important_data_warnings_json,
                decision_confidence=excluded.decision_confidence,
                trade_plan_status=excluded.trade_plan_status,
                position_sizing_status=excluded.position_sizing_status,
                payload_json=excluded.payload_json,evaluated_at=excluded.evaluated_at""", (
                result.run_id, result.execution_status, result.analysis_status,
                result.certification_status, result.side_effect_status, result.action,
                json.dumps(result.reason_codes), json.dumps(result.required_data_failures),
                json.dumps(result.important_data_warnings), result.decision_confidence,
                result.trade_plan_status, result.position_sizing_status,
                json.dumps(payload, ensure_ascii=False), result.evaluated_at))
        c.execute("""UPDATE analysis_runs SET execution_status=?,analysis_status=?,
            certification_status=?,side_effect_status=?,certified_action=?,
            certification_reason=? WHERE run_id=?""", (
            result.execution_status, result.analysis_status, result.certification_status,
            result.side_effect_status, result.action,
            ",".join(result.reason_codes), result.run_id))

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        with self.connect() as c:
            return c.execute("SELECT * FROM analysis_runs WHERE run_id=?", (run_id,)).fetchone()

    def log(self, run_id: str, level: str, event: str, payload: Any = None) -> None:
        with self.connect() as c:
            c.execute("INSERT INTO system_logs(run_id, level, event, payload_json) VALUES(?,?,?,?)",
                      (run_id, level, event, json.dumps(payload, ensure_ascii=False, default=str)))

    def mark_discord_message(self, message_id: str, user_id: str,
                             channel_id: str, request_id: str) -> bool:
        with self.connect() as c:
            cursor = c.execute("""INSERT OR IGNORE INTO processed_discord_messages
                VALUES(?,?,?,?,?)""", (message_id, user_id, channel_id, now_iso(), request_id))
            return cursor.rowcount == 1

    def save_user_request(self, request: Any, run_id: str = "") -> None:
        payload = asdict(request) if is_dataclass(request) else dict(request)
        with self.connect() as c:
            c.execute("""INSERT INTO user_requests
                (request_id,discord_message_id,discord_user_id,received_at,original_text,
                 intent,tickers_json,time_horizon,focus_json,comparison_mode,parser_type,
                 parser_confidence,missing_fields_json,status,run_id,updated_at,
                 analysis_intensity,min_debate_rounds,max_debate_rounds,intensity_explicit,
                 reasoning_profile,evidence_depth,max_evidence_refreshes,
                 consensus_stress_test_required)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(request_id) DO UPDATE SET
                 status=excluded.status,run_id=excluded.run_id,updated_at=excluded.updated_at,
                 missing_fields_json=excluded.missing_fields_json,
                 analysis_intensity=excluded.analysis_intensity,
                 min_debate_rounds=excluded.min_debate_rounds,
                 max_debate_rounds=excluded.max_debate_rounds""", (
                payload["request_id"], payload["discord_message_id"], payload["discord_user_id"],
                payload["received_at"], payload["original_text"], payload["intent"],
                json.dumps(payload.get("tickers", [])), payload.get("time_horizon", ""),
                json.dumps(payload.get("focus", []), ensure_ascii=False),
                payload.get("comparison_mode", "NONE"), payload.get("parser_type", ""),
                float(payload.get("parser_confidence", 0)),
                json.dumps(payload.get("missing_fields", []), ensure_ascii=False),
                payload.get("status", "PARSED"), run_id, now_iso(),
                payload.get("analysis_intensity", "NORMAL"),
                int(payload.get("min_debate_rounds", 3)), int(payload.get("max_debate_rounds", 5)),
                int(bool(payload.get("intensity_explicit", False))),
                payload.get("reasoning_profile", "high"), payload.get("evidence_depth", "STANDARD"),
                int(payload.get("max_evidence_refreshes", 2)),
                int(bool(payload.get("consensus_stress_test_required", False)))))

    def update_request_status(self, request_id: str, status: str, run_id: str = "") -> None:
        with self.connect() as c:
            c.execute("""UPDATE user_requests SET status=?,run_id=CASE WHEN ?='' THEN run_id ELSE ? END,
                updated_at=? WHERE request_id=?""", (status, run_id, run_id, now_iso(), request_id))

    def save_pending_clarification(self, request: Any, channel_id: str, expires_at: str) -> None:
        payload = asdict(request) if is_dataclass(request) else dict(request)
        with self.connect() as c:
            c.execute("""INSERT INTO pending_clarifications VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(pending_request_id) DO UPDATE SET
                missing_fields_json=excluded.missing_fields_json,expires_at=excluded.expires_at,
                payload_json=excluded.payload_json,status=excluded.status""", (
                payload["request_id"], payload["discord_user_id"], channel_id,
                payload["original_text"], json.dumps(payload.get("missing_fields", [])),
                expires_at, json.dumps(payload, ensure_ascii=False), "WAITING", now_iso()))

    def get_pending_clarification(self, user_id: str, channel_id: str, now_value: str) -> sqlite3.Row | None:
        with self.connect() as c:
            return c.execute("""SELECT * FROM pending_clarifications
                WHERE discord_user_id=? AND channel_id=? AND status='WAITING' AND expires_at>?
                ORDER BY created_at DESC LIMIT 1""", (user_id, channel_id, now_value)).fetchone()

    def resolve_pending_clarification(self, pending_request_id: str) -> None:
        with self.connect() as c:
            c.execute("UPDATE pending_clarifications SET status='RESOLVED' WHERE pending_request_id=?",
                      (pending_request_id,))

    def save_stage_event(self, run_id: str, stage: str, status: str,
                         payload: Any = None) -> None:
        with self.connect() as c:
            c.execute("INSERT INTO run_stage_events VALUES(NULL,?,?,?,?,?)", (
                run_id, stage, status, now_iso(),
                json.dumps(payload, ensure_ascii=False, default=str)))

    def active_runs(self) -> list[sqlite3.Row]:
        with self.connect() as c:
            return c.execute("""SELECT run_id,ticker,status,started_at,analysis_intensity,
                debate_status,round_count FROM analysis_runs
                WHERE status IN ('RUNNING','QUEUED') ORDER BY requested_at""").fetchall()

    def queued_requests(self) -> list[sqlite3.Row]:
        with self.connect() as c:
            return c.execute("""SELECT request_id,intent,tickers_json,status,updated_at
                FROM user_requests WHERE status='QUEUED' ORDER BY updated_at""").fetchall()

    def save_snapshot(self, run_id: str, snapshot: MarketSnapshot) -> None:
        self._save_payload("market_snapshots", (run_id, snapshot.ticker, snapshot.timestamp,
            json.dumps(asdict(snapshot), ensure_ascii=False)), "run_id,ticker,timestamp,payload_json")

    def save_evidence(self, items: list[EvidenceItem], run_id: str = "") -> None:
        with self.connect() as c:
            for item in items:
                c.execute("""INSERT INTO evidence_metadata VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                    published_at=excluded.published_at,evidence_grade=excluded.evidence_grade,
                    payload_json=excluded.payload_json""",
                    (item.evidence_id, item.ticker, item.source_type, item.document_type,
                     item.published_at, item.evidence_grade, json.dumps(asdict(item), ensure_ascii=False)))
                if run_id:
                    payload = json.dumps(asdict(item), ensure_ascii=False)
                    c.execute("""INSERT INTO raw_filings(
                        raw_filing_id,run_id,evidence_id,ticker,accession,document_type,source_url,
                        content_hash,lifecycle_status,payload_json,collected_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,evidence_id) DO UPDATE SET
                        content_hash=excluded.content_hash,lifecycle_status=excluded.lifecycle_status,
                        payload_json=excluded.payload_json""", (
                        f"RAW_{run_id}_{item.evidence_id}", run_id, item.evidence_id, item.ticker,
                        item.accession, item.document_type, item.source_url,
                        item.raw_document_hash or item.content_hash, item.lifecycle_status,
                        payload, item.ingested_at))
                    c.execute("""INSERT INTO evidence_processing_receipts(
                        run_id,evidence_id,content_hash,collected_at,parsed_at,lifecycle_status,
                        exhibits_resolved,validated_at,ready_for_analysis_at)
                        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(run_id,evidence_id) DO UPDATE SET
                        content_hash=excluded.content_hash,parsed_at=excluded.parsed_at,
                        lifecycle_status=excluded.lifecycle_status,
                        exhibits_resolved=excluded.exhibits_resolved,
                        validated_at=excluded.validated_at,
                        ready_for_analysis_at=excluded.ready_for_analysis_at""", (
                        run_id, item.evidence_id, item.raw_document_hash or item.content_hash,
                        item.ingested_at, item.parsed_at, item.lifecycle_status,
                        int(item.exhibits_resolved), item.validated_at, item.ready_for_analysis_at))

    def save_company_facts(self, ticker: str, rows: list[dict[str, Any]]) -> None:
        with self.connect() as c:
            for row in rows:
                fingerprint = "|".join(str(row.get(name) or "") for name in
                    ("taxonomy", "concept", "unit", "start", "end", "filed", "accn", "value"))
                import hashlib
                fact_id = f"FACT_{ticker}_{hashlib.sha256(fingerprint.encode()).hexdigest()[:20]}"
                c.execute("""INSERT OR IGNORE INTO company_facts
                    (fact_id,ticker,taxonomy,concept,unit,form,fy,fp,start,end,filed,accn,value,
                     period_type,payload_json,ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (fact_id, ticker, row.get("taxonomy"), row.get("concept"), row.get("unit"),
                     row.get("form"), row.get("fy"), row.get("fp"), row.get("start"), row.get("end"),
                     row.get("filed"), row.get("accn"), row.get("value"), row.get("period_type"),
                      json.dumps(row, ensure_ascii=False), now_iso()))

    def save_company_fact_bundle(self, run_id: str, ticker: str,
                                 bundle: dict[str, Any]) -> None:
        raw_payload = bundle.get("raw_companyfacts_payload") or {}
        raw_json = json.dumps(raw_payload, ensure_ascii=False, sort_keys=True)
        raw_id = f"CFRAW_{run_id}_{ticker}"
        timestamp = now_iso()
        with self.connect() as c:
            c.execute("""INSERT INTO raw_companyfacts(
                raw_companyfacts_id,run_id,ticker,cik,content_hash,payload_json,fetched_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(raw_companyfacts_id) DO NOTHING""", (
                raw_id, run_id, ticker, bundle.get("cik", ""),
                hashlib.sha256(raw_json.encode()).hexdigest(), raw_json, timestamp))
            for row in bundle.get("normalized_facts", []):
                source_fact_id = str(row.get("fact_id") or "")
                if not source_fact_id:
                    continue
                fact_id = f"{run_id}:{source_fact_id}"
                payload_json = json.dumps(row, ensure_ascii=False, sort_keys=True)
                c.execute("""INSERT INTO raw_xbrl_facts(
                    fact_id,run_id,ticker,taxonomy,concept,unit,payload_json,ingested_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(fact_id) DO NOTHING""", (
                    fact_id, run_id, ticker, row.get("taxonomy"), row.get("concept"),
                    row.get("unit"), payload_json, timestamp))
                duration_days = None
                if row.get("start") and row.get("end"):
                    try:
                        duration_days = (datetime.fromisoformat(row["end"]) -
                                         datetime.fromisoformat(row["start"])).days
                    except (TypeError, ValueError):
                        duration_days = None
                c.execute("""INSERT INTO normalized_facts(
                    normalized_fact_id,raw_fact_id,run_id,ticker,concept,unit,fact_nature,
                    form,fy,fp,start,end,duration_days,filed,accn,frame,value,status,
                    payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(normalized_fact_id) DO NOTHING""", (
                    f"NORM_{run_id}_{source_fact_id}", fact_id, run_id, ticker, row.get("concept"),
                    row.get("unit"), "DURATION_FACT" if row.get("period_type") == "DURATION"
                    else "INSTANT_FACT", row.get("form"), row.get("fy"), row.get("fp"),
                    row.get("start"), row.get("end"), duration_days, row.get("filed"),
                    row.get("accn"), row.get("frame"), row.get("value"), "KNOWN",
                    payload_json, timestamp))
            for name, metric in (bundle.get("derived") or {}).items():
                if isinstance(metric, dict):
                    value, source_ids = metric.get("value"), metric.get("source_fact_ids", [])
                    status, formula = metric.get("status", "KNOWN"), metric.get("formula", "")
                    method, as_of = metric.get("method", "DETERMINISTIC_DERIVATION"), metric.get("as_of", "")
                else:
                    value, source_ids, formula, method, as_of = metric, [], "", "LEGACY_DERIVATION", ""
                    status = "KNOWN" if metric is not None else "UNKNOWN_NOT_AVAILABLE"
                metric_payload = {"value": value, "status": status, "formula": formula,
                                  "source_fact_ids": source_ids, "method": method, "as_of": as_of}
                c.execute("""INSERT INTO derived_metrics(
                    derived_metric_id,run_id,ticker,metric_name,value,status,formula,
                    source_fact_ids_json,as_of,method,payload_json,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(derived_metric_id) DO NOTHING""", (
                    f"DERIVED_{run_id}_{name}", run_id, ticker, name, value, status, formula,
                    json.dumps(source_ids), as_of, method,
                    json.dumps(metric_payload, ensure_ascii=False), timestamp))

    def save_capital_structure(self, run_id: str, ticker: str, payload: dict[str, Any]) -> None:
        snapshot_id = f"CAP_{run_id}"
        with self.connect() as c:
            c.execute("INSERT INTO capital_structure_snapshots VALUES(?,?,?,?,?,?)",
                      (snapshot_id, run_id, ticker, payload.get("as_of", ""),
                       json.dumps(payload, ensure_ascii=False), now_iso()))

    def save_evidence_request(self, run_id: str, request: Any,
                              status: str = "OPEN", result: Any = None) -> None:
        payload = asdict(request) if is_dataclass(request) else dict(request)
        with self.connect() as c:
            c.execute("""INSERT INTO evidence_requests
                (request_id,run_id,issue_id,question,severity,source_scope_json,target_forms_json,
                 keywords_json,date_from,date_to,company_fact_targets_json,must_answer,
                 requesting_role,requested_round,status,result_json,created_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(request_id) DO UPDATE SET status=excluded.status,
                result_json=COALESCE(excluded.result_json,evidence_requests.result_json),
                completed_at=excluded.completed_at""", (
                payload["request_id"], run_id, payload.get("issue_id", ""), payload["question"],
                payload.get("severity", "HIGH"), json.dumps(payload.get("source_scope", [])),
                json.dumps(payload.get("target_forms", [])), json.dumps(payload.get("keywords", [])),
                payload.get("date_from", ""), payload.get("date_to", ""),
                json.dumps(payload.get("company_fact_targets", [])), int(payload.get("must_answer", False)),
                payload.get("requesting_role", "CRITIC"), int(payload.get("requested_round", 1)),
                status, json.dumps(result, ensure_ascii=False, default=str) if result is not None else None,
                now_iso(), now_iso() if status in {"COLLECTED", "REVIEW_REQUIRED", "RESOLVED", "FAILED"}
                else None))

    def mark_evidence_seen(self, run_id: str, evidence_ids: list[str], role: str,
                           round_no: int) -> None:
        if not evidence_ids:
            return
        role = role.upper()
        if role not in {"RESEARCH", "CRITIC", "CHAIRMAN"}:
            raise ValueError(f"unsupported evidence receipt role: {role}")
        with self.connect() as c:
            timestamp = now_iso()
            for evidence_id in dict.fromkeys(evidence_ids):
                if role == "RESEARCH":
                    c.execute("""UPDATE evidence_processing_receipts SET
                        selected_for_context_at=COALESCE(selected_for_context_at,?),
                        research_seen_round=CASE WHEN research_seen_round IS NULL
                            THEN ? ELSE MIN(research_seen_round,?) END
                        WHERE run_id=? AND evidence_id=?""",
                        (timestamp, round_no, round_no, run_id, evidence_id))
                elif role == "CRITIC":
                    c.execute("""UPDATE evidence_processing_receipts SET
                        selected_for_context_at=COALESCE(selected_for_context_at,?),
                        critic_seen_round=CASE WHEN critic_seen_round IS NULL
                            THEN ? ELSE MIN(critic_seen_round,?) END
                        WHERE run_id=? AND evidence_id=?""",
                        (timestamp, round_no, round_no, run_id, evidence_id))
                else:
                    c.execute("""UPDATE evidence_processing_receipts SET chairman_seen=1,
                        selected_for_context_at=COALESCE(selected_for_context_at,?)
                        WHERE run_id=? AND evidence_id=?""", (timestamp, run_id, evidence_id))
            requests = c.execute("""SELECT request_id,result_json,reviewed_by_research,
                reviewed_by_critic FROM evidence_requests WHERE run_id=?
                AND status='REVIEW_REQUIRED'""", (run_id,)).fetchall()
            seen = set(evidence_ids)
            for request in requests:
                resolved_ids = set(json.loads(request["result_json"] or "[]"))
                if not seen.intersection(resolved_ids):
                    continue
                research_seen = bool(request["reviewed_by_research"]) or role == "RESEARCH"
                critic_seen = bool(request["reviewed_by_critic"]) or role == "CRITIC"
                status = "RESOLVED" if research_seen and critic_seen and resolved_ids else "REVIEW_REQUIRED"
                c.execute("""UPDATE evidence_requests SET reviewed_by_research=?,
                    reviewed_by_critic=?,status=?,resolved_by_evidence_ids_json=?,
                    completed_at=CASE WHEN ?='RESOLVED' THEN ? ELSE completed_at END
                    WHERE request_id=?""", (
                    int(research_seen), int(critic_seen), status,
                    json.dumps(sorted(resolved_ids)), status, timestamp, request["request_id"]))

    def unresolved_must_answer_count(self, run_id: str) -> int:
        with self.connect() as c:
            row = c.execute("""SELECT COUNT(*) FROM evidence_requests
                WHERE run_id=? AND must_answer=1
                  AND COALESCE(status,'OPEN')!='RESOLVED'""", (run_id,)).fetchone()
            return int(row[0])

    def save_evidence_conflicts(self, run_id: str, conflicts: list[dict[str, Any]]) -> None:
        import hashlib
        with self.connect() as c:
            for item in conflicts:
                raw = f"{run_id}|{item.get('topic')}|{item.get('evidence_ids')}"
                conflict_id = f"CONFLICT_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
                c.execute("""INSERT INTO evidence_conflicts
                    (conflict_id,run_id,topic,evidence_ids_json,description,severity,status,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""", (conflict_id, run_id, item.get("topic", ""),
                    json.dumps(item.get("evidence_ids", [])), item.get("description", ""),
                    item.get("severity", "MEDIUM"), item.get("status", "OPEN"), now_iso()))
                c.execute("""INSERT INTO evidence_items VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(evidence_id) DO UPDATE SET
                    published_at=excluded.published_at,evidence_grade=excluded.evidence_grade,
                    payload_json=excluded.payload_json""",
                    (item.evidence_id, item.ticker, item.source_type, item.document_type,
                     item.published_at, item.evidence_grade, json.dumps(asdict(item), ensure_ascii=False)))

    def save_output(self, table: str, run_id: str, ticker: str, value: Any) -> None:
        if table not in OUTPUT_TABLES:
            raise ValueError(f"unsupported output table: {table}")
        with self.connect() as c:
            c.execute(f"""INSERT INTO {table}(run_id,ticker,payload_json) VALUES(?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET ticker=excluded.ticker,
                payload_json=excluded.payload_json""",
                      (run_id, ticker, json.dumps(asdict(value) if is_dataclass(value) else value,
                                                  ensure_ascii=False, default=str)))

    def save_stage_output(self, run_id: str, ticker: str, role: str, round_no: int,
                          phase: str, value: Any) -> None:
        payload = asdict(value) if is_dataclass(value) else value
        with self.connect() as c:
            c.execute("""INSERT INTO stage_outputs
                (run_id,ticker,role,round_no,phase,payload_json,created_at)
                VALUES(?,?,?,?,?,?,?)""", (run_id, ticker, role, int(round_no), phase,
                json.dumps(payload, ensure_ascii=False, default=str), now_iso()))

    def save_debate_round(self, state: Any, research: Any, critic: Any,
                          consensus: dict[str, Any], phase: str,
                          prompt_chars: int = 0) -> None:
        state_payload = asdict(state) if is_dataclass(state) else dict(state)
        research_payload = asdict(research) if is_dataclass(research) else dict(research)
        critic_payload = asdict(critic) if is_dataclass(critic) else dict(critic)
        with self.connect() as c:
            c.execute("""INSERT INTO debate_rounds
                (run_id,round_no,phase,research_json,critic_json,consensus_json,prompt_chars,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (state_payload["run_id"], state_payload["round_no"], phase,
                json.dumps(research_payload, ensure_ascii=False),
                json.dumps(critic_payload, ensure_ascii=False),
                json.dumps(consensus, ensure_ascii=False), int(prompt_chars), now_iso()))
            c.execute("""INSERT INTO debate_states
                (run_id,status,intensity,min_rounds,max_rounds,current_round,research_stance,
                 critic_stance,research_confidence,critic_confidence,provisional_consensus,
                 stress_test_completed,final_consensus,deadlock_reason,payload_json,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                status=excluded.status,current_round=excluded.current_round,
                research_stance=excluded.research_stance,critic_stance=excluded.critic_stance,
                research_confidence=excluded.research_confidence,
                critic_confidence=excluded.critic_confidence,
                provisional_consensus=excluded.provisional_consensus,
                stress_test_completed=excluded.stress_test_completed,
                final_consensus=excluded.final_consensus,deadlock_reason=excluded.deadlock_reason,
                payload_json=excluded.payload_json,updated_at=excluded.updated_at""", (
                state_payload["run_id"], state_payload["status"],
                state_payload.get("analysis_intensity", "UNKNOWN"), state_payload["min_rounds"],
                state_payload["max_rounds"], state_payload["round_no"],
                state_payload["research_stance"], state_payload["critic_stance"],
                state_payload["research_confidence"], state_payload["critic_confidence"],
                int(state_payload["provisional_consensus"]), int(state_payload["stress_test_completed"]),
                int(state_payload["final_consensus"]), state_payload["deadlock_reason"],
                json.dumps(state_payload, ensure_ascii=False), now_iso()))
            for issue in state_payload.get("issue_ledger", []):
                c.execute("""INSERT INTO debate_issue_instances(
                    issue_instance_id,run_id,semantic_issue_key,parent_issue_id,topic,severity,
                    materiality,research_position,critic_position,supporting_evidence_json,
                    opposing_evidence_json,status,resolution_basis,opened_round,last_updated_round,
                    updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(run_id,semantic_issue_key) DO UPDATE SET
                    topic=excluded.topic,severity=excluded.severity,materiality=excluded.materiality,
                    research_position=excluded.research_position,
                    critic_position=excluded.critic_position,
                    supporting_evidence_json=excluded.supporting_evidence_json,
                    opposing_evidence_json=excluded.opposing_evidence_json,status=excluded.status,
                    resolution_basis=excluded.resolution_basis,
                    last_updated_round=excluded.last_updated_round,updated_at=excluded.updated_at""", (
                    issue.get("issue_instance_id") or issue["issue_id"], state_payload["run_id"],
                    issue.get("semantic_issue_key") or issue["issue_id"],
                    issue.get("parent_issue_id", ""), issue["topic"], issue["severity"],
                    issue.get("materiality", "MATERIAL"), issue.get("research_position", ""),
                    issue.get("critic_position", ""),
                    json.dumps(issue.get("supporting_evidence_ids", [])),
                    json.dumps(issue.get("opposing_evidence_ids", [])), issue["status"],
                    issue.get("resolution_basis", ""), issue.get("opened_round", 1),
                    issue.get("last_updated_round", state_payload["round_no"]), now_iso()))
            for gain in state_payload.get("round_information_gain", []):
                c.execute("""INSERT INTO debate_round_information_gain(
                    run_id,round_no,new_material_evidence,material_thesis_change,
                    material_issue_closed,created_at) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(run_id,round_no) DO UPDATE SET
                    new_material_evidence=excluded.new_material_evidence,
                    material_thesis_change=excluded.material_thesis_change,
                    material_issue_closed=excluded.material_issue_closed""", (
                    state_payload["run_id"], gain.get("round", 0),
                    int(gain.get("new_material_evidence", 0)),
                    int(gain.get("material_thesis_change", 0)),
                    int(gain.get("material_issue_closed", 0)), now_iso()))
            for item in state_payload.get("thesis_change_log", []):
                raw_change = "|".join(str(value) for value in (
                    state_payload["run_id"], item.get("round", 0), item.get("role", ""),
                    item.get("from_decision", ""), item.get("to_decision", ""),
                    item.get("from_confidence", 0), item.get("to_confidence", 0),
                    item.get("reason", "")))
                change_id = f"CHANGE_{hashlib.sha256(raw_change.encode()).hexdigest()[:24]}"
                c.execute("""INSERT INTO thesis_change_events(
                    change_id,run_id,round_no,role,from_decision,to_decision,from_confidence,
                    to_confidence,reason,evidence_ids_json,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(change_id) DO NOTHING""", (
                    change_id, state_payload["run_id"], item.get("round", 0), item.get("role", ""),
                    item.get("from_decision", ""), item.get("to_decision", ""),
                    item.get("from_confidence", 0), item.get("to_confidence", 0),
                    item.get("reason", ""), json.dumps(item.get("evidence_ids", [])), now_iso()))

    def save_decision(self, decision: InvestmentDecision) -> None:
        with self.connect() as c:
            c.execute("INSERT INTO investment_decisions VALUES(?,?,?,?,?,?)",
                (decision.run_id, decision.ticker, decision.timestamp, decision.decision,
                 decision.confidence, json.dumps(asdict(decision), ensure_ascii=False)))
            c.execute("INSERT INTO final_decisions VALUES(?,?,?,?,?,?)",
                (decision.run_id, decision.ticker, decision.timestamp, decision.decision,
                 decision.confidence, json.dumps(asdict(decision), ensure_ascii=False)))

    def save_manifest(self, manifest: Any) -> None:
        with self.connect() as c:
            c.execute("INSERT INTO run_manifests VALUES(?,?,?,?)",
                (manifest.run_id, manifest.ticker, json.dumps(asdict(manifest), ensure_ascii=False), now_iso()))

    def record_api_usage(self, run_id: str, ticker: str, provider: str, purpose: str,
                         model: str, input_tokens: int, output_tokens: int,
                         cached_tokens: int = 0, latency_ms: int = 0,
                         estimated_cost: float = 0, success: bool = True) -> None:
        timestamp = now_iso()
        with self.connect() as c:
            c.execute("""INSERT INTO api_usage
                (run_id,ticker,provider,purpose,timestamp,input_tokens,output_tokens,cached_tokens,latency_ms,success)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (run_id,ticker,provider,purpose,timestamp,input_tokens,
                output_tokens,cached_tokens,latency_ms,int(success)))
            c.execute("INSERT INTO model_costs VALUES(NULL,?,?,?,?,?,?)",
                (run_id, provider, model, estimated_cost, "USD", timestamp))

    def save_company_state(self, state: CompanyState) -> None:
        with self.connect() as c:
            c.execute("""INSERT INTO company_states VALUES(?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET updated_at=excluded.updated_at,
                payload_json=excluded.payload_json""",
                      (state.ticker, now_iso(), json.dumps(asdict(state), ensure_ascii=False)))

    def load_company_state(self, ticker: str) -> CompanyState | None:
        with self.connect() as c:
            row = c.execute("SELECT payload_json FROM company_states WHERE ticker=?",
                            (ticker.upper(),)).fetchone()
        return CompanyState(**json.loads(row[0])) if row else None

    def initialize_paper_account(self, initial_cash: float,
                                 account_id: str = "PAPER_DEFAULT",
                                 risk_budget_pct: float = 0.75) -> None:
        if initial_cash <= 0:
            raise ValueError("PAPER initial cash must be positive")
        timestamp = now_iso()
        with self.connect() as c:
            c.execute("""INSERT OR IGNORE INTO paper_accounts
                (account_id,cash,equity,reserved_cash,realized_pnl,risk_budget_pct,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (account_id, initial_cash, initial_cash, 0, 0, risk_budget_pct, timestamp, timestamp))
            if c.execute("SELECT changes()").fetchone()[0]:
                c.execute("""INSERT INTO paper_cash_ledger
                    (account_id,run_id,timestamp,event_type,amount,balance_after,note)
                    VALUES(?,?,?,?,?,?,?)""",
                    (account_id, "", timestamp, "INITIAL_DEPOSIT", initial_cash,
                     initial_cash, "Explicit PAPER account initialization"))

    @staticmethod
    def _current_mark_to_stop_risk(provenance_json: str | None,
                                   mark: float | None) -> float | None:
        if mark is None:
            return None
        try:
            provenance = json.loads(provenance_json or "{}")
        except (TypeError, ValueError):
            return None
        if provenance.get("status") != "KNOWN":
            return None
        try:
            return round(sum(
                max(0.0, float(mark) - float(component["stop_price"])) *
                float(component["quantity"])
                for component in provenance.get("components") or []
            ), 2)
        except (KeyError, TypeError, ValueError):
            return None

    def update_position_mark(self, ticker: str, mark: float, source: str,
                             observed_at: str = "", account_id: str = "PAPER_DEFAULT") -> bool:
        """Persist the latest trusted mark used for exposure and mark-to-stop telemetry."""
        if float(mark) <= 0:
            raise ValueError("PAPER position mark must be positive")
        timestamp = observed_at or now_iso()
        with self.connect() as c:
            row = c.execute("""SELECT quantity FROM portfolio_positions
                WHERE ticker=? AND account_id=? AND status='OPEN'""",
                            (ticker.upper(), account_id)).fetchone()
            if row is None:
                return False
            c.execute("""UPDATE portfolio_positions SET market_value=?,latest_mark=?,
                mark_timestamp=?,mark_source=?,mark_status='FRESH',updated_at=?
                WHERE ticker=? AND account_id=? AND status='OPEN'""",
                      (float(row["quantity"]) * float(mark), float(mark), timestamp,
                       source or "UNKNOWN", timestamp, ticker.upper(), account_id))
        return True

    def paper_account_state(self, account_id: str = "PAPER_DEFAULT") -> dict[str, Any]:
        with self.connect() as c:
            account = c.execute("SELECT * FROM paper_accounts WHERE account_id=?",
                                (account_id,)).fetchone()
            if not account:
                raise ValueError(f"PAPER account not initialized: {account_id}")
            positions = c.execute("""SELECT ticker,quantity,average_price,sector,market_value,
                position_risk_usd,risk_provenance_json,latest_mark,mark_timestamp,mark_source,mark_status
                FROM portfolio_positions WHERE account_id=? AND status='OPEN'""",
                (account_id,)).fetchall()
            pending_rows = c.execute("""SELECT ticker,quantity,risk_per_share,risk_provenance_json,
                sector,reserved_cash
                FROM paper_orders WHERE account_id=?
                AND status IN ('PENDING','TRIGGERED','REVALIDATING')""",
                (account_id,)).fetchall()
        exposure = sum(float(row["market_value"] or 0)
                       for row in positions if row["mark_status"] in {"FRESH", "STALE_MARK"})
        sectors: dict[str, float] = {}
        for row in positions:
            if row["mark_status"] not in {"FRESH", "STALE_MARK"}:
                continue
            sector = row["sector"] or "UNKNOWN"
            sectors[sector] = sectors.get(sector, 0.0) + float(row["market_value"] or 0)
        pending_sectors: dict[str, float] = {}
        pending_tickers: dict[str, float] = {}
        for row in pending_rows:
            sector = row["sector"] or "UNKNOWN"
            pending_sectors[sector] = pending_sectors.get(sector, 0.0) + float(
                row["reserved_cash"] or 0)
            ticker = row["ticker"] or "UNKNOWN"
            pending_tickers[ticker] = pending_tickers.get(ticker, 0.0) + float(
                row["reserved_cash"] or 0)
        equity = float(account["cash"]) + exposure
        risk_budget = equity * float(account["risk_budget_pct"]) / 100
        open_risk = 0.0
        open_risk_complete = True
        current_mark_risk = 0.0
        current_mark_risk_complete = True
        for row in positions:
            try:
                provenance = json.loads(row["risk_provenance_json"] or "{}")
            except (TypeError, ValueError):
                provenance = {}
            if provenance.get("status") != "KNOWN" or row["position_risk_usd"] is None:
                open_risk_complete = False
            else:
                open_risk += max(0.0, float(row["position_risk_usd"] or 0))
            mark_risk = self._current_mark_to_stop_risk(
                row["risk_provenance_json"], row["latest_mark"])
            if row["mark_status"] not in {"FRESH", "STALE_MARK"} or mark_risk is None:
                current_mark_risk_complete = False
            else:
                current_mark_risk += mark_risk
        pending_risk = 0.0
        pending_risk_complete = True
        for row in pending_rows:
            try:
                provenance = json.loads(row["risk_provenance_json"] or "{}")
            except (TypeError, ValueError):
                provenance = {}
            if provenance.get("status") != "KNOWN" or float(row["risk_per_share"] or 0) <= 0:
                pending_risk_complete = False
                continue
            pending_risk += max(0.0, float(row["quantity"]) * float(row["risk_per_share"]))
        if not open_risk_complete and positions:
            open_risk = risk_budget
        if not pending_risk_complete and pending_rows:
            pending_risk = risk_budget
        risk_complete = open_risk_complete and pending_risk_complete
        portfolio_risk = min(risk_budget, open_risk + pending_risk)
        return {
            "account_id": account_id, "cash": float(account["cash"]), "equity": equity,
            "reserved_cash": float(account["reserved_cash"]), "current_exposure": exposure,
            "sector_exposure": sectors, "risk_budget": risk_budget,
            "ticker_exposure": {row["ticker"]: float(row["market_value"] or 0)
                                for row in positions if row["mark_status"] in {"FRESH", "STALE_MARK"}},
            "risk_budget_used": round(open_risk, 2),
            "open_position_risk_usd": round(open_risk, 2),
            "pending_committed_risk": round(pending_risk, 2),
            "portfolio_risk_used": round(portfolio_risk, 2),
            "risk_budget_policy": "INITIAL_RISK_AT_ENTRY",
            "current_mark_to_stop_risk_usd": round(current_mark_risk, 2),
            "current_mark_to_stop_risk_complete": current_mark_risk_complete,
            "risk_provenance_complete": risk_complete,
            "pending_sector_committed_exposure": pending_sectors,
            "pending_ticker_committed_exposure": pending_tickers,
            "pending_committed_exposure": sum(pending_tickers.values()),
            "sector_committed_exposure": {
                sector: sectors.get(sector, 0.0) + pending_sectors.get(sector, 0.0)
                for sector in set(sectors) | set(pending_sectors)
            },
            "open_positions": len(positions), "pending_conditional_orders": len(pending_rows),
            "available_cash": max(0.0, float(account["cash"]) - float(account["reserved_cash"])),
            "position_marks": [{"ticker": row["ticker"], "mark": row["latest_mark"],
                "timestamp": row["mark_timestamp"], "source": row["mark_source"],
                "status": row["mark_status"]} for row in positions],
        }

    @staticmethod
    def release_paper_reservation(c: sqlite3.Connection, order_id: str, final_status: str,
                                  reason: str, timestamp: str) -> None:
        row = c.execute("SELECT * FROM paper_orders WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            raise ValueError(f"PAPER order not found: {order_id}")
        reservation = c.execute("""SELECT * FROM paper_reservations
            WHERE order_id=? AND status='ACTIVE'""", (order_id,)).fetchone()
        amount = float(reservation["amount"]) if reservation else 0.0
        if reservation:
            c.execute("""UPDATE paper_reservations SET status='RELEASED',released_at=?
                WHERE reservation_id=? AND status='ACTIVE'""",
                (timestamp, reservation["reservation_id"]))
            c.execute("""UPDATE paper_accounts SET reserved_cash=MAX(0,reserved_cash-?),
                updated_at=? WHERE account_id=?""", (amount, timestamp, row["account_id"]))
        c.execute("""UPDATE paper_orders SET status=?,status_reason=?,revalidated_at=?,updated_at=?
            WHERE order_id=?""", (final_status, reason, timestamp, timestamp, order_id))

    def fill_conditional_order(self, c: sqlite3.Connection, order: sqlite3.Row,
                               current_price: float, timestamp: str) -> bool:
        """Release one reservation and apply one canonical BUY in the same transaction."""
        self.release_paper_reservation(c, order["order_id"], "REVALIDATING", "PASSED", timestamp)
        notional = round(float(order["quantity"]) * float(current_price), 2)
        effect = {
            "financial_operation_key": f"conditional-fill:{order['order_id']}",
            "account_id": order["account_id"], "run_id": order["run_id"],
            "ticker": order["ticker"], "timestamp": timestamp,
            "sector": order["sector"] or "UNKNOWN", "quantity": order["quantity"],
            "order_id": order["order_id"],
            "price": float(current_price), "notional_usd": notional, "action": "BUY",
            "stop_price": float(order["invalidation_price"]),
            "risk_per_share": float(order["risk_per_share"]),
            "risk_provenance": json.loads(order["risk_provenance_json"] or "{}"),
            "max_sector_exposure_pct": json.loads(
                order["risk_provenance_json"] or "{}").get("sector_cap_pct", 25.0),
            "prediction": {"prediction_id": f"PRED_FILL_{order['order_id']}",
                "run_id": order["run_id"], "ticker": order["ticker"], "decision": "BUY",
                "confidence": 0, "reference_price": float(current_price),
                "horizon": "CONDITIONAL_REVALIDATED"},
        }
        applied = self._apply_paper_effect(c, effect)
        if applied:
            c.execute("""UPDATE paper_orders SET status='FILLED',
                status_reason='CANONICAL_REVALIDATION_PASS',revalidated_at=?,filled_at=?,updated_at=?
                WHERE order_id=?""", (timestamp, timestamp, timestamp, order["order_id"]))
        return applied

    def latest_decision(self, ticker: str) -> sqlite3.Row | None:
        with self.connect() as c:
            return c.execute("SELECT * FROM investment_decisions WHERE ticker=? ORDER BY timestamp DESC LIMIT 1",
                             (ticker.upper(),)).fetchone()

    def latest_certified_decision(self, ticker: str) -> sqlite3.Row | None:
        with self.connect() as c:
            return c.execute("""SELECT d.*,c.certification_status,c.certified_action,
                c.side_effect_status FROM investment_decisions d
                JOIN certification_records c ON c.run_id=d.run_id
                WHERE d.ticker=? AND c.certification_status='CERTIFIED'
                ORDER BY d.timestamp DESC LIMIT 1""", (ticker.upper(),)).fetchone()

    def portfolio_positions(self) -> list[sqlite3.Row]:
        with self.connect() as c:
            return c.execute("SELECT * FROM portfolio_positions ORDER BY ticker").fetchall()

    def finalize_analysis(self, decision: InvestmentDecision, manifest: Any, state: CompanyState,
                          research: Any, critic: Any, risk_rule_version: str,
                          request_id: str, report_path: str, paper_effect: dict[str, Any],
                          debate_status: str, round_count: int,
                          usage: dict[str, Any] | None = None,
                          certification: CertificationResult | None = None) -> None:
        """Commit machine truth and PAPER effects atomically after report file creation."""
        usage = usage or {}
        timestamp = now_iso()
        with self.connect() as c:
            payload = json.dumps(asdict(decision), ensure_ascii=False)
            values = (decision.run_id, decision.ticker, decision.timestamp, decision.decision,
                      decision.confidence, payload)
            c.execute("INSERT INTO investment_decisions VALUES(?,?,?,?,?,?)", values)
            c.execute("INSERT INTO final_decisions VALUES(?,?,?,?,?,?)", values)
            c.execute("INSERT INTO run_manifests VALUES(?,?,?,?)",
                      (manifest.run_id, manifest.ticker,
                       json.dumps(asdict(manifest), ensure_ascii=False), timestamp))
            c.execute("""INSERT INTO company_states VALUES(?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET updated_at=excluded.updated_at,
                payload_json=excluded.payload_json""",
                (state.ticker, timestamp, json.dumps(asdict(state), ensure_ascii=False)))
            c.execute("""INSERT INTO report_artifacts
                (run_id,ticker_label,markdown_path,publish_status,publish_attempts,last_error,
                 created_at,delivered_at,next_retry_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (decision.run_id, decision.ticker, report_path, "PENDING", 0, "", timestamp, None, None))
            c.execute("""INSERT INTO outbox_events(
                event_id,aggregate_type,aggregate_id,event_type,payload_json,status,created_at)
                VALUES(?,?,?,?,?,'PENDING',?) ON CONFLICT(aggregate_id,event_type) DO NOTHING""", (
                f"OUT_REPORT_{decision.run_id}", "ANALYSIS_RUN", decision.run_id,
                "REPORT_READY", json.dumps({"run_id": decision.run_id,
                    "ticker": decision.ticker, "report_path": report_path}, ensure_ascii=False), timestamp))
            self._apply_paper_effect(c, paper_effect)
            c.execute("""UPDATE analysis_runs SET finished_at=?, status='SUCCESS',
                research_provider=?, research_model=?, critic_provider=?, critic_model=?,
                prompt_version=?, risk_rule_version=?, final_decision=?, final_confidence=?,
                debate_status=?,round_count=?,delivery_status='PENDING',input_tokens=?,
                output_tokens=?,reasoning_tokens=?,cached_tokens=?,estimated_cost=?,
                total_latency_ms=?,llm_call_count=? WHERE run_id=?""", (
                timestamp, research.provider, research.model, critic.provider, critic.model,
                f"{research.prompt_version}|{critic.prompt_version}", risk_rule_version,
                decision.decision, decision.confidence, debate_status, int(round_count),
                int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)),
                int(usage.get("reasoning_tokens", 0)), int(usage.get("cached_tokens", 0)),
                float(usage.get("estimated_cost_usd", 0)), int(usage.get("latency_ms", 0)),
                int(usage.get("llm_calls", 0)), decision.run_id))
            c.execute("UPDATE user_requests SET status='COMPLETED',run_id=?,updated_at=? WHERE request_id=?",
                      (decision.run_id, timestamp, request_id))
            if certification is not None:
                self._upsert_certification(c, certification)

    def finalize_uncertified_analysis(self, certification: CertificationResult, manifest: Any,
                                      research: Any, critic: Any, request_id: str,
                                      ticker: str, report_path: str, debate_status: str,
                                      round_count: int,
                                      usage: dict[str, Any] | None = None) -> None:
        """Persist diagnostics without decision, company-state, sizing, or PAPER mutation."""
        usage = usage or {}
        timestamp = now_iso()
        with self.connect() as c:
            c.execute("""INSERT INTO run_manifests(run_id,ticker,payload_json,created_at)
                VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET
                payload_json=excluded.payload_json,created_at=excluded.created_at""",
                (manifest.run_id, ticker, json.dumps(asdict(manifest), ensure_ascii=False), timestamp))
            c.execute("""INSERT INTO report_artifacts(
                run_id,ticker_label,markdown_path,publish_status,publish_attempts,last_error,
                created_at,delivered_at,next_retry_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET markdown_path=excluded.markdown_path,
                publish_status='PENDING',last_error='',next_retry_at=NULL""",
                (certification.run_id, ticker, report_path, "PENDING", 0, "", timestamp, None, None))
            c.execute("""INSERT INTO outbox_events(
                event_id,aggregate_type,aggregate_id,event_type,payload_json,status,created_at)
                VALUES(?,?,?,?,?,'PENDING',?) ON CONFLICT(aggregate_id,event_type) DO NOTHING""", (
                f"OUT_REPORT_{certification.run_id}", "ANALYSIS_RUN", certification.run_id,
                "REPORT_READY", json.dumps({"run_id": certification.run_id, "ticker": ticker,
                    "report_path": report_path, "certification_status":
                    certification.certification_status}, ensure_ascii=False), timestamp))
            self._upsert_certification(c, certification)
            c.execute("""UPDATE analysis_runs SET finished_at=?,status='SUCCESS',
                research_provider=?,research_model=?,critic_provider=?,critic_model=?,
                prompt_version=?,final_decision='NO_CERTIFIED_ACTION',final_confidence=NULL,
                debate_status=?,round_count=?,delivery_status='PENDING',input_tokens=?,
                output_tokens=?,reasoning_tokens=?,cached_tokens=?,estimated_cost=?,
                total_latency_ms=?,llm_call_count=? WHERE run_id=?""", (
                timestamp, research.provider, research.model, critic.provider, critic.model,
                f"{research.prompt_version}|{critic.prompt_version}", debate_status,
                int(round_count), int(usage.get("input_tokens", 0)),
                int(usage.get("output_tokens", 0)), int(usage.get("reasoning_tokens", 0)),
                int(usage.get("cached_tokens", 0)), float(usage.get("estimated_cost_usd", 0)),
                int(usage.get("latency_ms", 0)), int(usage.get("llm_calls", 0)),
                certification.run_id))
            c.execute("UPDATE user_requests SET status='COMPLETED',run_id=?,updated_at=? WHERE request_id=?",
                      (certification.run_id, timestamp, request_id))

    def finalize_data_blocked_analysis(self, run_id: str, request_id: str, ticker: str,
                                       certification: CertificationResult, report_path: str,
                                       diagnostics: dict[str, Any]) -> None:
        """Persist a deterministic pre-debate blocker without fabricating agent output."""
        timestamp = now_iso()
        with self.connect() as c:
            c.execute("""INSERT INTO run_manifests(run_id,ticker,payload_json,created_at)
                VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET payload_json=excluded.payload_json,
                created_at=excluded.created_at""", (run_id, ticker, json.dumps({
                    "run_id": run_id, "ticker": ticker, "final_decision": "NO_CERTIFIED_ACTION",
                    "code_version": "DATA_READINESS_PREFLIGHT", "db_schema_version": self.SCHEMA_VERSION,
                    "diagnostics": diagnostics}, ensure_ascii=False), timestamp))
            c.execute("""INSERT INTO report_artifacts(
                run_id,ticker_label,markdown_path,publish_status,publish_attempts,last_error,created_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET markdown_path=excluded.markdown_path,
                publish_status='PENDING',last_error=''""",
                (run_id, ticker, report_path, "PENDING", 0, "", timestamp))
            c.execute("""INSERT INTO outbox_events(
                event_id,aggregate_type,aggregate_id,event_type,payload_json,status,created_at)
                VALUES(?,?,?,?,?,'PENDING',?) ON CONFLICT(aggregate_id,event_type) DO NOTHING""",
                (f"OUT_REPORT_{run_id}", "ANALYSIS_RUN", run_id, "REPORT_READY",
                 json.dumps({"run_id": run_id, "ticker": ticker,
                             "report_path": report_path,
                             "certification_status": certification.certification_status}, ensure_ascii=False), timestamp))
            self._upsert_certification(c, certification)
            c.execute("""UPDATE analysis_runs SET finished_at=?,status='SUCCESS',
                execution_status='SUCCESS',analysis_status='BLOCKED',certification_status=?,
                side_effect_status='WITHHELD',certified_action='NO_CERTIFIED_ACTION',
                final_decision='NO_CERTIFIED_ACTION',final_confidence=NULL,debate_status='NOT_STARTED',
                round_count=0,delivery_status='PENDING',llm_call_count=0 WHERE run_id=?""",
                (timestamp, certification.certification_status, run_id))
            c.execute("UPDATE user_requests SET status='COMPLETED',run_id=?,updated_at=? WHERE request_id=?",
                      (run_id, timestamp, request_id))

    def save_periodic_readiness(self, run_id: str, ticker: str, item: Any, assessment: dict[str, Any]) -> None:
        with self.connect() as c:
            c.execute("""INSERT OR REPLACE INTO periodic_readiness(
                run_id,ticker,evidence_id,document_type,accession,readiness_state,
                reason_codes_json,numeric_claims_status,source_hash,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (run_id, ticker, item.evidence_id,
                item.document_type, item.accession, assessment.get("state", "UNKNOWN"),
                json.dumps(assessment.get("reason_codes", [])), assessment.get("numeric_claims", "UNKNOWN"),
                item.raw_document_hash, now_iso()))

    def save_offering_semantic_event(self, run_id: str, ticker: str, item: Any, event: dict[str, Any]) -> None:
        with self.connect() as c:
            c.execute("""INSERT OR REPLACE INTO offering_semantic_events(
                run_id,ticker,evidence_id,offering_type,status,economic_effect,
                new_share_creation_possible,issuer_receives_proceeds,remaining_capacity,
                used_amount,confidence,reason_codes_json,source_accession,filed_at,payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (run_id, ticker, item.evidence_id,
                event.get("offering_type", "UNKNOWN_OFFERING"), event.get("status", "UNKNOWN"),
                event.get("economic_effect", ""), event.get("new_share_creation_possible"),
                event.get("issuer_receives_proceeds"), event.get("remaining_capacity"),
                event.get("used_amount"), int(event.get("confidence", 0)),
                json.dumps(event.get("reason_codes", [])), event.get("source_accession", item.accession),
                event.get("filed_at", item.filed_at), json.dumps(event, ensure_ascii=False)))

    @staticmethod
    def _risk_provenance(effect: dict[str, Any]) -> dict[str, Any]:
        quantity = float(effect.get("quantity", 0) or 0)
        if effect.get("action") not in {"BUY", "CONDITIONAL_ORDER"} or quantity <= 0:
            return {"status": "NOT_APPLICABLE", "components": []}
        try:
            risk_per_share = float(effect["risk_per_share"])
            provenance = dict(effect["risk_provenance"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("PAPER entry requires persisted stop-based risk provenance") from exc
        if risk_per_share <= 0 or provenance.get("status") != "KNOWN":
            raise ValueError("PAPER entry risk provenance is incomplete")
        component = {
            "source_run_id": str(provenance.get("source_run_id") or effect.get("run_id", "")),
            "source_operation_key": str(provenance.get("source_operation_key") or
                                         effect.get("financial_operation_key", "")),
            "entry_price": float(provenance.get("entry_price") or effect.get("price", 0)),
            "stop_price": float(provenance.get("stop_price") or effect.get("stop_price", 0)),
            "risk_per_share": risk_per_share,
            "quantity": quantity,
            "risk_usd": round(quantity * risk_per_share, 2),
            "method": str(provenance.get("method") or "TRADE_PLAN_ENTRY_MINUS_STOP"),
        }
        return {"status": "KNOWN", "method": "SUM_ENTRY_TO_STOP_COMPONENTS",
                "components": [component], "risk_usd": component["risk_usd"],
                "sector_cap_pct": float(provenance.get("sector_cap_pct", 25.0))}

    @staticmethod
    def _merge_position_risk(existing_json: str | None, new_provenance: dict[str, Any],
                             existing_risk: float = 0.0) -> tuple[float, str]:
        try:
            existing = json.loads(existing_json or "{}")
        except (TypeError, ValueError):
            existing = {}
        if existing.get("status") == "UNKNOWN_LEGACY":
            return existing_risk, json.dumps(existing, ensure_ascii=False)
        components = list(existing.get("components") or []) + list(new_provenance.get("components") or [])
        total = round(sum(float(item.get("risk_usd", 0)) for item in components), 2)
        return total, json.dumps({"status": "KNOWN", "method": "SUM_ENTRY_TO_STOP_COMPONENTS",
                                  "components": components, "risk_usd": total}, ensure_ascii=False)

    @staticmethod
    def _reduce_position_risk(provenance_json: str | None,
                              remaining_ratio: float) -> tuple[float, str]:
        try:
            provenance = json.loads(provenance_json or "{}")
        except (TypeError, ValueError):
            provenance = {}
        if provenance.get("status") != "KNOWN":
            return 0.0, json.dumps({"status": "UNKNOWN_LEGACY"}, ensure_ascii=False)
        components = []
        for item in provenance.get("components") or []:
            value = dict(item)
            value["quantity"] = round(float(value.get("quantity", 0)) * remaining_ratio, 8)
            value["risk_usd"] = round(float(value.get("risk_usd", 0)) * remaining_ratio, 2)
            if value["quantity"] > 0:
                components.append(value)
        total = round(sum(float(item["risk_usd"]) for item in components), 2)
        return total, json.dumps({"status": "KNOWN", "method": "PRO_RATA_SELL_REDUCTION",
                                  "components": components, "risk_usd": total}, ensure_ascii=False)

    @staticmethod
    def _validate_paper_commit_limits(c: sqlite3.Connection,
                                       effect: dict[str, Any],
                                       account: sqlite3.Row) -> None:
        """Re-read authoritative portfolio limits inside the financial commit transaction."""
        action = str(effect.get("action") or "")
        if action not in {"BUY", "CONDITIONAL_ORDER"}:
            return
        quantity = float(effect.get("quantity", 0) or 0)
        if quantity <= 0:
            return

        positions = c.execute("""SELECT ticker,sector,market_value,position_risk_usd,
            risk_provenance_json,mark_status
            FROM portfolio_positions WHERE account_id=? AND status='OPEN'""",
            (account["account_id"],)).fetchall()
        if any(row["mark_status"] not in {"FRESH", "STALE_MARK"} for row in positions):
            raise ValueError("PAPER_SECTOR_EXPOSURE_UNKNOWN")
        current_exposure = sum(float(row["market_value"] or 0) for row in positions)
        equity = float(account["cash"]) + current_exposure
        if equity <= 0:
            raise ValueError("PAPER_ACCOUNT_EQUITY_INVALID")

        sector = str(effect.get("sector") or "UNKNOWN")
        open_sector = sum(float(row["market_value"] or 0) for row in positions
                          if str(row["sector"] or "UNKNOWN") == sector)
        pending_rows = c.execute("""SELECT order_id,sector,reserved_cash,risk_per_share,
            quantity,risk_provenance_json
            FROM paper_orders WHERE account_id=?
            AND status IN ('PENDING','TRIGGERED','REVALIDATING')
            AND reserved_cash > 0""",
            (account["account_id"],)).fetchall()
        pending = [row for row in pending_rows if row["order_id"] != effect.get("order_id")]
        pending_sector = sum(float(row["reserved_cash"] or 0) for row in pending
                             if str(row["sector"] or "UNKNOWN") == sector)
        risk_metadata = dict(effect.get("risk_provenance") or {})
        sector_cap_pct = float(effect.get("max_sector_exposure_pct") or
                               risk_metadata.get("sector_cap_pct", 25.0))
        projected_sector = open_sector + pending_sector + float(
            effect.get("notional_usd", 0) or 0)
        if projected_sector > equity * sector_cap_pct / 100 + 0.01:
            raise ValueError("PAPER_SECTOR_EXPOSURE_LIMIT")

        # Direct fills must be risk-budget safe at the financial commit boundary.
        # Conditional intents are checked at canonical trigger revalidation.
        if action != "BUY":
            return
        new_risk = Database._risk_provenance(effect)
        if new_risk.get("status") != "KNOWN":
            raise ValueError("PAPER_RISK_PROVENANCE_INCOMPLETE")
        open_risk = 0.0
        for row in positions:
            try:
                provenance = json.loads(row["risk_provenance_json"] or "{}")
            except (TypeError, ValueError):
                provenance = {}
            if provenance.get("status") != "KNOWN" or row["position_risk_usd"] is None:
                raise ValueError("PAPER_RISK_PROVENANCE_INCOMPLETE")
            open_risk += max(0.0, float(row["position_risk_usd"] or 0))
        pending_risk = 0.0
        for row in pending:
            try:
                provenance = json.loads(row["risk_provenance_json"] or "{}")
            except (TypeError, ValueError):
                provenance = {}
            if provenance.get("status") != "KNOWN" or float(row["risk_per_share"] or 0) <= 0:
                raise ValueError("PAPER_RISK_PROVENANCE_INCOMPLETE")
            pending_risk += max(0.0, float(row["quantity"]) * float(row["risk_per_share"]))
        risk_budget = equity * float(account["risk_budget_pct"]) / 100
        if open_risk + pending_risk + float(new_risk.get("risk_usd", 0)) > risk_budget + 0.01:
            raise ValueError("PAPER_RISK_LIMIT")

    @staticmethod
    def _apply_paper_effect(c: sqlite3.Connection, effect: dict[str, Any]) -> bool:
        if not effect:
            return False
        account_id = effect["account_id"]
        account = c.execute("SELECT * FROM paper_accounts WHERE account_id=?",
                            (account_id,)).fetchone()
        if not account:
            raise ValueError(f"PAPER account not initialized: {account_id}")
        timestamp = effect["timestamp"]
        action = effect.get("action", "PREDICTION_ONLY")
        operation_key = effect.get("financial_operation_key") or (
            f"paper:{effect.get('run_id','')}:{action}:{effect.get('ticker','')}")
        cancelled = c.execute("""SELECT status FROM financial_cancellation_requests
            WHERE financial_operation_key=?""", (operation_key,)).fetchone()
        if cancelled and cancelled["status"] == "CANCELLED_BEFORE_COMMIT":
            return False
        existing_operation = c.execute(
            "SELECT status FROM financial_operations WHERE operation_key=?",
            (operation_key,)).fetchone()
        if existing_operation:
            return False
        Database._validate_paper_commit_limits(c, effect, account)
        payload_hash = hashlib.sha256(
            json.dumps(effect, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        claimed = c.execute("""INSERT INTO financial_operations(
            operation_key,account_id,run_id,operation_type,status,payload_hash,claimed_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(operation_key) DO NOTHING""", (
            operation_key, account_id, effect.get("run_id", ""), action,
            "IN_PROGRESS", payload_hash, timestamp))
        if claimed.rowcount != 1:
            return False
        Database._financial_fault(effect, "AFTER_OPERATION_CLAIM")
        prediction = effect["prediction"]
        c.execute("""INSERT INTO paper_predictions
            (prediction_id,run_id,ticker,decision,confidence,reference_price,horizon,created_at,payload_json)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(prediction_id) DO NOTHING""", (
            prediction["prediction_id"], prediction["run_id"], prediction["ticker"],
            prediction["decision"], prediction["confidence"], prediction["reference_price"],
            prediction["horizon"], timestamp, json.dumps(prediction, ensure_ascii=False)))
        if action == "BUY":
            risk_provenance = Database._risk_provenance(effect)
            notional = float(effect["notional_usd"])
            available = float(account["cash"]) - float(account["reserved_cash"])
            if notional > available + 0.01:
                raise ValueError("PAPER account has insufficient available cash")
            new_cash = float(account["cash"]) - notional
            c.execute("UPDATE paper_accounts SET cash=?,updated_at=? WHERE account_id=?",
                      (new_cash, timestamp, account_id))
            Database._financial_fault(effect, "AFTER_CASH_UPDATE")
            c.execute("""INSERT INTO paper_cash_ledger
                (account_id,run_id,timestamp,event_type,amount,balance_after,note,
                 financial_operation_key)
                VALUES(?,?,?,?,?,?,?,?)""", (account_id, effect["run_id"], timestamp,
                "BUY_FILL", -notional, new_cash, effect["ticker"], operation_key))
            c.execute("""INSERT INTO paper_transactions
                (run_id,ticker,timestamp,side,quantity,price,mode,financial_operation_key,
                 cost_basis_method) VALUES(?,?,?,?,?,?,?,?,?)""",
                (effect["run_id"], effect["ticker"], timestamp, "BUY", effect["quantity"],
                 effect["price"], "PAPER", operation_key, "WEIGHTED_AVERAGE"))
            existing = c.execute("""SELECT quantity,average_price,position_risk_usd,
                risk_provenance_json FROM portfolio_positions
                WHERE ticker=? AND account_id=?""", (effect["ticker"], account_id)).fetchone()
            quantity = float(effect["quantity"])
            if existing:
                old_quantity = float(existing["quantity"])
                new_quantity = old_quantity + quantity
                average_price = ((old_quantity * float(existing["average_price"])) +
                                 (quantity * float(effect["price"]))) / new_quantity
                risk_usd, risk_json = Database._merge_position_risk(
                    existing["risk_provenance_json"], risk_provenance,
                    float(existing["position_risk_usd"] or 0))
                c.execute("""UPDATE portfolio_positions SET quantity=?,average_price=?,updated_at=?,
                    sector=?,status='OPEN',market_value=?,position_risk_usd=?,risk_provenance_json=?,
                    risk_as_of=?,latest_mark=?,mark_timestamp=?,mark_source=?,mark_status='FRESH'
                    WHERE ticker=? AND account_id=?""", (
                    new_quantity, average_price, timestamp, effect.get("sector", "UNKNOWN"),
                    new_quantity * float(effect["price"]), risk_usd, risk_json, timestamp,
                    effect["price"], timestamp, "PAPER_FILL", effect["ticker"], account_id))
            else:
                risk_usd, risk_json = Database._merge_position_risk("{}", risk_provenance)
                c.execute("""INSERT INTO portfolio_positions
                    (ticker,quantity,average_price,updated_at,mode,account_id,sector,status,market_value,
                     position_risk_usd,risk_provenance_json,risk_as_of,latest_mark,mark_timestamp,
                     mark_source,mark_status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    effect["ticker"], quantity, effect["price"], timestamp, "PAPER", account_id,
                    effect.get("sector", "UNKNOWN"), "OPEN", notional, risk_usd, risk_json, timestamp,
                    effect["price"], timestamp, "PAPER_FILL", "FRESH"))
            Database._financial_fault(effect, "AFTER_POSITION_UPDATE")
        elif action == "CONDITIONAL_ORDER":
            risk_provenance = Database._risk_provenance(effect)
            reserve = float(effect["notional_usd"])
            available = float(account["cash"]) - float(account["reserved_cash"])
            if reserve > available + 0.01:
                raise ValueError("PAPER account has insufficient cash to reserve conditional order")
            c.execute("UPDATE paper_accounts SET reserved_cash=reserved_cash+?,updated_at=? WHERE account_id=?",
                      (reserve, timestamp, account_id))
            c.execute("""INSERT INTO paper_orders
                (order_id,account_id,run_id,ticker,side,order_type,status,quantity,trigger_price,
                limit_price,reserved_cash,valid_until,invalidation_price,sector,created_at,updated_at,
                financial_operation_key,risk_per_share,risk_provenance_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                effect["order_id"], account_id, effect["run_id"], effect["ticker"], "BUY",
                "CONDITIONAL", "PENDING", effect["quantity"], effect["trigger_price"],
                effect["price"], reserve, effect["valid_until"], effect["invalidation_price"],
                effect.get("sector", "UNKNOWN"), timestamp, timestamp, operation_key,
                effect["risk_per_share"], json.dumps(risk_provenance, ensure_ascii=False)))
            c.execute("""INSERT INTO paper_reservations(
                reservation_id,order_id,account_id,amount,status,created_at)
                VALUES(?,?,?,?,?,?)""", (
                f"RES_{effect['order_id']}", effect["order_id"], account_id,
                reserve, "ACTIVE", timestamp))
        elif action in {"SELL", "TRIM"}:
            row = c.execute("""SELECT quantity,average_price,position_risk_usd,risk_provenance_json
                FROM portfolio_positions
                WHERE ticker=? AND account_id=? AND status='OPEN'""",
                (effect["ticker"], account_id)).fetchone()
            if not row or float(row["quantity"]) < float(effect["quantity"]):
                raise ValueError("PAPER position is insufficient for SELL/TRIM")
            quantity = float(effect["quantity"])
            proceeds = quantity * float(effect["price"])
            realized = quantity * (float(effect["price"]) - float(row["average_price"]))
            new_cash = float(account["cash"]) + proceeds
            remaining = float(row["quantity"]) - quantity
            c.execute("""UPDATE paper_accounts SET cash=?,realized_pnl=realized_pnl+?,updated_at=?
                WHERE account_id=?""", (new_cash, realized, timestamp, account_id))
            c.execute("""INSERT INTO paper_cash_ledger
                (account_id,run_id,timestamp,event_type,amount,balance_after,note,
                 financial_operation_key)
                VALUES(?,?,?,?,?,?,?,?)""", (account_id, effect["run_id"], timestamp,
                action, proceeds, new_cash, effect["ticker"], operation_key))
            c.execute("""INSERT INTO paper_transactions
                (run_id,ticker,timestamp,side,quantity,price,mode,financial_operation_key,
                 cost_basis_method) VALUES(?,?,?,?,?,?,?,?,?)""",
                (effect["run_id"], effect["ticker"], timestamp, action, quantity,
                 effect["price"], "PAPER", operation_key, "WEIGHTED_AVERAGE"))
            if remaining <= 0:
                c.execute("DELETE FROM portfolio_positions WHERE ticker=? AND account_id=?",
                          (effect["ticker"], account_id))
            else:
                risk_usd, risk_json = Database._reduce_position_risk(
                    row["risk_provenance_json"], remaining / float(row["quantity"]))
                c.execute("""UPDATE portfolio_positions SET quantity=?,updated_at=?,
                    market_value=?,position_risk_usd=?,risk_provenance_json=?,risk_as_of=?
                    WHERE ticker=? AND account_id=?""",
                    (remaining, timestamp, remaining * float(effect["price"]), risk_usd, risk_json,
                     timestamp,
                      effect["ticker"], account_id))
            Database._financial_fault(effect, "AFTER_POSITION_UPDATE")
        account_after = c.execute("SELECT cash FROM paper_accounts WHERE account_id=?",
                                  (account_id,)).fetchone()
        position_after = c.execute("""SELECT quantity FROM portfolio_positions
            WHERE ticker=? AND account_id=?""", (effect.get("ticker", ""), account_id)).fetchone()
        cash_delta = (-float(effect.get("notional_usd", 0)) if action == "BUY"
                      else float(effect.get("notional_usd", 0)) if action in {"SELL", "TRIM"}
                      else 0.0)
        quantity_delta = (float(effect.get("quantity", 0)) if action == "BUY"
                          else -float(effect.get("quantity", 0)) if action in {"SELL", "TRIM"}
                          else 0.0)
        c.execute("""INSERT INTO financial_journal(
            journal_id,financial_operation_key,account_id,run_id,ticker,timestamp,event_type,
            cash_delta,quantity_delta,price,balance_after,position_quantity_after,
            cost_basis_method,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            f"JRN_{hashlib.sha256(operation_key.encode()).hexdigest()[:24]}", operation_key,
            account_id, effect.get("run_id", ""), effect.get("ticker", ""), timestamp,
            action, cash_delta, quantity_delta, effect.get("price"),
            float(account_after[0]) if account_after else None,
            float(position_after[0]) if position_after else 0.0,
            "WEIGHTED_AVERAGE", json.dumps(effect, ensure_ascii=False, default=str)))
        c.execute("""INSERT INTO outbox_events(
            event_id,aggregate_type,aggregate_id,event_type,payload_json,status,created_at)
            VALUES(?,?,?,?,?,'PENDING',?) ON CONFLICT(aggregate_id,event_type) DO NOTHING""", (
            f"OUT_{hashlib.sha256((operation_key+action).encode()).hexdigest()[:24]}",
            "FINANCIAL_OPERATION", operation_key, f"PAPER_{action}_COMMITTED",
            json.dumps({"run_id": effect.get("run_id"), "ticker": effect.get("ticker"),
                        "operation_key": operation_key}, ensure_ascii=False), timestamp))
        Database._financial_fault(effect, "AFTER_OUTBOX_WRITE")
        Database._financial_fault(effect, "BEFORE_OPERATION_COMMIT")
        c.execute("""UPDATE financial_operations SET status='COMMITTED',committed_at=?
            WHERE operation_key=? AND status='IN_PROGRESS'""", (timestamp, operation_key))
        return True

    @staticmethod
    def _financial_fault(effect: dict[str, Any], point: str) -> None:
        if effect.get("fault_at") == point:
            raise RuntimeError(f"injected financial fault at {point}")

    def apply_paper_effect(self, effect: dict[str, Any]) -> bool:
        """Public transaction boundary, including an observable post-commit crash point."""
        with self.connect() as connection:
            applied = self._apply_paper_effect(connection, effect)
        if effect.get("fault_at") == "AFTER_COMMIT":
            raise RuntimeError("injected financial fault at AFTER_COMMIT")
        return applied

    def request_financial_cancellation(self, operation_key: str,
                                       reason: str = "USER_REQUEST") -> str:
        timestamp = now_iso()
        with self.connect() as connection:
            operation = connection.execute("""SELECT status FROM financial_operations
                WHERE operation_key=?""", (operation_key,)).fetchone()
            status = ("COMMITTED_BEFORE_CANCEL_REQUEST"
                      if operation and operation["status"] == "COMMITTED"
                      else "CANCELLED_BEFORE_COMMIT")
            connection.execute("""INSERT INTO financial_cancellation_requests(
                financial_operation_key,status,requested_at,resolved_at,reason)
                VALUES(?,?,?,?,?) ON CONFLICT(financial_operation_key) DO UPDATE SET
                status=CASE WHEN financial_cancellation_requests.status=
                    'COMMITTED_BEFORE_CANCEL_REQUEST' THEN financial_cancellation_requests.status
                    ELSE excluded.status END,
                resolved_at=excluded.resolved_at,reason=excluded.reason""",
                (operation_key, status, timestamp, timestamp, reason))
            if operation:
                connection.execute("""UPDATE financial_operations SET cancellation_status=?
                    WHERE operation_key=?""", (status, operation_key))
        return status

    def financial_invariants(self, account_id: str = "PAPER_DEFAULT") -> dict[str, Any]:
        with self.connect() as c:
            account = c.execute("SELECT * FROM paper_accounts WHERE account_id=?",
                                (account_id,)).fetchone()
            if account is None:
                raise ValueError(f"PAPER account not initialized: {account_id}")
            cash_journal = float(c.execute("""SELECT COALESCE(SUM(amount),0)
                FROM paper_cash_ledger WHERE account_id=?""", (account_id,)).fetchone()[0])
            reservations = float(c.execute("""SELECT COALESCE(SUM(amount),0)
                FROM paper_reservations WHERE account_id=? AND status='ACTIVE'""",
                (account_id,)).fetchone()[0])
            positions = {row["ticker"]: float(row["quantity"]) for row in c.execute(
                "SELECT ticker,quantity FROM portfolio_positions WHERE account_id=?", (account_id,))}
            tx_rows = c.execute("""SELECT ticker,side,quantity FROM paper_transactions
                WHERE financial_operation_key IS NOT NULL ORDER BY id""").fetchall()
        transaction_quantities: dict[str, float] = {}
        for row in tx_rows:
            sign = 1.0 if row["side"] == "BUY" else -1.0
            transaction_quantities[row["ticker"]] = (
                transaction_quantities.get(row["ticker"], 0.0) + sign * float(row["quantity"]))
        quantity_ok = all(abs(positions.get(ticker, 0.0) - quantity) < 1e-8
                          for ticker, quantity in transaction_quantities.items())
        return {
            "cash_journal_matches": abs(cash_journal - float(account["cash"])) < 0.01,
            "reservation_matches": abs(reservations - float(account["reserved_cash"])) < 0.01,
            "position_quantity_matches": quantity_ok,
            "cash_journal_total": cash_journal,
            "current_cash": float(account["cash"]),
            "active_reservations": reservations,
            "reserved_cash": float(account["reserved_cash"]),
            "cost_basis_method": "WEIGHTED_AVERAGE",
        }

    def pending_outbox_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as c:
            rows = c.execute("""SELECT * FROM outbox_events WHERE status IN ('PENDING','FAILED')
                ORDER BY created_at LIMIT ?""", (max(1, min(int(limit), 1000)),)).fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def mark_outbox_event(self, aggregate_id: str, event_type: str,
                          published: bool, error: str = "") -> None:
        with self.connect() as c:
            c.execute("""UPDATE outbox_events SET status=?,attempts=attempts+1,
                published_at=CASE WHEN ? THEN ? ELSE published_at END,last_error=?
                WHERE aggregate_id=? AND event_type=?""", (
                "PUBLISHED" if published else "FAILED", int(published), now_iso(), error,
                aggregate_id, event_type))

    def record_llm_call(self, call: dict[str, Any]) -> None:
        columns = (
            "api_call_id,run_id,request_id,ticker,role,round_no,phase,provider,model,"
            "reasoning_effort,started_at,finished_at,latency_ms,input_tokens,output_tokens,"
            "reasoning_tokens,cache_read_tokens,cache_write_tokens,total_tokens,api_calls,"
            "estimated_cost_usd,repair_attempt,completed,failed,error_type,prompt_chars,response_chars,"
            "call_id,parent_call_id,attempt,usage_known,exception_type"
        )
        names = columns.split(",")
        payload = dict(call)
        payload["call_id"] = payload.get("call_id") or payload.get("api_call_id")
        payload["attempt"] = int(payload.get("attempt") or 1)
        payload["usage_known"] = int(bool(payload.get("usage_known", any(
            key in payload for key in ("input_tokens", "output_tokens", "reasoning_tokens")))))
        payload["exception_type"] = payload.get("exception_type") or payload.get("error_type")
        with self.connect() as c:
            c.execute(f"INSERT INTO llm_calls({columns}) VALUES({','.join('?' for _ in names)})",
                      tuple(payload.get(name) for name in names))
            c.execute("""INSERT INTO api_usage
                (run_id,ticker,provider,purpose,timestamp,input_tokens,output_tokens,cached_tokens,
                 latency_ms,success) VALUES(?,?,?,?,?,?,?,?,?,?)""", (
                call.get("run_id", ""), call.get("ticker", ""), call.get("provider", ""),
                f"{call.get('role','')}:{call.get('phase','')}:R{call.get('round_no',0)}",
                call.get("finished_at") or now_iso(), int(call.get("input_tokens") or 0),
                int(call.get("output_tokens") or 0),
                int(call.get("cache_read_tokens") or 0) + int(call.get("cache_write_tokens") or 0),
                int(call.get("latency_ms") or 0), int(not bool(call.get("failed")))))
            c.execute("INSERT INTO model_costs VALUES(NULL,?,?,?,?,?,?)", (
                call.get("run_id", ""), call.get("provider", ""), call.get("model", ""),
                float(call.get("estimated_cost_usd") or 0), "USD",
                call.get("finished_at") or now_iso()))

    def usage_summary(self, run_id: str) -> dict[str, Any]:
        with self.connect() as c:
            row = c.execute("""SELECT COUNT(*) llm_calls,
                COALESCE(SUM(input_tokens),0) input_tokens,
                COALESCE(SUM(output_tokens),0) output_tokens,
                COALESCE(SUM(reasoning_tokens),0) reasoning_tokens,
                COALESCE(SUM(cache_read_tokens+cache_write_tokens),0) cached_tokens,
                COALESCE(SUM(latency_ms),0) latency_ms,
                COALESCE(SUM(estimated_cost_usd),0) estimated_cost_usd
                FROM llm_calls WHERE run_id=?""", (run_id,)).fetchone()
        return dict(row)

    def latest_successful_run(self, ticker: str) -> dict[str, Any] | None:
        with self.connect() as c:
            run = c.execute("""SELECT * FROM analysis_runs WHERE ticker=? AND status='SUCCESS'
                ORDER BY finished_at DESC LIMIT 1""", (ticker.upper(),)).fetchone()
            if not run:
                return None
            decision = c.execute("SELECT payload_json FROM investment_decisions WHERE run_id=?",
                                 (run["run_id"],)).fetchone()
            research = c.execute("SELECT payload_json FROM research_outputs WHERE run_id=?",
                                 (run["run_id"],)).fetchone()
            snapshot = c.execute("SELECT payload_json FROM market_snapshots WHERE run_id=? AND ticker=?",
                                 (run["run_id"], ticker.upper())).fetchone()
            manifest = c.execute("SELECT payload_json FROM run_manifests WHERE run_id=?",
                                 (run["run_id"],)).fetchone()
        return {"run": dict(run),
                "decision": json.loads(decision[0]) if decision else None,
                "research": json.loads(research[0]) if research else None,
                "market": json.loads(snapshot[0]) if snapshot else None,
                "manifest": json.loads(manifest[0]) if manifest else None}

    def latest_certified_run(self, ticker: str) -> dict[str, Any] | None:
        with self.connect() as c:
            run = c.execute("""SELECT r.* FROM analysis_runs r
                JOIN certification_records cr ON cr.run_id=r.run_id
                WHERE r.ticker=? AND r.execution_status='SUCCESS'
                AND cr.certification_status='CERTIFIED'
                ORDER BY r.finished_at DESC LIMIT 1""", (ticker.upper(),)).fetchone()
            if not run:
                return None
            run_id = run["run_id"]
            decision = c.execute("SELECT payload_json FROM investment_decisions WHERE run_id=?",
                                 (run_id,)).fetchone()
            research = c.execute("SELECT payload_json FROM research_outputs WHERE run_id=?",
                                 (run_id,)).fetchone()
            snapshot = c.execute("SELECT payload_json FROM market_snapshots WHERE run_id=? AND ticker=?",
                                 (run_id, ticker.upper())).fetchone()
            manifest = c.execute("SELECT payload_json FROM run_manifests WHERE run_id=?",
                                 (run_id,)).fetchone()
            certification = c.execute("SELECT payload_json FROM certification_records WHERE run_id=?",
                                      (run_id,)).fetchone()
        return {"run": dict(run),
                "decision": json.loads(decision[0]) if decision else None,
                "research": json.loads(research[0]) if research else None,
                "market": json.loads(snapshot[0]) if snapshot else None,
                "manifest": json.loads(manifest[0]) if manifest else None,
                "certification": json.loads(certification[0]) if certification else None}

    def record_knowledge_sync(self, run_id: str, ticker: str, status: str,
                              vault_path: str, error: str = "") -> None:
        with self.connect() as c:
            c.execute("""INSERT INTO knowledge_sync
                (run_id,ticker,status,vault_path,error,synced_at) VALUES(?,?,?,?,?,?)""",
                (run_id, ticker, status, vault_path, error, now_iso()))

    def request_cancellation(self, run_id: str, reason: str = "USER_REQUEST") -> None:
        with self.connect() as c:
            c.execute("""INSERT INTO run_cancellations(run_id,status,requested_at,reason)
                VALUES(?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET status='CANCEL_REQUESTED',
                requested_at=excluded.requested_at,reason=excluded.reason""",
                (run_id, "CANCEL_REQUESTED", now_iso(), reason))
            c.execute("UPDATE analysis_runs SET cancellation_status='CANCEL_REQUESTED' WHERE run_id=?",
                      (run_id,))
            c.execute("UPDATE job_queue SET cancel_requested=1 WHERE run_id=?", (run_id,))

    def request_cancellation_for_tickers(self, tickers: list[str]) -> list[str]:
        if not tickers:
            return []
        placeholders = ",".join("?" for _ in tickers)
        with self.connect() as c:
            rows = c.execute(f"""SELECT run_id FROM analysis_runs WHERE ticker IN ({placeholders})
                AND status IN ('RUNNING','QUEUED')""", tuple(value.upper() for value in tickers)).fetchall()
        for row in rows:
            self.request_cancellation(row["run_id"])
        with self.connect() as c:
            queued = c.execute("SELECT job_id,payload_json FROM job_queue WHERE status='QUEUED'").fetchall()
            wanted = {value.upper() for value in tickers}
            for job in queued:
                payload = json.loads(job["payload_json"])
                if wanted.intersection(value.upper() for value in payload.get("tickers", [])):
                    c.execute("UPDATE job_queue SET status='CANCELLED',cancel_requested=1,finished_at=? WHERE job_id=?",
                              (now_iso(), job["job_id"]))
                    c.execute("UPDATE user_requests SET status='CANCELLED',updated_at=? WHERE request_id=?",
                              (now_iso(), payload.get("request_id", "")))
        return [row["run_id"] for row in rows]

    def cancel_job(self, job_id: str) -> None:
        with self.connect() as c:
            c.execute("UPDATE job_queue SET status='CANCELLED',cancel_requested=1,finished_at=? WHERE job_id=?",
                      (now_iso(), job_id))

    def is_job_cancelled(self, job_id: str) -> bool:
        with self.connect() as c:
            row = c.execute("SELECT status,cancel_requested FROM job_queue WHERE job_id=?",
                            (job_id,)).fetchone()
        return bool(row and (row["status"] == "CANCELLED" or row["cancel_requested"]))

    def is_cancel_requested(self, run_id: str) -> bool:
        with self.connect() as c:
            row = c.execute("SELECT status FROM run_cancellations WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row["status"] == "CANCEL_REQUESTED")

    def acknowledge_cancellation(self, run_id: str) -> None:
        with self.connect() as c:
            c.execute("""INSERT INTO run_cancellations(run_id,status,acknowledged_at)
                VALUES(?,?,?) ON CONFLICT(run_id) DO UPDATE SET status='CANCELLED',
                acknowledged_at=excluded.acknowledged_at""", (run_id, "CANCELLED", now_iso()))
            c.execute("""UPDATE analysis_runs SET status='CANCELLED',finished_at=?,
                cancellation_status='CANCELLED' WHERE run_id=?""", (now_iso(), run_id))
            c.execute("UPDATE job_queue SET status='CANCELLED',finished_at=? WHERE run_id=?",
                      (now_iso(), run_id))

    def enqueue_job(self, request: Any, priority: int = 100) -> str:
        payload = asdict(request) if is_dataclass(request) else dict(request)
        identity = "|".join((
            str(payload.get("discord_message_id") or payload.get("request_id") or ""),
            str(payload.get("intent") or ""),
            ",".join(sorted(str(value).upper() for value in payload.get("tickers", []))),
        ))
        stable_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        job_id = f"JOB_{stable_key[:32]}"
        with self.connect() as c:
            c.execute("""INSERT INTO job_queue
                (job_id,request_id,payload_json,status,priority,created_at,cancel_requested,
                 attempt,stable_key)
                VALUES(?,?,?,?,?,?,0,0,?) ON CONFLICT DO NOTHING""",
                (job_id, payload["request_id"], json.dumps(payload, ensure_ascii=False),
                 "QUEUED", int(priority), now_iso(), stable_key))
            row = c.execute("SELECT job_id FROM job_queue WHERE stable_key=?", (stable_key,)).fetchone()
            if row is None:
                raise RuntimeError("failed to persist stable queue identity")
            job_id = str(row[0])
        return job_id

    def start_job(self, job_id: str, run_id: str = "", lease_owner: str = "",
                  lease_seconds: int = 900) -> bool:
        owner = lease_owner or f"worker:{__import__('os').getpid()}"
        now = datetime.now(timezone.utc)
        lease_until = (now + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        with self.connect() as c:
            cursor = c.execute("""UPDATE job_queue SET status='RUNNING',
                run_id=CASE WHEN ?='' THEN run_id ELSE ? END,
                started_at=COALESCE(started_at,?),heartbeat_at=?,attempt=attempt+1,
                lease_owner=?,lease_until=?
                WHERE job_id=? AND status='QUEUED' AND cancel_requested=0""",
                (run_id, run_id, now.isoformat(), now.isoformat(), owner, lease_until, job_id))
            return cursor.rowcount == 1

    def heartbeat_job(self, job_id: str, run_id: str = "", lease_seconds: int = 900) -> None:
        lease_until = (datetime.now(timezone.utc) +
                       timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        with self.connect() as c:
            c.execute("""UPDATE job_queue SET heartbeat_at=?,lease_until=?,
                run_id=CASE WHEN ?='' THEN run_id ELSE ? END
                WHERE job_id=? AND status='RUNNING'""",
                      (now_iso(), lease_until, run_id, run_id, job_id))

    def finish_job(self, job_id: str, status: str, error: str = "") -> None:
        with self.connect() as c:
            c.execute("""UPDATE job_queue SET status=?,finished_at=?,last_error=?,
                lease_owner=NULL,lease_until=NULL WHERE job_id=?""",
                      (status, now_iso(), error, job_id))

    def recoverable_jobs(self, max_attempts: int = 2) -> list[dict[str, Any]]:
        now = now_iso()
        with self.connect() as c:
            c.execute("""UPDATE job_queue SET status='QUEUED',started_at=NULL,heartbeat_at=NULL,
                lease_owner=NULL,lease_until=NULL WHERE status='RUNNING' AND attempt<?
                AND lease_until IS NOT NULL AND lease_until<=?""",
                (max_attempts, now))
            c.execute("""UPDATE job_queue SET status='ABORTED',finished_at=?,
                last_error='restart retry limit reached',lease_owner=NULL,lease_until=NULL
                WHERE status='RUNNING' AND attempt>=?
                AND lease_until IS NOT NULL AND lease_until<=?""",
                (now, max_attempts, now))
            rows = c.execute("""SELECT * FROM job_queue WHERE status='QUEUED' AND cancel_requested=0
                ORDER BY priority,created_at""").fetchall()
        return [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]

    def _save_payload(self, table: str, values: tuple[Any, ...], columns: str) -> None:
        if table != "market_snapshots" or columns != "run_id,ticker,timestamp,payload_json":
            raise ValueError("unsupported payload table")
        with self.connect() as c:
            c.execute("""INSERT INTO market_snapshots(run_id,ticker,timestamp,payload_json)
                VALUES(?,?,?,?) ON CONFLICT(run_id,ticker) DO UPDATE SET
                timestamp=excluded.timestamp,payload_json=excluded.payload_json""", values)
