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
    SCHEMA_VERSION = 23

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
     ßžuâÚ$z{-®éÜj×bÖ&µö÷WF&÷…öWfVçB‡6VÆbÂvw&VvFUö–C¢7G"ÂWfVçE÷G—S¢7G"ÀÐ¢V&Æ—6†VC¢&ööÂÂW'&÷#¢7G"Ò""’ÓâæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚""%UDDR÷WF&÷…öWfVçG24UB7FGW3ÓòÆGFV×G3ÖGFV×G2³ÀÐ¢V&Æ—6†VEöCÔ44Rt„TâòD„TâòTÅ4RV&Æ—6†VEöBTäBÆÆ7EöW'&÷#ÓðÐ¢t„U$Rvw&VvFUö–CÓòäBWfVçE÷G—SÓò"""Â€Ð¢%T$Ä•4„TB"–bV&Æ—6†VBVÇ6R$d”ÄTB"Â–çB‡V&Æ—6†VB’Âæ÷uö—6ò‚’ÂW'&÷"ÀÐ¢vw&VvFUö–BÂWfVçE÷G—R’Ð Ð¢FVb&V6÷&EöÆÆÕö6ÆÂ‡6VÆbÂ6ÆÃ¢F–7E·7G"Âç•Ò’ÓâæöæS Ð¢6öÇVÖç2Ò€Ð¢&•ö6ÆÅö–BÇ'Våö–BÇ&WVW7Eö–BÇF–6¶W"Ç&öÆRÇ&÷VæEöæòÇ†6RÇ&÷f–FW"ÆÖöFVÂÂ Ð¢'&V6öæ–æuöVff÷'BÇ7F'FVEöBÆf–æ—6†VEöBÆÆFVæ7•ö×2Æ–çWE÷Fö¶Vç2Æ÷WGWE÷Fö¶Vç2Â Ð¢'&V6öæ–æu÷Fö¶Vç2Æ66†U÷&VE÷Fö¶Vç2Æ66†U÷w&—FU÷Fö¶Vç2ÇF÷FÅ÷Fö¶Vç2Æ•ö6ÆÇ2Â Ð¢&W7F–ÖFVEö6÷7E÷W6BÇ&W—%öGFV×BÆ6ö×ÆWFVBÆf–ÆVBÆW'&÷%÷G—RÇ&ö×Eö6†'2Ç&W7öç6Uö6†'2Â Ð¢&6ÆÅö–BÇ&VçEö6ÆÅö–BÆGFV×BÇW6vUö¶æ÷vâÆW†6WF–öå÷G—R Ð¢Ð¢æÖW2Ò6öÇVÖç2ç7Æ—B‚"Â"Ð¢–ÆöBÒF–7B†6ÆÂÐ¢–ÆöE²&6ÆÅö–B%ÒÒ–ÆöBævWB‚&6ÆÅö–B"’÷"–ÆöBævWB‚&•ö6ÆÅö–B"Ð¢–ÆöE²&GFV×B%ÒÒ–çB‡–ÆöBævWB‚&GFV×B"’÷"Ð¢–ÆöE²'W6vUö¶æ÷vâ%ÒÒ–çB†&ööÂ‡–ÆöBævWB‚'W6vUö¶æ÷vâ"Âç’€Ð¢¶W’–â–ÆöBf÷"¶W’–â‚&–çWE÷Fö¶Vç2"Â&÷WGWE÷Fö¶Vç2"Â'&V6öæ–æu÷Fö¶Vç2"’’’’Ð¢–ÆöE²&W†6WF–öå÷G—R%ÒÒ–ÆöBævWB‚&W†6WF–öå÷G—R"’÷"–ÆöBævWB‚&W'&÷%÷G—R"Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR†b$”å4U%B”åDòÆÆÕö6ÆÇ2‡¶6öÇVÖç7Ò’dÅTU2‡²rÂræ¦ö–â‚sòrf÷"ò–âæÖW2—Ò’"ÀÐ¢GWÆR‡–ÆöBævWB†æÖR’f÷"æÖR–âæÖW2’Ð¢2æW†V7WFR‚""$”å4U%B”åDò•÷W6vPÐ¢‡'Våö–BÇF–6¶W"Ç&÷f–FW"ÇW'÷6RÇF–ÖW7F×Æ–çWE÷Fö¶Vç2Æ÷WGWE÷Fö¶Vç2Æ66†VE÷Fö¶Vç2ÀÐ¢ÆFVæ7•ö×2Ç7V66W72’dÅTU2ƒòÃòÃòÃòÃòÃòÃòÃòÃòÃò’"""Â€Ð¢6ÆÂævWB‚''Våö–B"Â""’Â6ÆÂævWB‚'F–6¶W""Â""’Â6ÆÂævWB‚'&÷f–FW""Â""’ÀÐ¢b'¶6ÆÂævWB‚w&öÆRrÂrr—Ó§¶6ÆÂævWB‚w†6RrÂrr—Ó¥'¶6ÆÂævWB‚w&÷VæEöæòrÃ—Ò"ÀÐ¢6ÆÂævWB‚&f–æ—6†VEöB"’÷"æ÷uö—6ò‚’Â–çB†6ÆÂævWB‚&–çWE÷Fö¶Vç2"’÷"’ÀÐ¢–çB†6ÆÂævWB‚&÷WGWE÷Fö¶Vç2"’÷"’ÀÐ¢–çB†6ÆÂævWB‚&66†U÷&VE÷Fö¶Vç2"’÷"’²–çB†6ÆÂævWB‚&66†U÷w&—FU÷Fö¶Vç2"’÷"’ÀÐ¢–çB†6ÆÂævWB‚&ÆFVæ7•ö×2"’÷"’Â–çB†æ÷B&ööÂ†6ÆÂævWB‚&f–ÆVB"’’’’Ð¢2æW†V7WFR‚$”å4U%B”åDòÖöFVÅö6÷7G2dÅTU2„åTÄÂÃòÃòÃòÃòÃòÃò’"Â€Ð¢6ÆÂævWB‚''Våö–B"Â""’Â6ÆÂævWB‚'&÷f–FW""Â""’Â6ÆÂævWB‚&ÖöFVÂ"Â""’ÀÐ¢fÆöB†6ÆÂævWB‚&W7F–ÖFVEö6÷7E÷W6B"’÷"’Â%U4B"ÀÐ¢6ÆÂævWB‚&f–æ—6†VEöB"’÷"æ÷uö—6ò‚’’Ð Ð¢FVbW6vU÷7VÖÖ'’‡6VÆbÂ'Våö–C¢7G"’ÓâF–7E·7G"Âç•Ó Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢&÷rÒ2æW†V7WFR‚""%4TÄT5B4õTåB‚¢’ÆÆÕö6ÆÇ2ÀÐ¢4ôÄU44R…5TÒ†–çWE÷Fö¶Vç2’Ã’–çWE÷Fö¶Vç2ÀÐ¢4ôÄU44R…5TÒ†÷WGWE÷Fö¶Vç2’Ã’÷WGWE÷Fö¶Vç2ÀÐ¢4ôÄU44R…5TÒ‡&V6öæ–æu÷Fö¶Vç2’Ã’&V6öæ–æu÷Fö¶Vç2ÀÐ¢4ôÄU44R…5TÒ†66†U÷&VE÷Fö¶Vç2¶66†U÷w&—FU÷Fö¶Vç2’Ã’66†VE÷Fö¶Vç2ÀÐ¢4ôÄU44R…5TÒ†ÆFVæ7•ö×2’Ã’ÆFVæ7•ö×2ÀÐ¢4ôÄU44R…5TÒ†W7F–ÖFVEö6÷7E÷W6B’Ã’W7F–ÖFVEö6÷7E÷W6@Ð¢e$ôÒÆÆÕö6ÆÇ2t„U$R'Våö–CÓò"""Â‡'Våö–BÂ’’æfWF6†öæR‚Ð¢&WGW&âF–7B‡&÷rÐ Ð¢FVbÆFW7E÷7V66W76gVÅ÷'Vâ‡6VÆbÂF–6¶W#¢7G"’ÓâF–7E·7G"Âç•ÒÂæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢'VâÒ2æW†V7WFR‚""%4TÄT5B¢e$ôÒæÇ—6—5÷'Vç2t„U$RF–6¶W#ÓòäB7FGW3Òu5T44U52pÐ¢õ$DU"%’f–æ—6†VEöBDU42Ä”Ô•B"""Â‡F–6¶W"çWW"‚’Â’’æfWF6†öæR‚Ð¢–bæ÷B'Vã Ð¢&WGW&âæöæPÐ¢FV6—6–öâÒ2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒ–çfW7FÖVçEöFV6—6–öç2t„U$R'Våö–CÓò"ÀÐ¢‡'Vå²''Våö–B%ÒÂ’’æfWF6†öæR‚Ð¢&W6V&6‚Ò2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒ&W6V&6…ö÷WGWG2t„U$R'Våö–CÓò"ÀÐ¢‡'Vå²''Våö–B%ÒÂ’’æfWF6†öæR‚Ð¢6æ6†÷BÒ2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒÖ&¶WE÷6æ6†÷G2t„U$R'Våö–CÓòäBF–6¶W#Óò"ÀÐ¢‡'Vå²''Våö–B%ÒÂF–6¶W"çWW"‚’’’æfWF6†öæR‚Ð¢Öæ–fW7BÒ2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒ'VåöÖæ–fW7G2t„U$R'Våö–CÓò"ÀÐ¢‡'Vå²''Våö–B%ÒÂ’’æfWF6†öæR‚Ð¢&WGW&â²''Vâ#¢F–7B‡'Vâ’ÀÐ¢&FV6—6–öâ#¢§6öâæÆöG2†FV6—6–öå³Ò’–bFV6—6–öâVÇ6RæöæRÀÐ¢'&W6V&6‚#¢§6öâæÆöG2‡&W6V&6…³Ò’–b&W6V&6‚VÇ6RæöæRÀÐ¢&Ö&¶WB#¢§6öâæÆöG2‡6æ6†÷E³Ò’–b6æ6†÷BVÇ6RæöæRÀÐ¢&Öæ–fW7B#¢§6öâæÆöG2†Öæ–fW7E³Ò’–bÖæ–fW7BVÇ6RæöæWÐÐ Ð¢FVbÆFW7Eö6W'F–f–VE÷'Vâ‡6VÆbÂF–6¶W#¢7G"’ÓâF–7E·7G"Âç•ÒÂæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢'VâÒ2æW†V7WFR‚""%4TÄT5B"â¢e$ôÒæÇ—6—5÷'Vç2 Ð¢¤ô”â6W'F–f–6F–öå÷&V6÷&G27"ôâ7"ç'Våö–C×"ç'Våö–@Ð¢t„U$R"çF–6¶W#ÓòäB"æW†V7WF–öå÷7FGW3Òu5T44U52pÐ¢äB7"æ6W'F–f–6F–öå÷7FGW3Òt4U%D”d”TBpÐ¢õ$DU"%’"æf–æ—6†VEöBDU42Ä”Ô•B"""Â‡F–6¶W"çWW"‚’Â’’æfWF6†öæR‚Ð¢–bæ÷B'Vã Ð¢&WGW&âæöæPÐ¢'Våö–BÒ'Vå²''Våö–B%ÐÐ¢FV6—6–öâÒ2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒ–çfW7FÖVçEöFV6—6–öç2t„U$R'Våö–CÓò"ÀÐ¢‡'Våö–BÂ’’æfWF6†öæR‚Ð¢&W6V&6‚Ò2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒ&W6V&6…ö÷WGWG2t„U$R'Våö–CÓò"ÀÐ¢‡'Våö–BÂ’’æfWF6†öæR‚Ð¢6æ6†÷BÒ2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒÖ&¶WE÷6æ6†÷G2t„U$R'Våö–CÓòäBF–6¶W#Óò"ÀÐ¢‡'Våö–BÂF–6¶W"çWW"‚’’’æfWF6†öæR‚Ð¢Öæ–fW7BÒ2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒ'VåöÖæ–fW7G2t„U$R'Våö–CÓò"ÀÐ¢‡'Våö–BÂ’’æfWF6†öæR‚Ð¢6W'F–f–6F–öâÒ2æW†V7WFR‚%4TÄT5B–ÆöEö§6öâe$ôÒ6W'F–f–6F–öå÷&V6÷&G2t„U$R'Våö–CÓò"ÀÐ¢‡'Våö–BÂ’’æfWF6†öæR‚Ð¢&WGW&â²''Vâ#¢F–7B‡'Vâ’ÀÐ¢&FV6—6–öâ#¢§6öâæÆöG2†FV6—6–öå³Ò’–bFV6—6–öâVÇ6RæöæRÀÐ¢'&W6V&6‚#¢§6öâæÆöG2‡&W6V&6…³Ò’–b&W6V&6‚VÇ6RæöæRÀÐ¢&Ö&¶WB#¢§6öâæÆöG2‡6æ6†÷E³Ò’–b6æ6†÷BVÇ6RæöæRÀÐ¢&Öæ–fW7B#¢§6öâæÆöG2†Öæ–fW7E³Ò’–bÖæ–fW7BVÇ6RæöæRÀÐ¢&6W'F–f–6F–öâ#¢§6öâæÆöG2†6W'F–f–6F–öå³Ò’–b6W'F–f–6F–öâVÇ6RæöæWÐÐ Ð¢FVb&V6÷&Eö¶æ÷vÆVFvU÷7–æ2‡6VÆbÂ'Våö–C¢7G"ÂF–6¶W#¢7G"Â7FGW3¢7G"ÀÐ¢fVÇE÷Fƒ¢7G"ÂW'&÷#¢7G"Ò""’ÓâæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚""$”å4U%B”åDò¶æ÷vÆVFvU÷7–æ0Ð¢‡'Våö–BÇF–6¶W"Ç7FGW2ÇfVÇE÷F‚ÆW'&÷"Ç7–æ6VEöB’dÅTU2ƒòÃòÃòÃòÃòÃò’"""ÀÐ¢‡'Våö–BÂF–6¶W"Â7FGW2ÂfVÇE÷F‚ÂW'&÷"Âæ÷uö—6ò‚’’Ð Ð¢FVb&WVW7Eö6æ6VÆÆF–öâ‡6VÆbÂ'Våö–C¢7G"Â&V6öã¢7G"Ò%U4U%õ$UTU5B"’ÓâæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚""$”å4U%B”åDò'Våö6æ6VÆÆF–öç2‡'Våö–BÇ7FGW2Ç&WVW7FVEöBÇ&V6öâÐ¢dÅTU2ƒòÃòÃòÃò’ôâ4ôädÄ”5B‡'Våö–B’DòUDDR4UB7FGW3Òt4ä4TÅõ$UTU5DTBrÀÐ¢&WVW7FVEöCÖW†6ÇVFVBç&WVW7FVEöBÇ&V6öãÖW†6ÇVFVBç&V6öâ"""ÀÐ¢‡'Våö–BÂ$4ä4TÅõ$UTU5DTB"Âæ÷uö—6ò‚’Â&V6öâ’Ð¢2æW†V7WFR‚%UDDRæÇ—6—5÷'Vç24UB6æ6VÆÆF–öå÷7FGW3Òt4ä4TÅõ$UTU5DTBrt„U$R'Våö–CÓò"ÀÐ¢‡'Våö–BÂ’Ð¢2æW†V7WFR‚%UDDR¦ö%÷VWVR4UB6æ6VÅ÷&WVW7FVCÓt„U$R'Våö–CÓò"Â‡'Våö–BÂ’Ð Ð¢FVb&WVW7Eö6æ6VÆÆF–öåöf÷%÷F–6¶W'2‡6VÆbÂF–6¶W'3¢Æ—7E·7G%Ò’ÓâÆ—7E·7G%Ó Ð¢–bæ÷BF–6¶W'3 Ð¢&WGW&âµÐÐ¢Æ6V†öÆFW'2Ò"Â"æ¦ö–â‚#ò"f÷"ò–âF–6¶W'2Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢&÷w2Ò2æW†V7WFR†b""%4TÄT5B'Våö–Be$ôÒæÇ—6—5÷'Vç2t„U$RF–6¶W"”â‡·Æ6V†öÆFW'7ÒÐ¢äB7FGW2”â‚u%Tää”ärrÂuTUTTBr’"""ÂGWÆR‡fÇVRçWW"‚’f÷"fÇVR–âF–6¶W'2’’æfWF6†ÆÂ‚Ð¢f÷"&÷r–â&÷w3 Ð¢6VÆbç&WVW7Eö6æ6VÆÆF–öâ‡&÷u²''Våö–B%ÒÐ¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢VWVVBÒ2æW†V7WFR‚%4TÄT5B¦ö%ö–BÇ–ÆöEö§6öâe$ôÒ¦ö%÷VWVRt„U$R7FGW3ÒuTUTTBr"’æfWF6†ÆÂ‚Ð¢vçFVBÒ·fÇVRçWW"‚’f÷"fÇVR–âF–6¶W'7ÐÐ¢f÷"¦ö"–âVWVVC Ð¢–ÆöBÒ§6öâæÆöG2†¦ö%²'–ÆöEö§6öâ%ÒÐ¢–bvçFVBæ–çFW'6V7F–öâ‡fÇVRçWW"‚’f÷"fÇVR–â–ÆöBævWB‚'F–6¶W'2"ÂµÒ’“ Ð¢2æW†V7WFR‚%UDDR¦ö%÷VWVR4UB7FGW3Òt4ä4TÄÄTBrÆ6æ6VÅ÷&WVW7FVCÓÆf–æ—6†VEöCÓòt„U$R¦ö%ö–CÓò"ÀÐ¢†æ÷uö—6ò‚’Â¦ö%²&¦ö%ö–B%Ò’Ð¢2æW†V7WFR‚%UDDRW6W%÷&WVW7G24UB7FGW3Òt4ä4TÄÄTBrÇWFFVEöCÓòt„U$R&WVW7Eö–CÓò"ÀÐ¢†æ÷uö—6ò‚’Â–ÆöBævWB‚'&WVW7Eö–B"Â""’’Ð¢&WGW&â·&÷u²''Våö–B%Òf÷"&÷r–â&÷w5ÐÐ Ð¢FVb6æ6VÅö¦ö"‡6VÆbÂ¦ö%ö–C¢7G"’ÓâæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚%UDDR¦ö%÷VWVR4UB7FGW3Òt4ä4TÄÄTBrÆ6æ6VÅ÷&WVW7FVCÓÆf–æ—6†VEöCÓòt„U$R¦ö%ö–CÓò"ÀÐ¢†æ÷uö—6ò‚’Â¦ö%ö–B’Ð Ð¢FVb—5ö¦ö%ö6æ6VÆÆVB‡6VÆbÂ¦ö%ö–C¢7G"’Óâ&ööÃ Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢&÷rÒ2æW†V7WFR‚%4TÄT5B7FGW2Æ6æ6VÅ÷&WVW7FVBe$ôÒ¦ö%÷VWVRt„U$R¦ö%ö–CÓò"ÀÐ¢†¦ö%ö–BÂ’’æfWF6†öæR‚Ð¢&WGW&â&ööÂ‡&÷ræB‡&÷u²'7FGW2%ÒÓÒ$4ä4TÄÄTB"÷"&÷u²&6æ6VÅ÷&WVW7FVB%Ò’Ð Ð¢FVb—5ö6æ6VÅ÷&WVW7FVB‡6VÆbÂ'Våö–C¢7G"’Óâ&ööÃ Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢&÷rÒ2æW†V7WFR‚%4TÄT5B7FGW2e$ôÒ'Våö6æ6VÆÆF–öç2t„U$R'Våö–CÓò"Â‡'Våö–BÂ’’æfWF6†öæR‚Ð¢&WGW&â&ööÂ‡&÷ræB&÷u²'7FGW2%ÒÓÒ$4ä4TÅõ$UTU5DTB"Ð Ð¢FVb6¶æ÷vÆVFvUö6æ6VÆÆF–öâ‡6VÆbÂ'Våö–C¢7G"’ÓâæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚""$”å4U%B”åDò'Våö6æ6VÆÆF–öç2‡'Våö–BÇ7FGW2Æ6¶æ÷vÆVFvVEöBÐ¢dÅTU2ƒòÃòÃò’ôâ4ôädÄ”5B‡'Våö–B’DòUDDR4UB7FGW3Òt4ä4TÄÄTBrÀÐ¢6¶æ÷vÆVFvVEöCÖW†6ÇVFVBæ6¶æ÷vÆVFvVEöB"""Â‡'Våö–BÂ$4ä4TÄÄTB"Âæ÷uö—6ò‚’’Ð¢2æW†V7WFR‚""%UDDRæÇ—6—5÷'Vç24UB7FGW3Òt4ä4TÄÄTBrÆf–æ—6†VEöCÓòÀÐ¢6æ6VÆÆF–öå÷7FGW3Òt4ä4TÄÄTBrt„U$R'Våö–CÓò"""Â†æ÷uö—6ò‚’Â'Våö–B’Ð¢2æW†V7WFR‚%UDDR¦ö%÷VWVR4UB7FGW3Òt4ä4TÄÄTBrÆf–æ—6†VEöCÓòt„U$R'Våö–CÓò"ÀÐ¢†æ÷uö—6ò‚’Â'Våö–B’Ð Ð¢FVbVçVWVUö¦ö"‡6VÆbÂ&WVW7C¢ç’Â&–÷&—G“¢–çBÒ’Óâ7G# Ð¢–ÆöBÒ6F–7B‡&WVW7B’–b—5öFF6Æ72‡&WVW7B’VÇ6RF–7B‡&WVW7BÐ¢–FVçF—G’Ò'Â"æ¦ö–â‚€Ð¢7G"‡–ÆöBævWB‚&F—66÷&EöÖW76vUö–B"’÷"–ÆöBævWB‚'&WVW7Eö–B"’÷"""’ÀÐ¢7G"‡–ÆöBævWB‚&–çFVçB"’÷"""’ÀÐ¢"Â"æ¦ö–â‡6÷'FVB‡7G"‡fÇVR’çWW"‚’f÷"fÇVR–â–ÆöBævWB‚'F–6¶W'2"ÂµÒ’’’ÀÐ¢’Ð¢7F&ÆUö¶W’Ò†6†Æ–"ç6†#Sb†–FVçF—G’æVæ6öFR‚'WFbÓ‚"’’æ†W†F–vW7B‚Ð¢¦ö%ö–BÒb$¤ô%÷·7F&ÆUö¶W•³£3%×Ò Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚""$”å4U%B”åDò¦ö%÷VWVPÐ¢†¦ö%ö–BÇ&WVW7Eö–BÇ–ÆöEö§6öâÇ7FGW2Ç&–÷&—G’Æ7&VFVEöBÆ6æ6VÅ÷&WVW7FVBÀÐ¢GFV×BÇ7F&ÆUö¶W’Ð¢dÅTU2ƒòÃòÃòÃòÃòÃòÃÃÃò’ôâ4ôädÄ”5BDòäõD„”är"""ÀÐ¢†¦ö%ö–BÂ–ÆöE²'&WVW7Eö–B%ÒÂ§6öâæGV×2‡–ÆöBÂVç7W&Uö66–“ÔfÇ6R’ÀÐ¢%TUTTB"Â–çB‡&–÷&—G’’Âæ÷uö—6ò‚’Â7F&ÆUö¶W’’Ð¢&÷rÒ2æW†V7WFR‚%4TÄT5B¦ö%ö–Be$ôÒ¦ö%÷VWVRt„U$R7F&ÆUö¶W“Óò"Â‡7F&ÆUö¶W’Â’’æfWF6†öæR‚Ð¢–b&÷r—2æöæS Ð¢&—6R'VçF–ÖTW'&÷"‚&f–ÆVBFòW'6—7B7F&ÆRVWVR–FVçF—G’"Ð¢¦ö%ö–BÒ7G"‡&÷u³ÒÐ¢&WGW&â¦ö%ö–@Ð Ð¢FVb7F'Eö¦ö"‡6VÆbÂ¦ö%ö–C¢7G"Â'Våö–C¢7G"Ò""ÂÆV6Uö÷væW#¢7G"Ò""ÀÐ¢ÆV6U÷6V6öæG3¢–çBÒ“’Óâ&ööÃ Ð¢÷væW"ÒÆV6Uö÷væW"÷"b'v÷&¶W#§µõö–×÷'Eõò‚v÷2r’ævWG–B‚—Ò Ð¢æ÷rÒFFWF–ÖRææ÷r‡F–ÖW¦öæRçWF2Ð¢ÆV6U÷VçF–ÂÒ†æ÷r²F–ÖVFVÇF‡6V6öæG3ÖÖ‚ƒ3Â–çB†ÆV6U÷6V6öæG2’’’’æ—6öf÷&ÖB‚Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢7W'6÷"Ò2æW†V7WFR‚""%UDDR¦ö%÷VWVR4UB7FGW3Òu%Tää”ärrÀÐ¢'Våö–CÔ44Rt„TâóÒrrD„Tâ'Våö–BTÅ4RòTäBÀÐ¢7F'FVEöCÔ4ôÄU44R‡7F'FVEöBÃò’Æ†V'F&VEöCÓòÆGFV×CÖGFV×B³ÀÐ¢ÆV6Uö÷væW#ÓòÆÆV6U÷VçF–ÃÓðÐ¢t„U$R¦ö%ö–CÓòäB7FGW3ÒuTUTTBräB6æ6VÅ÷&WVW7FVCÓ"""ÀÐ¢‡'Våö–BÂ'Våö–BÂæ÷ræ—6öf÷&ÖB‚’Âæ÷ræ—6öf÷&ÖB‚’Â÷væW"ÂÆV6U÷VçF–ÂÂ¦ö%ö–B’Ð¢&WGW&â7W'6÷"ç&÷v6÷VçBÓÒÐ Ð¢FVb†V'F&VEö¦ö"‡6VÆbÂ¦ö%ö–C¢7G"Â'Våö–C¢7G"Ò""ÂÆV6U÷6V6öæG3¢–çBÒ“’ÓâæöæS ¢ÆV6U÷VçF–ÂÒ†FFWF–ÖRææ÷r‡F–ÖW¦öæRçWF2’°¢F–ÖVFVÇF‡6V6öæG3ÖÖ‚ƒ3Â–çB†ÆV6U÷6V6öæG2’’’’æ—6öf÷&ÖB‚¢v—F‚6VÆbæ6öææV7B‚’23 ¢2æW†V7WFR‚""%UDDR¦ö%÷VWVR4UB†V'F&VEöCÓòÆÆV6U÷VçF–ÃÓòÀ¢'Våö–CÔ44Rt„TâóÒrrD„Tâ'Våö–BTÅ4RòTä@¢t„U$R¦ö%ö–CÓòäB7FGW3Òu%Tää”ärr"""À¢†æ÷uö—6ò‚’ÂÆV6U÷VçF–ÂÂ'Våö–BÂ'Våö–BÂ¦ö%ö–B’ Ð¢FVbf–æ—6…ö¦ö"‡6VÆbÂ¦ö%ö–C¢7G"Â7FGW3¢7G"ÂW'&÷#¢7G"Ò""’ÓâæöæS Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚""%UDDR¦ö%÷VWVR4UB7FGW3ÓòÆf–æ—6†VEöCÓòÆÆ7EöW'&÷#ÓòÀÐ¢ÆV6Uö÷væW#ÔåTÄÂÆÆV6U÷VçF–ÃÔåTÄÂt„U$R¦ö%ö–CÓò"""ÀÐ¢‡7FGW2Âæ÷uö—6ò‚’ÂW'&÷"Â¦ö%ö–B’Ð Ð¢FVb&V6÷fW&&ÆUö¦ö'2‡6VÆbÂÖ…öGFV×G3¢–çBÒ"’ÓâÆ—7E¶F–7E·7G"Âç•ÕÓ ¢æ÷rÒæ÷uö—6ò‚¢v—F‚6VÆbæ6öææV7B‚’23 ¢2æW†V7WFR‚""%UDDR¦ö%÷VWVR4UB7FGW3ÒuTUTTBrÇ7F'FVEöCÔåTÄÂÆ†V'F&VEöCÔåTÄÂÀ¢ÆV6Uö÷væW#ÔåTÄÂÆÆV6U÷VçF–ÃÔåTÄÂt„U$R7FGW3Òu%Tää”ärräBGFV×CÃð¢äBÆV6U÷VçF–Â•2äõBåTÄÂäBÆV6U÷VçF–ÃÃÓò"""À¢†Ö…öGFV×G2Âæ÷r’¢2æW†V7WFR‚""%UDDR¦ö%÷VWVR4UB7FGW3Òt$õ%DTBrÆf–æ—6†VEöCÓòÀ¢Æ7EöW'&÷#Òw&W7F'B&WG'’Æ–Ö—B&V6†VBrÆÆV6Uö÷væW#ÔåTÄÂÆÆV6U÷VçF–ÃÔåTÄÀ¢t„U$R7FGW3Òu%Tää”ärräBGFV×CãÓð¢äBÆV6U÷VçF–Â•2äõBåTÄÂäBÆV6U÷VçF–ÃÃÓò"""À¢†æ÷rÂÖ…öGFV×G2Âæ÷r’¢&÷w2Ò2æW†V7WFR‚""%4TÄT5B¢e$ôÒ¦ö%÷VWVRt„U$R7FGW3ÒuTUTTBräB6æ6VÅ÷&WVW7FVCÓ Ð¢õ$DU"%’&–÷&—G’Æ7&VFVEöB"""’æfWF6†ÆÂ‚Ð¢&WGW&â¶F–7B‡&÷r’Â²'–ÆöB#¢§6öâæÆöG2‡&÷u²'–ÆöEö§6öâ%Ò—Òf÷"&÷r–â&÷w5ÐÐ Ð¢FVb÷6fU÷–ÆöB‡6VÆbÂF&ÆS¢7G"ÂfÇVW3¢GWÆU´ç’ÂââåÒÂ6öÇVÖç3¢7G"’ÓâæöæS Ð¢–bF&ÆRÒ&Ö&¶WE÷6æ6†÷G2"÷"6öÇVÖç2Ò''Våö–BÇF–6¶W"ÇF–ÖW7F×Ç–ÆöEö§6öâ# Ð¢&—6RfÇVTW'&÷"‚'Vç7W÷'FVB–ÆöBF&ÆR"Ð¢v—F‚6VÆbæ6öææV7B‚’23 Ð¢2æW†V7WFR‚""$”å4U%B”åDòÖ&¶WE÷6æ6†÷G2‡'Våö–BÇF–6¶W"ÇF–ÖW7F×Ç–ÆöEö§6öâÐ¢dÅTU2ƒòÃòÃòÃò’ôâ4ôädÄ”5B‡'Våö–BÇF–6¶W"’DòUDDR4U@Ð¢F–ÖW7F×ÖW†6ÇVFVBçF–ÖW7F×Ç–ÆöEö§6öãÖW†6ÇVFVBç–ÆöEö§6öâ"""ÂfÇVW2Ð