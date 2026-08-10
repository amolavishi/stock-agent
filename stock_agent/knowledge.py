from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .schemas import CompanyState
from .security import redact_secrets
from .validation import validate_ticker


class UnsafeVaultPathError(ValueError):
    pass


class ObsidianKnowledgeManager:
    def __init__(self, vault_path: str, enabled: bool = True,
                 companies_dir: str = "02_Companies", reports_dir: str = "05_Reports",
                 decision_log_dir: str = "06_Decision_Log"):
        self.enabled = bool(enabled)
        self.root = Path(vault_path).resolve()
        self.companies_root = self._configured_dir(companies_dir)
        self.reports_root = self._configured_dir(reports_dir)
        self.decision_log_root = self._configured_dir(decision_log_dir)
        if self.enabled:
            self.root.mkdir(parents=True, exist_ok=True)

    def _configured_dir(self, value: str) -> Path:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts or ".obsidian" in {
                part.lower() for part in relative.parts}:
            raise UnsafeVaultPathError(f"unsafe Vault subdirectory: {value}")
        return self._inside_root(self.root / relative)

    def _inside_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise UnsafeVaultPathError(f"path escapes vault: {resolved}")
        try:
            relative_parts = resolved.relative_to(self.root).parts
        except ValueError:
            relative_parts = ()
        if ".obsidian" in {part.lower() for part in relative_parts}:
            raise UnsafeVaultPathError("access to .obsidian is forbidden")
        return resolved

    def company_dir(self, ticker: str) -> Path:
        ticker = validate_ticker(ticker)
        return self._inside_root(self.companies_root / ticker)

    def load_company_state(self, ticker: str) -> CompanyState | None:
        if not self.enabled:
            return None
        path = self._inside_root(self.company_dir(ticker) / "CompanyState.json")
        if not path.exists():
            return None
        return CompanyState(**json.loads(path.read_text(encoding="utf-8")))

    def update_company_state(self, state: CompanyState) -> Path:
        if not self.enabled:
            raise RuntimeError("Obsidian knowledge layer is disabled")
        path = self._inside_root(self.company_dir(state.ticker) / "CompanyState.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_latest_report(self, ticker: str) -> str | None:
        if not self.enabled:
            return None
        reports = self._inside_root(self.company_dir(ticker) / "Reports")
        if not reports.exists():
            return None
        files = sorted(reports.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0].read_text(encoding="utf-8") if files else None

    def load_latest_thesis(self, ticker: str) -> str | None:
        if not self.enabled:
            return None
        path = self._inside_root(self.company_dir(ticker) / "01_Current_Thesis.md")
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_report(self, ticker: str, run_id: str, report: str) -> Path:
        if not self.enabled:
            raise RuntimeError("Obsidian knowledge layer is disabled")
        ticker = validate_ticker(ticker)
        reports = self._inside_root(self.company_dir(ticker) / "Reports")
        reports.mkdir(parents=True, exist_ok=True)
        target = self._inside_root(reports / f"{run_id}.md")
        target.write_text(report, encoding="utf-8")
        return target

    def initialize_layout(self) -> None:
        if not self.enabled:
            return
        for path in (self.root / "00_System/Rules", self.root / "00_System/Prompts",
                     self.root / "00_System/Templates", self.root / "01_Market/Sector",
                     self.root / "01_Market/Macro", self.companies_root,
                     self.root / "03_Candidates", self.root / "04_Portfolio",
                     self.reports_root, self.decision_log_root, self.root / "99_Cache"):
            self._inside_root(path).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _managed_block(name: str, content: str) -> str:
        return f"<!-- STOCK_AGENT:{name}:BEGIN -->\n{content.rstrip()}\n<!-- STOCK_AGENT:{name}:END -->"

    def _write_managed(self, path: Path, name: str, content: str, title: str) -> Path:
        path = self._inside_root(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        start, end = f"<!-- STOCK_AGENT:{name}:BEGIN -->", f"<!-- STOCK_AGENT:{name}:END -->"
        existing = path.read_text(encoding="utf-8") if path.exists() else f"# {title}\n"
        block = self._managed_block(name, redact_secrets(content))
        if start in existing and end in existing:
            prefix, tail = existing.split(start, 1)
            _, suffix = tail.split(end, 1)
            updated = prefix.rstrip() + "\n\n" + block + suffix
        else:
            updated = existing.rstrip() + "\n\n" + block + "\n"
        path.write_text(updated, encoding="utf-8")
        return path

    def _append_unique_rows(self, path: Path, name: str, rows: list[str], title: str) -> Path:
        path = self._inside_root(path)
        existing = path.read_text(encoding="utf-8") if path.exists() else f"# {title}\n"
        start, end = f"<!-- STOCK_AGENT:{name}:BEGIN -->", f"<!-- STOCK_AGENT:{name}:END -->"
        managed = ""
        if start in existing and end in existing:
            managed = existing.split(start, 1)[1].split(end, 1)[0].strip()
        lines = managed.splitlines() if managed else []
        for row in rows:
            clean = redact_secrets(row).strip()
            if clean and clean not in lines:
                lines.append(clean)
        return self._write_managed(path, name, "\n".join(lines), title)

    def load_context(self, ticker: str, issue_topics: list[str] | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {}
        company = self.company_dir(ticker)
        core_path = self._inside_root(company / "Core.md")
        events_path = self._inside_root(company / "Event_History.md")
        evidence_path = self._inside_root(company / "Evidence_Index.md")
        core = core_path.read_text(encoding="utf-8")[:6000] if core_path.exists() else ""
        event_lines = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
        topics = [value.lower() for value in (issue_topics or []) if value]
        if topics:
            selected_events = [line for line in event_lines if any(topic in line.lower() for topic in topics)][-40:]
        else:
            selected_events = event_lines[-40:]
        evidence_lines = evidence_path.read_text(encoding="utf-8").splitlines()[-60:] if evidence_path.exists() else []
        return {"company_core": core, "relevant_historical_events": selected_events,
                "evidence_index": evidence_lines,
                "safety_note": "Historical values are not current state; mutable facts must be freshly verified."}

    def sync_run(self, ticker: str, run_id: str, state: CompanyState, evidence: list[Any],
                 research: Any, decision: Any, debate_state: Any, report_path: str | Path,
                 *, certification_status: str) -> list[Path]:
        if not self.enabled:
            return []
        if certification_status != "CERTIFIED":
            raise PermissionError(
                f"Obsidian knowledge write blocked for certification={certification_status}")
        self.initialize_layout()
        company = self.company_dir(ticker)
        company.mkdir(parents=True, exist_ok=True)
        verified_at = state.last_updated
        core = (f"- Ticker: `{ticker}` | source=`SYSTEM_IDENTITY` | verified_at=`{verified_at}` "
                "| certification=`CERTIFIED` | mutable_class=`IDENTITY`\n"
                f"- Sector: `{state.sector or 'UNKNOWN'}` | source=`SEC_COMPANY_STATE` "
                f"| verified_at=`{verified_at}` | certification=`CERTIFIED` "
                "| mutable_class=`STRUCTURAL_REVERIFY`\n"
                f"- SEC SIC: `{state.sic or 'UNKNOWN'}` | source=`SEC_COMPANY_STATE` "
                f"| verified_at=`{verified_at}` | certification=`CERTIFIED` "
                "| mutable_class=`STRUCTURAL`\n\n"
                "> This Core intentionally excludes current price, market regime, cash, shares, "
                "ATM/shelf capacity, guidance and market cap. Mutable facts require fresh verification.")
        paths = [self._write_managed(company / "Core.md", "CORE", core, f"{ticker} Core")]
        verified_evidence = [item for item in evidence
                             if not getattr(item, "is_mock", False)
                             and getattr(item, "evidence_grade", "") in {"A", "B"}]
        event_rows = [
            f"- {item.filed_at or item.published_at} | `{item.evidence_id}` | {item.document_type} | "
            "certification=`CERTIFIED` | mutable_class=`HISTORICAL_EVENT` | "
            f"{(item.normalized_fact or item.summary).replace(chr(10), ' ')[:500]}"
            for item in verified_evidence]
        paths.append(self._append_unique_rows(company / "Event_History.md", "EVENTS", event_rows,
                                              f"{ticker} Event History"))
        evidence_rows = [
            f"- `{item.evidence_id}` | {item.filed_at or item.published_at} | {item.source_type}/"
            f"{item.document_type} | Grade {item.evidence_grade} | Hash `{item.content_hash or 'N/A'}`"
            for item in verified_evidence]
        paths.append(self._append_unique_rows(company / "Evidence_Index.md", "EVIDENCE", evidence_rows,
                                              f"{ticker} Evidence Index"))
        thesis_row = (f"- {decision.timestamp} | Run `{run_id}` | `{decision.decision}` "
                      f"({decision.confidence}/100) | Debate `{getattr(debate_state, 'status', 'UNKNOWN')}` | "
                      "Final Guard 완료")
        paths.append(self._append_unique_rows(company / "Thesis_History.md", "THESIS", [thesis_row],
                                              f"{ticker} Thesis History"))
        source = Path(report_path)
        reports = self._inside_root(company / "Reports")
        reports.mkdir(parents=True, exist_ok=True)
        target = self._inside_root(reports / f"{run_id}.md")
        if not target.exists():
            target.write_text(redact_secrets(source.read_text(encoding="utf-8")), encoding="utf-8")
        paths.append(target)
        global_report = self._inside_root(self.reports_root / f"{ticker}_{run_id}.md")
        if not global_report.exists():
            global_report.write_text(redact_secrets(source.read_text(encoding="utf-8")), encoding="utf-8")
        paths.append(global_report)
        decision_path = self._inside_root(self.decision_log_root / f"{ticker}.md")
        decision_row = (f"- {decision.timestamp} | `{ticker}` | Run `{run_id}` | "
                        f"`{decision.decision}` ({decision.confidence}/100) | "
                        f"Debate `{getattr(debate_state, 'status', 'UNKNOWN')}`")
        paths.append(self._append_unique_rows(decision_path, "DECISIONS", [decision_row],
                                              f"{ticker} Decision Log"))
        return paths
