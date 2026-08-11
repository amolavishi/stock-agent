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
        # Discovery consumes this same period-aware resolver output.  Keeping
        # the ontology here prevents a second, weaker CompanyFacts resolver
        # from inventing growth or margin semantics downstream.
        output["period_metrics"] = self._period_metrics(normalized)
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
    def _series(cls, rows: list[dict[str, Any]], concept: str) -> list[dict[str, Any]]:
        candidates = [row for row in rows if row.get("concept") == concept and
                      row.get("period_type") == "DURATION" and
                      str(row.get("form") or "").upper() == "10-Q"]
        if not candidates:
            return []
        units = {str(row.get("unit") or "") for row in candidates}
        if len(units) != 1:
            return []
        selected: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in candidates:
            if not cls._is_standalone_quarter(row):
                continue
            key = (str(row.get("fy") or ""), str(row.get("fp") or ""), str(row.get("end") or ""))
            current = selected.get(key)
            if current is None or str(row.get("filed") or "") > str(current.get("filed") or ""):
                selected[key] = row
        series = list(selected.values())
        # If a dir×Ž4¶‰žËkºwµç@€€€€€€€€€€É½Ü¹•Ð ‰Á•É¥½‘}ÑåÁ”ˆ¤€ôô€‰UIQ%=8ˆ…¹(€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™½É´ˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôô€ˆÄÀµD‰t(€€€€€€€ÕÉÉ•¹Ñ}É•Ù•¹Õ”€ô9½¹”(€€€€€€€™½È½¹•ÁÐ¥¸€ ‰I•Ù•¹Õ•É½µ½¹ÑÉ…Ñ]¥Ñ¡ÕÍÑ½µ•Éá±Õ‘¥¹ÍÍ•ÍÍ•‘Q…àˆ°€‰I•Ù•¹Õ•Ìˆ¤è(€€€€€€€€€€€½¹•ÁÑ}É½ÝÌ€ômÉ½Ü™½ÈÉ½Ü¥¸É•Ù•¹Õ•}…¹‘¥‘…Ñ•Ì¥˜É½Ü¹•Ð ‰½¹•ÁÐˆ¤€ôô½¹•ÁÑt(€€€€€€€€€€€¥˜½¹•ÁÑ}É½ÝÌè(€€€€€€€€€€€€€€€Í•±•Ñ•€ô±Ì¹}Í•±•Ñ}Á•É¥½‘}…Ý…É”¡½¹•ÁÑ}É½ÝÌ¤(€€€€€€€€€€€€€€€ÕÉÉ•¹Ñ}É•Ù•¹Õ”€ô±Ì¹}É•Í½±Ù•}‘ÕÉ…Ñ¥½¹}µ•ÑÉ¥Œ¡½¹•ÁÑ}É½ÝÌ°Í•±•Ñ•¤¥˜Í•±•Ñ••±Í”9½¹”(€€€€€€€€€€€€€€€¥˜ÕÉÉ•¹Ñ}É•Ù•¹Õ”è(€€€€€€€€€€€€€€€€€€€‰É•…¬(€€€€€€€¥˜ÕÉÉ•¹Ñ}É•Ù•¹Õ”è(€€€€€€€€€€€ÕÉÉ•¹Ñ}™ä€ôÍÑÈ¡ÕÉÉ•¹Ñ}É•Ù•¹Õ”¹•Ð ‰™äˆ¤½È€ˆˆ¤(€€€€€€€€€€€ÕÉÉ•¹Ñ}™À€ôÍÑÈ¡ÕÉÉ•¹Ñ}É•Ù•¹Õ”¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤(€€€€€€€€€€€Í…µ•}™ä€ômÉ½Ü™½ÈÉ½Ü¥¸É•Ù•¹Õ•}…¹‘¥‘…Ñ•Ì¥˜ÍÑÈ¡É½Ü¹•Ð ‰™äˆ¤½È€ˆˆ¤€ôôÕÉÉ•¹Ñ}™ä…¹(€€€€€€€€€€€€€€€€€€€€€€±Ì¹}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡É½Ü¤…¹ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤€ðÍÑÈ¡ÕÉÉ•¹Ñ}É•Ù•¹Õ”¹•Ð ‰•¹ˆ¤½È€ˆˆ¥t(€€€€€€€€€€€ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”€ôµ…à¡Í…µ•}™ä°­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤°‘•™…Õ±Ðõ9½¹”¤(€€€€€€€€€€€ÑÉäè(€€€€€€€€€€€€€€€ÁÉ¥½É}™ä€ôÍÑÈ¡¥¹Ð¡ÕÉÉ•¹Ñ}™ä¤€´€Ä¤(€€€€€€€€€€€•á•ÁÐ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è(€€€€€€€€€€€€€€€ÁÉ¥½É}™ä€ô€ˆˆ(€€€€€€€€€€€ÁÉ¥½É}ÕÉÉ•¹Ð€ôµ…à¡mÉ½Ü™½ÈÉ½Ü¥¸É•Ù•¹Õ•}…¹‘¥‘…Ñ•Ì¥˜ÍÑÈ¡É½Ü¹•Ð ‰™äˆ¤½È€ˆˆ¤€ôôÁÉ¥½É}™ä…¹(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôôÕÉÉ•¹Ñ}™À…¹±Ì¹}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡É½Ü¥t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤°‘•™…Õ±Ðõ9½¹”¤(€€€€€€€€€€€ÁÉ•Ù¥½ÕÍ}™À€ôÍÑÈ¡ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤¥˜ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”•±Í”€ˆˆ(€€€€€€€€€€€ÁÉ¥½É}ÁÉ•Ù¥½ÕÌ€ôµ…à¡mÉ½Ü™½ÈÉ½Ü¥¸É•Ù•¹Õ•}…¹‘¥‘…Ñ•Ì¥˜ÍÑÈ¡É½Ü¹•Ð ‰™äˆ¤½È€ˆˆ¤€ôôÁÉ¥½É}™ä…¹(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôôÁÉ•Ù¥½ÕÍ}™À…¹±Ì¹}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡É½Ü¥t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤°‘•™…Õ±Ðõ9½¹”¤(€€€€€€€€€€€¥˜€¡½µÁ…Ñ¥‰±”¡ÕÉÉ•¹Ñ}É•Ù•¹Õ”°ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”¤…¹½µÁ…Ñ¥‰±”¡ÁÉ¥½É}ÕÉÉ•¹Ð°ÁÉ¥½É}ÁÉ•Ù¥½ÕÌ¤…¹(€€€€€€€€€€€€€€€€€€€ÕÉÉ•¹Ñ}É•Ù•¹Õ”¹•Ð ‰Õ¹¥Ðˆ¤€ôôÁÉ¥½É}ÕÉÉ•¹Ð¹•Ð ‰Õ¹¥Ðˆ¤…¹(€€€€€€€€€€€€€€€€€€€ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”¹•Ð ‰Õ¹¥Ðˆ¤€ôôÁÉ¥½É}ÁÉ•Ù¥½ÕÌ¹•Ð ‰Õ¹¥Ðˆ¤¤è(€€€€€€€€€€€€€€€Œ°À°ÁŒ°ÁÀ€ô€¡É…Ý}Ù…±Õ”¡ÕÉÉ•¹Ñ}É•Ù•¹Õ”¤°É…Ý}Ù…±Õ”¡ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€É…Ý}Ù…±Õ”¡ÁÉ¥½É}ÕÉÉ•¹Ð¤°É…Ý}Ù…±Õ”¡ÁÉ¥½É}ÁÉ•Ù¥½ÕÌ¤¤(€€€€€€€€€€€€€€€¥˜Œ¥Ì¹½Ð9½¹”…¹À¹½Ð¥¸€¡9½¹”°€À¤…¹ÁŒ¥Ì¹½Ð9½¹”…¹ÁÀ¹½Ð¥¸€¡9½¹”°€À¤è(€€€€€€€€€€€€€€€€€€€ÕÉÉ•¹Ñ}É½ÝÑ €ô€¡Œ€¼ÁŒ€´€Ä¤€¨€ÄÀÀ(€€€€€€€€€€€€€€€€€€€ÁÉ•Ù¥½ÕÍ}É½ÝÑ €ô€¡À€¼ÁÀ€´€Ä¤€¨€ÄÀÀ(€€€€€€€€€€€€€€€€€€€µ•ÑÉ¥Ì¹ÕÁ‘…Ñ”¡ì‰É•Ù•¹Õ•}É½ÝÑ¡}ÕÉÉ•¹Ñ}ÁÐˆèÕÉÉ•¹Ñ}É½ÝÑ °(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É•Ù•¹Õ•}É½ÝÑ¡}ÁÉ•Ù¥½ÕÍ}ÁÐˆèÁÉ•Ù¥½ÕÍ}É½ÝÑ °(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É•Ù•¹Õ•}É½ÝÑ¡}…•±•É…Ñ¥½¹}ÁÀˆèÕÉÉ•¹Ñ}É½ÝÑ €´ÁÉ•Ù¥½ÕÍ}É½ÝÑ °(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰É•Ù•¹Õ•}É½ÝÑ¡}ÁÉ½Ù•¹…¹”ˆèì(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™…Ñ}¥‘ÌˆèmÉ½Ü¹•Ð ‰™…Ñ}¥ˆ¤™½ÈÉ½Ü¥¸(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€¡ÕÉÉ•¹Ñ}É•Ù•¹Õ”°ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”°ÁÉ¥½É}ÕÉÉ•¹Ð°ÁÉ¥½É}ÁÉ•Ù¥½ÕÌ¥t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…•ÍÍ¥½¹ÌˆèmÉ½Ü¹•Ð ‰…¸ˆ¤™½ÈÉ½Ü¥¸(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€¡ÕÉÉ•¹Ñ}É•Ù•¹Õ”°ÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”°ÁÉ¥½É}ÕÉÉ•¹Ð°ÁÉ¥½É}ÁÉ•Ù¥½ÕÌ¥t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÕÉÉ•¹Ñ}Á•É¥½ˆèÕÉÉ•¹Ñ}É•Ù•¹Õ”¹•Ð ‰•¹ˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÁÉ•Ù¥½ÕÍ}Á•É¥½ˆèÁÉ•Ù¥½ÕÍ}É•Ù•¹Õ”¹•Ð ‰•¹ˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰ÁÉ¥½É}å•…É}Á•É¥½‘ÌˆèmÁÉ¥½É}ÕÉÉ•¹Ð¹•Ð ‰•¹ˆ¤°ÁÉ¥½É}ÁÉ•Ù¥½ÕÌ¹•Ð ‰•¹ˆ¥t°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Á•É¥½‘}ÑåÁ”ˆèÕÉÉ•¹Ñ}É•Ù•¹Õ”¹•Ð ‰Á•É¥½‘}ÑåÁ”ˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Õ¹¥ÐˆèÕÉÉ•¹Ñ}É•Ù•¹Õ”¹•Ð ‰Õ¹¥Ðˆ¥õô¤((€€€€€€€‘•˜µ…É¥¹}Á…¥È¡¹Õµ•É…Ñ½Èè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut¤€´øÑÕÁ±•m™±½…Ðð9½¹”°™±½…Ðð9½¹”°‘¥ÑmÍÑÈ°¹åtð9½¹•tè(€€€€€€€€€€€¹}ÕÉÉ•¹Ð°¹}ÁÉ•Ù¥½ÕÌ°|€ô½µÁ…É…‰±”¡¹Õµ•É…Ñ½È¤(€€€€€€€€€€€É}ÕÉÉ•¹Ð°É}ÁÉ•Ù¥½ÕÌ°|€ô½µÁ…É…‰±”¡É•Ù•¹Õ”¤(€€€€€€€€€€€¥˜¹½Ð½µÁ…Ñ¥‰±”¡¹}ÕÉÉ•¹Ð°¹}ÁÉ•Ù¥½ÕÌ¤½È¹½Ð½µÁ…Ñ¥‰±”¡É}ÕÉÉ•¹Ð°É}ÁÉ•Ù¥½ÕÌ¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”°9½¹”°9½¹”(€€€€€€€€€€€¥˜¹}ÕÉÉ•¹Ð¹•Ð ‰•¹ˆ¤€„ôÉ}ÕÉÉ•¹Ð¹•Ð ‰•¹ˆ¤½È¹}ÁÉ•Ù¥½ÕÌ¹•Ð ‰•¹ˆ¤€„ôÉ}ÁÉ•Ù¥½ÕÌ¹•Ð ‰•¹ˆ¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”°9½¹”°9½¹”(€€€€€€€€€€€¥˜¹}ÕÉÉ•¹Ð¹•Ð ‰Õ¹¥Ðˆ¤€„ôÉ}ÕÉÉ•¹Ð¹•Ð ‰Õ¹¥Ðˆ¤è(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”°9½¹”°9½¹”(€€€€€€€€€€€ÉÙ}Œ°ÉÙ}À€ôÉ…Ý}Ù…±Õ”¡É}ÕÉÉ•¹Ð¤°É…Ý}Ù…±Õ”¡É}ÁÉ•Ù¥½ÕÌ¤(€€€€€€€€€€€¹Ù}Œ°¹Ù}À€ôÉ…Ý}Ù…±Õ”¡¹}ÕÉÉ•¹Ð¤°É…Ý}Ù…±Õ”¡¹}ÁÉ•Ù¥½ÕÌ¤(€€€€€€€€€€€¥˜ÉÙ}Œ¥¸€¡9½¹”°€À¤½ÈÉÙ}À¥¸€¡9½¹”°€À¤½È¹Ù}Œ¥Ì9½¹”½È¹Ù}À¥Ì9½¹”è(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”°9½¹”°9½¹”(€€€€€€€€€€€É•ÑÕÉ¸¹Ù}Œ€¼ÉÙ}Œ€¨€ÄÀÀ°¹Ù}À€¼ÉÙ}À€¨€ÄÀÀ°ì(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™…Ñ}¥‘Ìˆèm¹}ÕÉÉ•¹Ð¹•Ð ‰™…Ñ}¥ˆ¤°¹}ÁÉ•Ù¥½ÕÌ¹•Ð ‰™…Ñ}¥ˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€É}ÕÉÉ•¹Ð¹•Ð ‰™…Ñ}¥ˆ¤°É}ÁÉ•Ù¥½ÕÌ¹•Ð ‰™…Ñ}¥ˆ¥t°(€€€€€€€€€€€€€€€€‰ÕÉÉ•¹Ñ}Á•É¥½ˆè¹}ÕÉÉ•¹Ð¹•Ð ‰•¹ˆ¤°€‰ÁÉ•Ù¥½ÕÍ}Á•É¥½ˆè¹}ÁÉ•Ù¥½ÕÌ¹•Ð ‰•¹ˆ¥ô((€€€€€€€É½ÍÍ}ÕÉÉ•¹Ð°É½ÍÍ}ÁÉ•Ù¥½ÕÌ°É½ÍÍ}ÁÉ½Ù•¹…¹”€ôµ…É¥¹}Á…¥È¡É½ÍÌ¤(€€€€€€€½Á}ÕÉÉ•¹Ð°½Á}ÁÉ•Ù¥½ÕÌ°½Á}ÁÉ½Ù•¹…¹”€ôµ…É¥¹}Á…¥È¡½Á•É…Ñ¥¹œ¤(€€€€€€€µ•ÑÉ¥Ì¹ÕÁ‘…Ñ”¡ì(€€€€€€€€€€€€‰É½ÍÍ}µ…É¥¹}ÕÉÉ•¹Ñ}ÁÐˆèÉ½ÍÍ}ÕÉÉ•¹Ð°(€€€€€€€€€€€€‰É½ÍÍ}µ…É¥¹}ÁÉ•Ù¥½ÕÍ}ÁÐˆèÉ½ÍÍ}ÁÉ•Ù¥½ÕÌ°(€€€€€€€€€€€€‰É½ÍÍ}µ…É¥¹}‘•±Ñ…}ÁÀˆè€¡É½ÍÍ}ÕÉÉ•¹Ð€´É½ÍÍ}ÁÉ•Ù¥½ÕÌ¥˜É½ÍÍ}ÕÉÉ•¹Ð¥Ì¹½Ð9½¹”…¹É½ÍÍ}ÁÉ•Ù¥½ÕÌ¥Ì¹½Ð9½¹”•±Í”9½¹”¤°(€€€€€€€€€€€€‰É½ÍÍ}µ…É¥¹}ÁÉ½Ù•¹…¹”ˆèÉ½ÍÍ}ÁÉ½Ù•¹…¹”°(€€€€€€€€€€€€‰½Á•É…Ñ¥¹}µ…É¥¹}ÕÉÉ•¹Ñ}ÁÐˆè½Á}ÕÉÉ•¹Ð°(€€€€€€€€€€€€‰½Á•É…Ñ¥¹}µ…É¥¹}ÁÉ•Ù¥½ÕÍ}ÁÐˆè½Á}ÁÉ•Ù¥½ÕÌ°(€€€€€€€€€€€€‰½Á•É…Ñ¥¹}µ…É¥¹}‘•±Ñ…}ÁÀˆè€¡½Á}ÕÉÉ•¹Ð€´½Á}ÁÉ•Ù¥½ÕÌ¥˜½Á}ÕÉÉ•¹Ð¥Ì¹½Ð9½¹”…¹½Á}ÁÉ•Ù¥½ÕÌ¥Ì¹½Ð9½¹”•±Í”9½¹”¤°(€€€€€€€€€€€€‰½Á•É…Ñ¥¹}µ…É¥¹}ÁÉ½Ù•¹…¹”ˆè½Á}ÁÉ½Ù•¹…¹”°(€€€€€€€ô¤((€€€€€€€½™}ÕÉÉ•¹Ð°½™}ÁÉ•Ù¥½ÕÌ°|€ô½µÁ…É…‰±”¡½˜¥lèÍt(€€€€€€€½™}Œ°½™}À€ôÉ…Ý}Ù…±Õ”¡½™}ÕÉÉ•¹Ð¤°É…Ý}Ù…±Õ”¡½™}ÁÉ•Ù¥½ÕÌ¤(€€€€€€€µ•ÑÉ¥Ì¹ÕÁ‘…Ñ”¡ì‰½Á•É…Ñ¥¹}…Í¡}™±½Ý}ÕÉÉ•¹Ðˆè½™}Œ°(€€€€€€€€€€€€€€€€€€€€€€€€‰½Á•É…Ñ¥¹}…Í¡}™±½Ý}ÁÉ•Ù¥½ÕÌˆè½™}À°(€€€€€€€€€€€€€€€€€€€€€€€€‰½Á•É…Ñ¥¹}…Í¡}™±½Ý}¥¹™±•Ñ¥½¸ˆè€¡½™}Œ€´½™}À¥˜½™}Œ¥Ì¹½Ð9½¹”…¹½™}À¥Ì¹½Ð9½¹”•±Í”9½¹”¥ô¤(€€€€€€€™™}ÕÉÉ•¹Ð°™™}ÁÉ•Ù¥½ÕÌ°|€ô½µÁ…É…‰±”¡…Á•à¥lèÍt(€€€€€€€…Á}Œ°…Á}À€ôÉ…Ý}Ù…±Õ”¡™™}ÕÉÉ•¹Ð¤°É…Ý}Ù…±Õ”¡™™}ÁÉ•Ù¥½ÕÌ¤(€€€€€€€¥˜½™}Œ¥Ì¹½Ð9½¹”…¹…Á}Œ¥Ì¹½Ð9½¹”…¹½™}À¥Ì¹½Ð9½¹”…¹…Á}À¥Ì¹½Ð9½¹”è(€€€€€€€€€€€™™}Œ°™™}À€ô½™}Œ€´…Á}Œ°½™}À€´…Á}À(€€€€€€€•±Í”è(€€€€€€€€€€€™™}Œ€ô™™}À€ô9½¹”(€€€€€€€µ•ÑÉ¥Ì¹ÕÁ‘…Ñ”¡ì‰™™}ÕÉÉ•¹Ðˆè™™}Œ°€‰™™}ÁÉ•Ù¥½ÕÌˆè™™}À°(€€€€€€€€€€€€€€€€€€€€€€€€‰™™}¥¹™±•Ñ¥½¸ˆè€¡™™}Œ€´™™}À¥˜™™}Œ¥Ì¹½Ð9½¹”…¹™™}À¥Ì¹½Ð9½¹”•±Í”9½¹”¥ô¤(€€€€€€€É•ÑÕÉ¸µ•ÑÉ¥Ì(4(€€€±…ÍÍµ•Ñ¡½4(€€€‘•˜}‘•‰Ñ}½¹Ñ½±½ä¡±Ì°É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut¤€´ø‘¥ÑmÍÑÈ°‘¥ÑmÍÑÈ°¹åutè4(€€€€€€€É•ÍÕ±Ðè‘¥ÑmÍÑÈ°‘¥ÑmÍÑÈ°¹åut€ôíô4(€€€€€€€™½È…Ñ•½Éä°½¹•ÁÑÌ¥¸±Ì¹	Q}=9AQL¹¥Ñ•µÌ ¤è4(€€€€€€€€€€€…¹‘¥‘…Ñ•Ì€ômÉ½Ü™½ÈÉ½Ü¥¸É½ÝÌ4(€€€€€€€€€€€€€€€€€€€€€€€€€¥˜É½Ü¹•Ð ‰½¹•ÁÐˆ¤¥¸½¹•ÁÑÌ…¹É½Ü¹•Ð ‰Á•É¥½‘}ÑåÁ”ˆ¤€ôô€‰%9MQ9P‰t4(€€€€€€€€€€€Í•±•Ñ•€ô±Ì¹}Í•±•Ñ}Á•É¥½‘}…Ý…É”¡…¹‘¥‘…Ñ•Ì¤4(€€€€€€€€€€€¥˜Í•±•Ñ•¥Ì9½¹”è4(€€€€€€€€€€€€€€€É•ÍÕ±Ñm…Ñ•½Éåt€ôì‰Ù…±Õ”ˆè9½¹”°€‰ÍÑ…ÑÕÌˆè€‰U9-9=]9}9=Q}Y%1	1ˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰‘•É¥Ù•ˆè…±Í”°€‰µ•Ñ¡½ˆè€‰M}a	I1}%9MQ9Q}=91d‰ô4(€€€€€€€€€€€•±Í”è4(€€€€€€€€€€€€€€€É•ÍÕ±Ñm…Ñ•½Éåt€ô‘¥Ð¡Í•±•Ñ•¤ðì4(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰-9=]8ˆ°€‰‘•É¥Ù•ˆè…±Í”°4(€€€€€€€€€€€€€€€€€€€€‰Í½ÕÉ•}¥ˆèÍ•±•Ñ•¹•Ð ‰™…Ñ}¥ˆ¤°€‰…Í}½˜ˆèÍ•±•Ñ•¹•Ð ‰•¹ˆ¤°4(€€€€€€€€€€€€€€€€€€€€‰µ•Ñ¡½ˆè€‰M}a	I1}%9MQ9Q}=91dˆ°4(€€€€€€€€€€€€€€€ô4(€€€€€€€¥¹±Õ‘•€ô€ ‰Í¡½ÉÑ}Ñ•Éµ}‰½ÉÉ½Ý¥¹Ìˆ°€‰±½¹}Ñ•Éµ}‰½ÉÉ½Ý¥¹Ìˆ°€‰½¹Ù•ÉÑ¥‰±•}‘•‰Ðˆ¤4(€€€€€€€­¹½Ý¸€ômÉ•ÍÕ±Ñm¹…µ•t™½È¹…µ”¥¸¥¹±Õ‘•¥˜É•ÍÕ±Ñm¹…µ•t¹•Ð ‰Ù…±Õ”ˆ¤¥Ì¹½Ð9½¹•t4(€€€€€€€¥˜­¹½Ý¸è4(€€€€€€€€€€€É•ÍÕ±Ñl‰™¥¹…¹¥…±}‘•‰Ð‰t€ôì4(€€€€€€€€€€€€€€€€‰Ù…±Õ”ˆèÍÕ´¡™±½…Ð¡¥Ñ•µl‰Ù…±Õ”‰t¤™½È¥Ñ•´¥¸­¹½Ý¸¤°€‰ÍÑ…ÑÕÌˆè€‰-9=]8ˆ°4(€€€€€€€€€€€€€€€€‰‘•É¥Ù•ˆèQÉÕ”°€‰µ•Ñ¡½ˆè€‰MU5}	=II=]%9M}9}=9YIQ%	1ˆ°4(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™…Ñ}¥‘Ìˆèm¥Ñ•´¹•Ð ‰™…Ñ}¥ˆ¤½È¥Ñ•´¹•Ð ‰Í½ÕÉ•}¥ˆ¤™½È¥Ñ•´¥¸­¹½Ý¹t°4(€€€€€€€€€€€€€€€€‰…Í}½˜ˆèµ…à¡ÍÑÈ¡¥Ñ•´¹•Ð ‰•¹ˆ¤½È¥Ñ•´¹•Ð ‰…Í}½˜ˆ¤½È€ˆˆ¤™½È¥Ñ•´¥¸­¹½Ý¸¤°4(€€€€€€€€€€€ô4(€€€€€€€•±Í”è4(€€€€€€€€€€€É•ÍÕ±Ñl‰™¥¹…¹¥…±}‘•‰Ð‰t€ôì4(€€€€€€€€€€€€€€€€‰Ù…±Õ”ˆè9½¹”°€‰ÍÑ…ÑÕÌˆè€‰U9-9=]9}9=Q}Y%1	1ˆ°€‰‘•É¥Ù•ˆèQÉÕ”°4(€€€€€€€€€€€€€€€€‰µ•Ñ¡½ˆè€‰MU5}	=II=]%9M}9}=9YIQ%	1ˆ°€‰Í½ÕÉ•}™…Ñ}¥‘Ìˆèmt°4(€€€€€€€€€€€ô4(€€€€€€€É•ÍÕ±Ñl‰‘•‰Ñ}±¥­•}½‰±¥…Ñ¥½¹Ì‰t€ôì4(€€€€€€€€€€€€‰Ù…±Õ”ˆè€¡ÍÕ´¡™±½…Ð¡É•ÍÕ±Ñm¹…µ•ul‰Ù…±Õ”‰t¤™½È¹…µ”¥¸4(€€€€€€€€€€€€€€€€€€€€€€€€€€ ‰™¥¹…¹•}±•…Í•}±¥…‰¥±¥Ñäˆ°€‰½Á•É…Ñ¥¹}±•…Í•}±¥…‰¥±¥Ñäˆ°€‰Á•¹Í¥½¹}½‰±¥…Ñ¥½¹Ìˆ¤4(€€€€€€€€€€€€€€€€€€€€€€€€€¥˜É•ÍÕ±Ñm¹…µ•t¹•Ð ‰Ù…±Õ”ˆ¤¥Ì¹½Ð9½¹”¤½È9½¹”¤°4(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰-9=]8ˆ¥˜…¹ä¡É•ÍÕ±Ñm¹…µ•t¹•Ð ‰Ù…±Õ”ˆ¤¥Ì¹½Ð9½¹”™½È¹…µ”¥¸4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ ‰™¥¹…¹•}±•…Í•}±¥…‰¥±¥Ñäˆ°€‰½Á•É…Ñ¥¹}±•…Í•}±¥…‰¥±¥Ñäˆ°4(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€‰Á•¹Í¥½¹}½‰±¥…Ñ¥½¹Ìˆ¤¤•±Í”€‰U9-9=]9}9=Q}Y%1	1ˆ°4(€€€€€€€€€€€€‰‘•É¥Ù•ˆèQÉÕ”°€‰µ•Ñ¡½ˆè€‰MU5}	Q}1%-}=	1%Q%=9Lˆ°4(€€€€€€€ô4(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð4(4(€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}Í•±•Ñ}Á•É¥½‘}…Ý…É”¡É½ÝÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut¤€´ø‘¥ÑmÍÑÈ°¹åtð9½¹”è(€€€€€€€¥˜¹½ÐÉ½ÝÌè(€€€€€€€€€€€É•ÑÕÉ¸9½¹”(€€€€€€€¥¹ÍÑ…¹ÑÌ€ômÉ½Ü™½ÈÉ½Ü¥¸É½ÝÌ¥˜É½Ýl‰Á•É¥½‘}ÑåÁ”‰t€ôô€‰%9MQ9P‰t(€€€€€€€¥˜¥¹ÍÑ…¹ÑÌè(€€€€€€€€€€€É•ÑÕÉ¸µ…à¡¥¹ÍÑ…¹ÑÌ°­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤¤(€€€€€€€ÄÉ}‘¥É•Ð€ômÉ½Ü™½ÈÉ½Ü¥¸É½ÝÌ(€€€€€€€€€€€€€€€€€€€€¥˜M½µÁ…¹å…ÑÍAÉ½Ù¥‘•È¹}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡É½Ü¤(€€€€€€€€€€€€€€€€€€€€…¹ÍÑÈ¡É½Ü¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôô€‰DÈ‰t(€€€€€€€¥˜ÄÉ}‘¥É•Ðè(€€€€€€€€€€€É•ÑÕÉ¸µ…à¡ÄÉ}‘¥É•Ð°­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤¤(€€€€€€€åÑ‘}ÄÈ€ômÉ½Ü™½ÈÉ½Ü¥¸É½ÝÌ¥˜M½µÁ…¹å…ÑÍAÉ½Ù¥‘•È¹}¥Í}ÄÉ}åÑ¡É½Ü¥t(€€€€€€€¥˜åÑ‘}ÄÈè(€€€€€€€€€€€É•ÑÕÉ¸µ…à¡åÑ‘}ÄÈ°­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤¤(€€€€€€€ÅÕ…ÉÑ•É±ä€ômÉ½Ü™½ÈÉ½Ü¥¸É½ÝÌ¥˜M½µÁ…¹å…ÑÍAÉ½Ù¥‘•È¹}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡É½Ü¥t(€€€€€€€…¹‘¥‘…Ñ•Ì€ôÅÕ…ÉÑ•É±ä½ÈÉ½ÝÌ(€€€€€€€É•ÑÕÉ¸µ…à¡…¹‘¥‘…Ñ•Ì°­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}Á•É¥½‘}‘…åÌ¡É½Üè‘¥ÑmÍÑÈ°¹åt¤€´ø¥¹Ðð9½¹”è(€€€€€€€ÑÉäè(€€€€€€€€€€€É•ÑÕÉ¸€¡‘…Ñ”¹™É½µ¥Í½™½Éµ…Ð¡ÍÑÈ¡É½Ýl‰•¹‰t¤¤€´(€€€€€€€€€€€€€€€€€€€‘…Ñ”¹™É½µ¥Í½™½Éµ…Ð¡ÍÑÈ¡É½Ýl‰ÍÑ…ÉÐ‰t¤¤¤¹‘…åÌ(€€€€€€€•á•ÁÐ€¡-•åÉÉ½È°QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è(€€€€€€€€€€€É•ÑÕÉ¸9½¹”((€€€±…ÍÍµ•Ñ¡½(€€€‘•˜}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡±Ì°É½Üè‘¥ÑmÍÑÈ°¹åt¤€´ø‰½½°è(€€€€€€€‘…åÌ€ô±Ì¹}Á•É¥½‘}‘…åÌ¡É½Ü¤(€€€€€€€É•ÑÕÉ¸€¡É½Ü¹•Ð ‰Á•É¥½‘}ÑåÁ”ˆ¤€ôô€‰UIQ%=8ˆ…¹(€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™½É´ˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôô€ˆÄÀµDˆ…¹(€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤¥¸ì‰DÄˆ°€‰DÈˆ°€‰DÌ‰ô…¹(€€€€€€€€€€€€€€€‘…åÌ¥Ì¹½Ð9½¹”…¹€ØÀ€ðô‘…åÌ€ðô€ÄÈÀ¤((€€€±…ÍÍµ•Ñ¡½(€€€‘•˜}¥Í}ÄÉ}åÑ¡±Ì°É½Üè‘¥ÑmÍÑÈ°¹åt¤€´ø‰½½°è(€€€€€€€‘…åÌ€ô±Ì¹}Á•É¥½‘}‘…åÌ¡É½Ü¤(€€€€€€€É•ÑÕÉ¸€¡É½Ü¹•Ð ‰Á•É¥½‘}ÑåÁ”ˆ¤€ôô€‰UIQ%=8ˆ…¹(€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™½É´ˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôô€ˆÄÀµDˆ…¹(€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôô€‰DÈˆ…¹(€€€€€€€€€€€€€€€‘…åÌ¥Ì¹½Ð9½¹”…¹€ÄÈÀ€ðô‘…åÌ€ðô€ÈÈÀ¤((€€€ÍÑ…Ñ¥µ•Ñ¡½(€€€‘•˜}‘¥É•Ñ}ÁÉ½Ù•¹…¹”¡É½Üè‘¥ÑmÍÑÈ°¹åt¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€É•ÍÕ±Ð€ô‘¥Ð¡É½Ü¤(€€€€€€€É•ÍÕ±Ð¹ÕÁ‘…Ñ”¡ì(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰-9=]8ˆ°(€€€€€€€€€€€€‰‘•É¥Ù•ˆè…±Í”°(€€€€€€€€€€€€‰µ•Ñ¡½ˆè€‰M}a	I1}%IQ}Pˆ°(€€€€€€€€€€€€‰ÁÉ½Ù•¹…¹”ˆèì(€€€€€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰-9=]8ˆ°(€€€€€€€€€€€€€€€€‰µ•Ñ¡½ˆè€‰M}a	I1}%IQ}Pˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}™…Ñ}¥‘ÌˆèmÉ½Ü¹•Ð ‰™…Ñ}¥ˆ°€ˆˆ¥t°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}…•ÍÍ¥½¹ÌˆèmÉ½Ü¹•Ð ‰…¸ˆ°€ˆˆ¥t¥˜É½Ü¹•Ð ‰…¸ˆ¤•±Í”mt°(€€€€€€€€€€€€€€€€‰…Í}½˜ˆèÉ½Ü¹•Ð ‰•¹ˆ¤°(€€€€€€€€€€€ô°(€€€€€€€ô¤(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð((€€€±…ÍÍµ•Ñ¡½(€€€‘•˜}É•Í½±Ù•}‘ÕÉ…Ñ¥½¹}µ•ÑÉ¥Œ¡±Ì°…¹‘¥‘…Ñ•Ìè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€Í•±•Ñ•è‘¥ÑmÍÑÈ°¹åt¤€´ø‘¥ÑmÍÑÈ°¹åtè(€€€€€€€¥˜±Ì¹}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡Í•±•Ñ•¤è(€€€€€€€€€€€É•ÑÕÉ¸±Ì¹}‘¥É•Ñ}ÁÉ½Ù•¹…¹”¡Í•±•Ñ•¤(€€€€€€€¥˜¹½Ð±Ì¹}¥Í}ÄÉ}åÑ¡Í•±•Ñ•¤è(€€€€€€€€€€€É•ÑÕÉ¸±Ì¹}‘¥É•Ñ}ÁÉ½Ù•¹…¹”¡Í•±•Ñ•¤(€€€€€€€ÄÅ}…¹‘¥‘…Ñ•Ì€ômÉ½Ü™½ÈÉ½Ü¥¸…¹‘¥‘…Ñ•Ì¥˜±Ì¹}¥Í}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡É½Ü¤(€€€€€€€€€€€€€€€€€€€€€€€€…¹ÍÑÈ¡É½Ü¹•Ð ‰™Àˆ¤½È€ˆˆ¤¹ÕÁÁ•È ¤€ôô€‰DÄ‰t(€€€€€€€ÄÄ€ôµ…à¡ÄÅ}…¹‘¥‘…Ñ•Ì°­•äõ±…µ‰‘„É½Üè€¡ÍÑÈ¡É½Ü¹•Ð ‰•¹ˆ¤½È€ˆˆ¤°(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€ÍÑÈ¡É½Ü¹•Ð ‰™¥±•ˆ¤½È€ˆˆ¤¤°(€€€€€€€€€€€€€€€€‘•™…Õ±Ðõíô¤(€€€€€€€‘•É¥Ù•€ô‘•É¥Ù•}ÍÑ…¹‘…±½¹•}ÅÕ…ÉÑ•È¡Í•±•Ñ•°ÄÄ¤(€€€€€€€Í½ÕÉ•}¥‘Ì€ô±¥ÍÐ¡‘•É¥Ù•¹•Ð ‰Í½ÕÉ•}™…Ñ}¥‘Ìˆ¤½Èmt¤(€€€€€€€ÁÉ½Ù•¹…¹”€ôì(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè‘•É¥Ù•‘l‰ÍÑ…ÑÕÌ‰t°(€€€€€€€€€€€€‰µ•Ñ¡½ˆè‘•É¥Ù•‘l‰µ•Ñ¡½‰t°(€€€€€€€€€€€€‰™½ÉµÕ±„ˆè‘•É¥Ù•‘l‰™½ÉµÕ±„‰t°(€€€€€€€€€€€€‰Í½ÕÉ•}™…Ñ}¥‘ÌˆèÍ½ÕÉ•}¥‘Ì°(€€€€€€€€€€€€‰Í½ÕÉ•}…•ÍÍ¥½¹ÌˆèmÙ…±Õ”™½ÈÙ…±Õ”¥¸(€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€€¡Í•±•Ñ•¹•Ð ‰…¸ˆ¤°ÄÄ¹•Ð ‰…¸ˆ¤¤¥˜Ù…±Õ•t°(€€€€€€€€€€€€‰…Í}½˜ˆè‘•É¥Ù•¹•Ð ‰…Í}½˜ˆ¤°(€€€€€€€€€€€€‰½µÁ…É…‰¥±¥Ñäˆè‘•É¥Ù•‘l‰½µÁ…É…‰¥±¥Ñä‰t°(€€€€€€€€€€€€‰É•©•Ñ¥½¹}É•…Í½¹Ìˆè±¥ÍÐ¡‘•É¥Ù•¹•Ð ‰É•©•Ñ¥½¹}É•…Í½¹Ìˆ¤½Èmt¤°(€€€€€€€ô(€€€€€€€¥‘•¹Ñ¥Ñä€ô€‰ðˆ¹©½¥¸¡ÍÑÈ¡Ù…±Õ”¤™½ÈÙ…±Õ”¥¸Í½ÕÉ•}¥‘Ì¤(€€€€€€€É•ÍÕ±Ð€ô‘¥Ð¡Í•±•Ñ•¤ð‘•É¥Ù•(€€€€€€€É•ÍÕ±Ñl‰™…Ñ}¥‰t€ô˜‰I%Y}a	I1}í¡…Í¡±¥ˆ¹Í¡„ÈÔØ¡¥‘•¹Ñ¥Ñä¹•¹½‘” ¤¤¹¡•á‘¥•ÍÐ ¥lèÈÑuôˆ(€€€€€€€É•ÍÕ±Ñl‰ÁÉ½Ù•¹…¹”‰t€ôÁÉ½Ù•¹…¹”(€€€€€€€É•ÍÕ±Ñl‰Í½ÕÉ•}™…Ñ}¥‘Ì‰t€ôÍ½ÕÉ•}¥‘Ì(€€€€€€€É•ÑÕÉ¸É•ÍÕ±Ð(4(€€€ÍÑ…Ñ¥µ•Ñ¡½4(€€€‘•˜}‘•É¥Ù”¡½ÕÑÁÕÐè‘¥ÑmÍÑÈ°¹åt¤€´ø‘¥ÑmÍÑÈ°¹åtè4(€€€€€€€‘•˜Ù…±Õ”¡¹…µ”èÍÑÈ¤€´ø™±½…Ðð9½¹”è4(€€€€€€€€€€€É½Ü€ô½ÕÑÁÕÐ¹•Ð¡¹…µ”¤4(€€€€€€€€€€€ÑÉäè4(€€€€€€€€€€€€€€€É•ÑÕÉ¸™±½…Ð¡É½Ýl‰Ù…±Õ”‰t¤¥˜É½Ü…¹É½Ü¹•Ð ‰Ù…±Õ”ˆ¤¥Ì¹½Ð9½¹”•±Í”9½¹”4(€€€€€€€€€€€•á•ÁÐ€¡QåÁ•ÉÉ½È°Y…±Õ•ÉÉ½È¤è4(€€€€€€€€€€€€€€€É•ÑÕÉ¸9½¹”4(€€€€€€€…Í °‘•‰Ð°É•Ù•¹Õ”°É½ÍÌ€ôÙ…±Õ” ‰…Í ˆ¤°Ù…±Õ” ‰‘•‰Ðˆ¤°Ù…±Õ” ‰É•Ù•¹Õ”ˆ¤°Ù…±Õ” ‰É½ÍÍ}ÁÉ½™¥Ðˆ¤4(€€€€€€€½˜°…Á•à€ôÙ…±Õ” ‰½Á•É…Ñ¥¹}…Í¡}™±½Üˆ¤°Ù…±Õ” ‰…Á•àˆ¤4(€€€€€€€‰ÕÉ¸€ô9½¹”4(€€€€€€€¥˜½˜¥Ì¹½Ð9½¹”è4(€€€€€€€€€€€‰ÕÉ¸€ôµ…à À¸À°€µ½˜€¬€¡…Á•à½È€À¸À¤¤4(€€€€€€€ÉÕ¹Ý…ä€ôÉ½Õ¹¡…Í €¼‰ÕÉ¸€¨€ÄÈ°€È¤¥˜…Í ¥Ì¹½Ð9½¹”…¹‰ÕÉ¸…¹‰ÕÉ¸€ø€À•±Í”9½¹”4(€€€€€€€É•ÑÕÉ¸ì‰É½ÍÍ}µ…É¥¹}ÁÐˆèÉ½Õ¹¡É½ÍÌ€¼É•Ù•¹Õ”€¨€ÄÀÀ°€Ð¤4(€€€€€€€€€€€€€€€¥˜É½ÍÌ¥Ì¹½Ð9½¹”…¹É•Ù•¹Õ”…¹É•Ù•¹Õ”€„ô€À•±Í”9½¹”°4(€€€€€€€€€€€€€€€€‰¹•Ñ}…Í ˆè…Í €´‘•‰Ð¥˜…Í ¥Ì¹½Ð9½¹”…¹‘•‰Ð¥Ì¹½Ð9½¹”•±Í”9½¹”°4(€€€€€€€€€€€€€€€€‰…Í¡}‰ÕÉ¸ˆè‰ÕÉ¸°€‰•ÍÑ¥µ…Ñ•‘}ÉÕ¹Ý…å}µ½¹Ñ¡ÌˆèÉÕ¹Ý…åô4(