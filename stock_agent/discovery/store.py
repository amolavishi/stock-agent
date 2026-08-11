from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from ..database import Database
from .schemas import CandidateFeatureSnapshot, DailyBar


class DiscoveryStore:
    """SQLite persistence for Discovery; it has no write path to PAPER tables."""

    def __init__(self, database: Database):
        self.database = database

    def save_run(self, result: DiscoveryResult, started_at: str, finished_at: str = "") -> None:
        payload = result.to_dict()
        context = result.context
        with self.database.connect() as connection:
            connection.execute("""INSERT INTO discovery_runs(
                discovery_run_id,request_id,mode,requested_sector,intensity,as_of,rule_version,
                feature_version,code_sha,universe_snapshot_id,status,certification_status,
                coverage_pct,identity_coverage_pct,feature_coverage_pct,sector_coverage_pct,
                fundamental_enrichment_coverage_pct,capital_preflight_coverage_pct,
                final_selection,final_selection_status,final_selection_reason_codes_json,budget_json,
                started_at,finished_at,error_code,payload_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(discovery_run_id) DO UPDATE SET status=excluded.status,
                certification_status=excluded.certification_status,coverage_pct=excluded.coverage_pct,
                identity_coverage_pct=excluded.identity_coverage_pct,feature_coverage_pct=excluded.feature_coverage_pct,
                sector_coverage_pct=excluded.sector_coverage_pct,
                fundamental_enrichment_coverage_pct=excluded.fundamental_enrichment_coverage_pct,
                capital_preflight_coverage_pct=excluded.capital_preflight_coverage_pct,
                final_selection=excluded.final_selection,final_selection_status=excluded.final_selection_status,
                final_selection_reason_codes_json=excluded.final_selection_reason_codes_json,
                budget_json=excluded.budget_json,finished_at=excluded.finished_at,error_code=excluded.error_code,
                payload_json=excluded.payload_json""",
                (result.run_id, "", context.mode, context.requested_sector, context.intensity,
                 context.discovery_as_of, context.rule_version, context.feature_version,
                 context.code_sha, context.universe_snapshot_id, result.status,
                 result.certification_status, result.coverage.market_coverage_pct,
                 result.coverage.identity_coverage_pct, result.coverage.feature_coverage_pct,
                 result.coverage.sector_coverage_pct, result.coverage.fundamental_enrichment_coverage_pct,
                 result.coverage.capital_preflight_coverage_pct, result.final_selection,
                 result.final_selection_status, json.dumps(result.final_selection_reason_codes),
                 json.dumps(result.budget_status), started_at, finished_at, result.error_code,
                 json.dumps(payload, ensure_ascii=False)))
            for candidate in (result.all_candidates or result.candidates):
                self._save_candidate(connection, candidate)
                for scanner in result.scanner_counts:
                    if scanner in candidate.scanner_hits:
                        connection.execute("""INSERT OR REPLACE INTO scanner_hits(
                            discovery_run_id,ticker,scanner,scanner_version,hit,strength,
                            reason_codes_json,signal_families_json,unknown_fields_json,payload_json)
                            VALUES(?,?,?,?,?,?,?,?,?,?)""", (result.run_id, candidate.security.ticker,
                            scanner, "discovery_scanners_v001", 1, 0.0, "[]",
                            json.dumps(candidate.signal_families), json.dumps(candidate.unknown_fields),
                            json.dumps({"scanner": scanner})))
            for sector in result.sector_snapshots:
                connection.execute("""INSERT OR REPLACE INTO sector_snapshots(
                    discovery_run_id,sector_or_theme,feature_json,rotation_score,rotation_phase,coverage_pct)
                    VALUES(?,?,?,?,?,?)""", (result.run_id, sector["sector"], json.dumps(sector, ensure_ascii=False),
                                             sector.get("rotation_score"), sector.get("rotation_phase", "UNAVAILABLE"),
                                             sector.get("coverage_pct", 0)))
            packet = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            connection.execute("""INSERT OR REPLACE INTO discovery_packets(
                discovery_run_id,packet_hash,payload_json,created_at) VALUES(?,?,?,?)""",
                (result.run_id, hashlib.sha256(packet.encode()).hexdigest(), packet, context.discovery_as_of))
            if result.report_path:
                connection.execute("""INSERT OR REPLACE INTO report_artifacts(
                    run_id,ticker_label,markdown_path,publish_status,publish_attempts,last_error,created_at)
                    VALUES(?,?,?,?,?,?,?)""", (result.run_id, "DISCOVERY", result.report_path,
                    "PENDING", 0, "", context.discovery_as_of))

    @staticmethod
    def _save_candidate(connection, candidate: CandidateFeatureSnapshot) -> None:
        payload = json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True)
        connection.execute("""INSERT OR REPLACE INTO candidate_feature_snapshots(
            discovery_run_id,ticker,as_of,feature_version,payload_json,payload_hash)
            VALUES(?,?,?,?,?,?)""", (candidate.discovery_run_id, candidate.security.ticker,
                                      candidate.as_of, candidate.feature_version, payload, candidate.canonical_hash()))
        connection.execute("""INSERT OR REPLACE INTO discovery_candidates(
            discovery_run_id,ticker,stage,eligibility,discovery_bucket,composite_score,
            reason_codes_json,unknown_fields_json,risk_flags_json,payload_json,screen_layer,
            preflight_status,analysis_status,certification_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (candidate.discovery_run_id, candidate.security.ticker,
                                              candidate.stage, candidate.eligibility, candidate.discovery_bucket,
                                              candidate.composite_score, json.dumps(candidate.gate_results),
                                              json.dumps(candidate.unknown_fields), json.dumps(candidate.risk_flags), payload,
                                              "FUNDAMENTAL" if "primary_financial_evidence" in candidate.fields else "MARKET",
                                              "READY" if candidate.fields.get("capital_overhang_status") and candidate.fields["capital_overhang_status"].known else "NOT_FETCHED",
                                              "NOT_REQUESTED", "NOT_APPLICABLE"))

    def latest(self, run_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT payload_json FROM discovery_runs WHERE discovery_run_id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest_any(self) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT payload_json FROM discovery_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def save_analysis_link(self, discovery_run_id: str, ticker: str, analysis_run_id: str, created_at: str) -> None:
        with self.database.connect() as connection:
            connection.execute("""INSERT OR REPLACE INTO discovery_analysis_links(
                discovery_run_id,ticker,analysis_run_id,created_at) VALUES(?,?,?,?)""",
                (discovery_run_id, ticker, analysis_run_id, created_at))

    def save_universe(self, snapshot: dict) -> None:
        with self.database.connect() as connection:
            connection.execute("""INSERT OR REPLACE INTO universe_snapshots(
                universe_snapshot_id,as_of,mode,raw_count,accepted_count,rejected_json,payload_json,created_at)
                VALUES(?,?,?,?,?,?,?,?)""", (snapshot["snapshot_id"], snapshot["as_of"], "DISCOVERY",
                snapshot["raw_count"], len(snapshot["records"]), json.dumps(snapshot["rejected"]),
                json.dumps({"snapshot_id": snapshot["snapshot_id"], "as_of": snapshot["as_of"]}), snapshot["as_of"]))
            for record in snapshot["records"]:
                connection.execute("""INSERT OR REPLACE INTO universe_securities(
                    universe_snapshot_id,security_id,ticker,payload_json) VALUES(?,?,?,?)""",
                    (snapshot["snapshot_id"], record.security_id, record.ticker, json.dumps(record.to_dict(), ensure_ascii=False)))

    def save_bars(self, bars: list[DailyBar]) -> None:
        with self.database.connect() as connection:
            for bar in bars:
                connection.execute("""INSERT OR REPLACE INTO daily_bars(
                    ticker,session_date,open,high,low,close,adjusted_close,volume,source,
                    observed_at,ingested_at,quality_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (bar.ticker, bar.session_date, bar.open, bar.high, bar.low, bar.close,
                     bar.adjusted_close, bar.volume, bar.source, bar.observed_at, bar.ingested_at,
                     bar.quality_status))

    def load_bars(self, ticker: str, cutoff: str) -> list[DailyBar]:
        with self.database.connect() as connection:
            rows = connection.execute("""SELECT ticker,session_date,open,high,low,close,
                adjusted_close,volume,source,observed_at,ingested_at,quality_status
                FROM daily_bars WHERE ticker=? AND session_date<=? ORDER BY session_date""",
                (ticker, cutoff[:10])).fetchall()
        return [DailyBar(ticker=row[0], session_date=row[1], open=row[2], high=row[3], low=row[4],
                         close=row[5], adjusted_close=row[6], volume=row[7], source=row[8],
                         observed_at=row[9], ingested_at=row[10], quality_status=row[11]) for row in rows]
