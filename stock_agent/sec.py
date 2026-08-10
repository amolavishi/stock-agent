from __future__ import annotations

import json
import hashlib
import os
import time
import threading
import urllib.error
import urllib.request
from datetime import date
from typing import Any

from .schemas import EvidenceItem
from .validation import validate_ticker


def derive_standalone_quarter(ytd: dict[str, Any], q1: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if str(ytd.get("fp") or "").upper() != "Q2":
        failures.append("YTD_NOT_Q2")
    if str(q1.get("fp") or "").upper() != "Q1":
        failures.append("Q1_NOT_Q1")
    for field in ("concept", "taxonomy", "unit", "form", "fy", "entity", "entity_id", "scope"):
        if ytd.get(field) != q1.get(field):
            failures.append(f"{field.upper()}_MISMATCH")
    if str(ytd.get("form") or "").upper() != "10-Q":
        failures.append("YTD_FORM_NOT_10Q")
    if str(q1.get("form") or "").upper() != "10-Q":
        failures.append("Q1_FORM_NOT_10Q")
    if ytd.get("is_restatement") or q1.get("is_restatement"):
        failures.append("RESTATEMENT_FLAG_PRESENT")
    try:
        ytd_start, ytd_end = date.fromisoformat(str(ytd["start"])), date.fromisoformat(str(ytd["end"]))
        q1_start, q1_end = date.fromisoformat(str(q1["start"])), date.fromisoformat(str(q1["end"]))
        if not (ytd_start == q1_start and q1_end < ytd_end):
            failures.append("PERIOD_BOUNDARIES_NOT_NESTED")
        ytd_duration = (ytd_end - ytd_start).days
        q1_duration = (q1_end - q1_start).days
        if ytd_duration < q1_duration:
            failures.append("YTD_DURATION_SHORTER_THAN_Q1")
        if not 60 <= q1_duration <= 120:
            failures.append("Q1_DURATION_NOT_QUARTERLIKE")
        if not 120 <= ytd_duration <= 220:
            failures.append("YTD_DURATION_NOT_SIX_MONTH_LIKE")
    except (KeyError, TypeError, ValueError):
        failures.append("PERIOD_METADATA_MISSING")
    base = {
        "value": None,
        "status": "UNKNOWN_NOT_COMPARABLE" if failures else "KNOWN",
        "derived": True,
        "formula": "6M_YTD - Q1",
        "source_fact_ids": [ytd.get("fact_id", ""), q1.get("fact_id", "")],
        "as_of": ytd.get("end"),
        "method": "DETERMINISTIC_PERIOD_SUBTRACTION",
        "comparability": "FAILED" if failures else "PASSED",
        "rejection_reasons": failures,
    }
    if failures:
        return base
    return {
        "value": float(ytd["value"]) - float(q1["value"]),
        "status": "KNOWN",
        "derived": True,
        "formula": "6M_YTD - Q1",
        "source_fact_ids": [ytd["fact_id"], q1["fact_id"]],
        "as_of": ytd.get("end"),
        "method": "DETERMINISTIC_PERIOD_SUBTRACTION",
        "comparability": "PASSED",
        "rejection_reasons": [],
    }


class EdgarError(RuntimeError):
    pass


class EdgarMetadataCollector:
    """Collect filing metadata only; economic event strength remains UNCLASSIFIED."""

    TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
    ALLOWED_FORMS = {"8-K", "10-Q", "10-K", "S-1", "S-3", "S-4", "S-8",
                     "424B3", "424B5", "424B7", "424B8", "4", "144",
                     "13D", "13G", "13F", "20-F", "6-K"}
    _rate_lock = threading.Lock()
    _last_request_at = 0.0

    def __init__(self, user_agent: str | None = None, timeout: float = 15.0,
                 max_attempts: int = 3, max_rps: float = 4.0):
        self.user_agent = user_agent or os.getenv("SEC_USER_AGENT", "stock-agent/0.2 research@example.com")
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self.max_rps = max(0.5, min(float(max_rps), 5.0))

    def collect(self, ticker: str, limit: int = 8,
                target_forms: set[str] | None = None,
                date_from: str = "", date_to: str = "") -> list[EvidenceItem]:
        ticker = validate_ticker(ticker)
        cik = self._find_cik(ticker)
        payload = self._get_json(self.SUBMISSIONS_URL.format(cik=cik))
        recent = payload.get("filings", {}).get("recent", {})
        rows = zip(recent.get("form", []), recent.get("accessionNumber", []),
                   recent.get("filingDate", []), recent.get("primaryDocument", []))
        items: list[EvidenceItem] = []
        seen: set[str] = set()
        for form, accession_number, filing_date, document in rows:
            allowed = target_forms or self.ALLOWED_FORMS
            if form not in allowed or accession_number in seen:
                continue
            if date_from and filing_date < date_from:
                continue
            if date_to and filing_date > date_to:
                continue
            seen.add(accession_number)
            accession = accession_number.replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{document}"
            items.append(EvidenceItem(
                evidence_id=f"SEC_{ticker}_{accession}", ticker=ticker, source_type="SEC",
                document_type=form, published_at=filing_date, title=f"{ticker} Form {form}",
                source_url=filing_url, evidence_grade="UNCLASSIFIED", category="FILING_METADATA",
                summary=f"SEC filing metadata only: {form} filed on {filing_date}",
                facts={"accession_number": accession_number, "primary_document": document, "cik": cik},
                source_reliability="PRIMARY", data_quality="PARTIAL", is_mock=False,
                accession=accession_number, filed_at=filing_date,
                extraction_method="SEC_SUBMISSIONS_METADATA"))
            if len(items) >= limit:
                break
        return items

    def company_profile(self, ticker: str) -> dict[str, Any]:
        ticker = validate_ticker(ticker)
        cik = self._find_cik(ticker)
        payload = self._get_json(self.SUBMISSIONS_URL.format(cik=cik))
        return {"ticker": ticker, "cik": cik, "sic": str(payload.get("sic") or ""),
                "sic_description": str(payload.get("sicDescription") or ""),
                "name": str(payload.get("name") or ""),
                "fiscal_year_end": str(payload.get("fiscalYearEnd") or "")}

    def _find_cik(self, ticker: str) -> str:
        directory = self._get_json(self.TICKER_MAP_URL)
        for entry in directory.values():
            if entry.get("ticker", "").upper() == ticker:
                return str(entry["cik_str"]).zfill(10)
        raise EdgarError(f"SEC ticker not found: {ticker}")

    def _get_json(self, url: str) -> dict[str, Any]:
        self._throttle()
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept": "application/json"})
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if response.status != 200:
                        raise EdgarError(f"SEC HTTP status: {response.status}")
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # 401/403 are access failures. Retrying them only delays a clear diagnosis.
                if exc.code not in {429, 500, 502, 503, 504} or attempt == self.max_attempts:
                    code = "SEC_ACCESS_DENIED" if exc.code in {401, 403} else (
                        "SEC_RATE_LIMITED" if exc.code == 429 else "SEC_HTTP_ERROR")
                    raise EdgarError(f"{code}: HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == self.max_attempts:
                    raise EdgarError(f"SEC request failed after {attempt} attempts: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise EdgarError(f"SEC returned invalid JSON: {exc}") from exc
            time.sleep(0.5 * (2 ** (attempt - 1)))
        raise EdgarError("SEC request failed")

    def _throttle(self) -> None:
        interval = 1.0 / self.max_rps
        with self._rate_lock:
            wait = interval - (time.monotonic() - self.__class__._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self.__class__._last_request_at = time.monotonic()


EdgarCollector = EdgarMetadataCollector


class SECCompanyFactsProvider(EdgarMetadataCollector):
    """Structured XBRL facts. Filing narratives remain a separate provider."""

    FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    TAGS = {
        "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
        "gross_profit": ("GrossProfit",),
        "operating_income": ("OperatingIncomeLoss",),
        "cash": ("CashAndCashEquivalentsAtCarryingValue",),
        "net_income": ("NetIncomeLoss",),
        "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
        "shares_outstanding": ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"),
        "diluted_weighted_average_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        "stock_based_compensation": ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
        "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    }
    DEBT_CONCEPTS = {
        "short_term_borrowings": ("ShortTermBorrowings", "LongTermDebtCurrent"),
        "long_term_borrowings": ("LongTermDebtNoncurrent",),
        "finance_lease_liability": ("FinanceLeaseLiability", "FinanceLeaseLiabilityNoncurrent"),
        "operating_lease_liability": ("OperatingLeaseLiability", "OperatingLeaseLiabilityNoncurrent"),
        "convertible_debt": ("ConvertibleDebtCurrent", "ConvertibleDebtNoncurrent"),
        "pension_obligations": ("PensionLiabilitiesNoncurrent",),
        "other_contractual_obligations": ("OtherLiabilitiesCurrent", "OtherLiabilitiesNoncurrent"),
    }
    DURATION_METRICS = {
        "revenue", "gross_profit", "operating_income", "net_income",
        "operating_cash_flow", "stock_based_compensation", "capex",
    }

    @staticmethod
    def _latest(units: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
        rows = [row for values in units.values() for row in values if row.get("val") is not None]
        if not rows:
            return None
        return max(rows, key=lambda row: (str(row.get("end", "")), str(row.get("filed", ""))))

    def facts(self, ticker: str) -> dict[str, Any]:
        ticker = validate_ticker(ticker)
        cik = self._find_cik(ticker)
        payload = self._get_json(self.FACTS_URL.format(cik=cik))
        all_facts = payload.get("facts", {})
        us_gaap = all_facts.get("us-gaap", {})
        dei = all_facts.get("dei", {})
        output: dict[str, Any] = {"ticker": ticker, "cik": cik, "source": "SEC_COMPANYFACTS"}
        normalized = self._normalized_rows(payload)
        for name, tags in self.TAGS.items():
            selected = None
            for tag in tags:
                candidates = [row for row in normalized if row["concept"] == tag]
                selected = self._select_period_aware(candidates)
                if selected:
                    if name in self.DURATION_METRICS:
                        selected = self._resolve_duration_metric(candidates, selected)
                    else:
                        selected = self._direct_provenance(selected)
                    break
            output[name] = selected
        output["debt_ontology"] = self._debt_ontology(normalized)
        output["debt"] = output["debt_ontology"]["financial_debt"]
        output["normalized_facts"] = normalized
        output["derived"] = self._derive(output)
        output["raw_companyfacts_payload"] = payload
        return output

    @staticmethod
    def _normalized_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for taxonomy, concepts in payload.get("facts", {}).items():
            if taxonomy not in {"us-gaap", "dei"}:
                continue
            for concept, fact in concepts.items():
                for unit, rows in fact.get("units", {}).items():
                    for row in rows:
                        if row.get("val") is None:
                            continue
                        start, end = str(row.get("start") or ""), str(row.get("end") or "")
                        period_type = "DURATION" if start else "INSTANT"
                        identity = "|".join(str(value or "") for value in (
                            taxonomy, concept, unit, row.get("form"), row.get("fy"), row.get("fp"),
                            start, end, row.get("filed"), row.get("accn"), row.get("frame"), row.get("val")))
                        output.append({
                            "fact_id": f"XBRL_{hashlib.sha256(identity.encode()).hexdigest()[:24]}",
                            "taxonomy": taxonomy, "concept": concept, "unit": unit,
                            "form": row.get("form"), "fy": row.get("fy"), "fp": row.get("fp"),
                            "start": start, "end": end, "filed": row.get("filed"),
                            "accn": row.get("accn"), "value": row.get("val"),
                            "frame": row.get("frame"), "period_type": period_type,
                            "entity": row.get("entity"),
                            "entity_id": row.get("entity_id"),
                            "scope": row.get("scope"),
                        })
        return output

    @classmethod
    def _debt_ontology(cls, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for category, concepts in cls.DEBT_CONCEPTS.items():
            candidates = [row for row in rows
                          if row.get("concept") in concepts and row.get("period_type") == "INSTANT"]
            selected = cls._select_period_aware(candidates)
            if selected is None:
                result[category] = {"value": None, "status": "UNKNOWN_NOT_AVAILABLE",
                                    "derived": False, "method": "SEC_XBRL_INSTANT_ONLY"}
            else:
                result[category] = dict(selected) | {
                    "status": "KNOWN", "derived": False,
                    "source_id": selected.get("fact_id"), "as_of": selected.get("end"),
                    "method": "SEC_XBRL_INSTANT_ONLY",
                }
        included = ("short_term_borrowings", "long_term_borrowings", "convertible_debt")
        known = [result[name] for name in included if result[name].get("value") is not None]
        if known:
            result["financial_debt"] = {
                "value": sum(float(item["value"]) for item in known), "status": "KNOWN",
                "derived": True, "method": "SUM_BORROWINGS_AND_CONVERTIBLE",
                "source_fact_ids": [item.get("fact_id") or item.get("source_id") for item in known],
                "as_of": max(str(item.get("end") or item.get("as_of") or "") for item in known),
            }
        else:
            result["financial_debt"] = {
                "value": None, "status": "UNKNOWN_NOT_AVAILABLE", "derived": True,
                "method": "SUM_BORROWINGS_AND_CONVERTIBLE", "source_fact_ids": [],
            }
        result["debt_like_obligations"] = {
            "value": (sum(float(result[name]["value"]) for name in
                          ("finance_lease_liability", "operating_lease_liability", "pension_obligations")
                          if result[name].get("value") is not None) or None),
            "status": "KNOWN" if any(result[name].get("value") is not None for name in
                                      ("finance_lease_liability", "operating_lease_liability",
                                       "pension_obligations")) else "UNKNOWN_NOT_AVAILABLE",
            "derived": True, "method": "SUM_DEBT_LIKE_OBLIGATIONS",
        }
        return result

    @staticmethod
    def _select_period_aware(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not rows:
            return None
        instants = [row for row in rows if row["period_type"] == "INSTANT"]
        if instants:
            return max(instants, key=lambda row: (str(row.get("end") or ""),
                                                  str(row.get("filed") or "")))
        q2_direct = [row for row in rows
                     if SECCompanyFactsProvider._is_standalone_quarter(row)
                     and str(row.get("fp") or "").upper() == "Q2"]
        if q2_direct:
            return max(q2_direct, key=lambda row: (str(row.get("end") or ""),
                                                   str(row.get("filed") or "")))
        ytd_q2 = [row for row in rows if SECCompanyFactsProvider._is_q2_ytd(row)]
        if ytd_q2:
            return max(ytd_q2, key=lambda row: (str(row.get("end") or ""),
                                                str(row.get("filed") or "")))
        quarterly = [row for row in rows if SECCompanyFactsProvider._is_standalone_quarter(row)]
        candidates = quarterly or rows
        return max(candidates, key=lambda row: (str(row.get("end") or ""),
                                                str(row.get("filed") or "")))

    @staticmethod
    def _period_days(row: dict[str, Any]) -> int | None:
        try:
            return (date.fromisoformat(str(row["end"])) -
                    date.fromisoformat(str(row["start"]))).days
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def _is_standalone_quarter(cls, row: dict[str, Any]) -> bool:
        days = cls._period_days(row)
        return (row.get("period_type") == "DURATION" and
                str(row.get("form") or "").upper() == "10-Q" and
                str(row.get("fp") or "").upper() in {"Q1", "Q2", "Q3"} and
                days is not None and 60 <= days <= 120)

    @classmethod
    def _is_q2_ytd(cls, row: dict[str, Any]) -> bool:
        days = cls._period_days(row)
        return (row.get("period_type") == "DURATION" and
                str(row.get("form") or "").upper() == "10-Q" and
                str(row.get("fp") or "").upper() == "Q2" and
                days is not None and 120 <= days <= 220)

    @staticmethod
    def _direct_provenance(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result.update({
            "status": "KNOWN",
            "derived": False,
            "method": "SEC_XBRL_DIRECT_FACT",
            "provenance": {
                "status": "KNOWN",
                "method": "SEC_XBRL_DIRECT_FACT",
                "source_fact_ids": [row.get("fact_id", "")],
                "source_accessions": [row.get("accn", "")] if row.get("accn") else [],
                "as_of": row.get("end"),
            },
        })
        return result

    @classmethod
    def _resolve_duration_metric(cls, candidates: list[dict[str, Any]],
                                 selected: dict[str, Any]) -> dict[str, Any]:
        if cls._is_standalone_quarter(selected):
            return cls._direct_provenance(selected)
        if not cls._is_q2_ytd(selected):
            return cls._direct_provenance(selected)
        q1_candidates = [row for row in candidates if cls._is_standalone_quarter(row)
                         and str(row.get("fp") or "").upper() == "Q1"]
        q1 = max(q1_candidates, key=lambda row: (str(row.get("end") or ""),
                                                  str(row.get("filed") or "")),
                 default={})
        derived = derive_standalone_quarter(selected, q1)
        source_ids = list(derived.get("source_fact_ids") or [])
        provenance = {
            "status": derived["status"],
            "method": derived["method"],
            "formula": derived["formula"],
            "source_fact_ids": source_ids,
            "source_accessions": [value for value in
                                   (selected.get("accn"), q1.get("accn")) if value],
            "as_of": derived.get("as_of"),
            "comparability": derived["comparability"],
            "rejection_reasons": list(derived.get("rejection_reasons") or []),
        }
        identity = "|".join(str(value) for value in source_ids)
        result = dict(selected) | derived
        result["fact_id"] = f"DERIVED_XBRL_{hashlib.sha256(identity.encode()).hexdigest()[:24]}"
        result["provenance"] = provenance
        result["source_fact_ids"] = source_ids
        return result

    @staticmethod
    def _derive(output: dict[str, Any]) -> dict[str, Any]:
        def value(name: str) -> float | None:
            row = output.get(name)
            try:
                return float(row["value"]) if row and row.get("value") is not None else None
            except (TypeError, ValueError):
                return None
        cash, debt, revenue, gross = value("cash"), value("debt"), value("revenue"), value("gross_profit")
        ocf, capex = value("operating_cash_flow"), value("capex")
        burn = None
        if ocf is not None:
            burn = max(0.0, -ocf + (capex or 0.0))
        runway = round(cash / burn * 12, 2) if cash is not None and burn and burn > 0 else None
        return {"gross_margin_pct": round(gross / revenue * 100, 4)
                if gross is not None and revenue and revenue != 0 else None,
                "net_cash": cash - debt if cash is not None and debt is not None else None,
                "cash_burn": burn, "estimated_runway_months": runway}
