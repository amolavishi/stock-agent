"""V8 Discovery Recall Lite V1.5.

Derived from V8_DISCOVERY_RECALL_FORENSIC_AUDIT_2026-09-01.zip.
Discovery is recall-first and grade-blind. This module cannot create Research
Grade, PRE-A readiness, execution action, position size, or broker writes.

Hard invariants:
- LANE_TOUCHED != SCANNER_EXECUTED
- RAW_BREADTH != SIGNAL_COVERAGE
- DEEP_DIVE_YIELD_ZERO != SEARCH_EXHAUSTED
- UNKNOWN != NEGATIVE_EVIDENCE
- DISCOVERY_INSUFFICIENT != HARD_FAIL
- DEEP_DIVE_SECONDARY != PRE_A
- RESEARCH_VALUE != RESEARCH_GRADE
"""
from __future__ import annotations

import copy
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from typing import Any

from . import adapters as adapters_module
from . import alpha_bootstrap
from . import runtime as runtime_module
from .adapters import ProviderError, _artifact, _response_final_url, _validate_public_https_url
from .models import RunMode, RunOutcome, canonical_hash

DISCOVERY_RECALL_LITE_VERSION = "V8_DISCOVERY_RECALL_LITE_V1.5"
FORENSIC_AUDIT_ARCHIVE = "V8_DISCOVERY_RECALL_FORENSIC_AUDIT_2026-09-01.zip"
FORENSIC_AUDIT_SHA256 = "47494df8fd0464c3fb63c6f2a5facd7dd6296616bec635b6faebe15e4ddab616"
MIN_SIGNAL_COVERAGE = 150
PREFERRED_SIGNAL_COVERAGE = 250
DEFAULT_ADV_PROBE_TARGET = 1000
SCANNER_ROUND_SIZE = 50
SENTINEL_SAMPLE_SIZE = 30
PREFERRED_MAX_MARKET_CAP = 20_000_000_000.0

SCANNERS: dict[str, dict[str, Any]] = {
    "02": {"name": "NON_AI_NON_SEMI_BROAD_BLIND", "keywords": (), "focus": "broad non-AI/non-semi price-lag/fundamental/catalyst search"},
    "03": {"name": "RECENT_IPO_BUSTED_IPO_REVALUATION", "keywords": ("ipo",), "focus": "recent/busted IPO dislocation and operating bridge"},
    "04": {"name": "TURNAROUND_EARNINGS", "keywords": (), "focus": "operating inflection versus lagging price"},
    "05": {"name": "POLICY_DEFENSE_NUCLEAR_URANIUM_CRITICAL_MINERALS_ENERGY_SECURITY", "keywords": ("security", "cyber", "energy", "nuclear", "uranium", "grid", "infrastructure", "critical", "defense"), "focus": "funded policy/security demand and issuer materiality"},
    "06": {"name": "SPACE_DEFENSE_ISR_AEROSPACE_COMPONENTS", "keywords": ("space", "satellite", "rocket", "defense", "aerospace", "missile", "drone"), "focus": "funded backlog and contract quality"},
    "07": {"name": "UNDERFOLLOWED_PROFITABILITY_IMPROVING_SMALLCAP", "keywords": (), "focus": "small/mid-cap revenue, margin and FCF inflection"},
    "08": {"name": "SECONDARY_BLOCK_ABSORPTION_RECOVERY", "keywords": (), "focus": "post-offering dilution/float absorption"},
    "09": {"name": "INSIDER_BUY_BUYBACK_DEFENSIVE_TURNAROUND", "keywords": (), "focus": "open-market insider buying or actual buyback net of SBC"},
    "10": {"name": "DEBT_REFINANCING_BANKRUPTCY_RISK_REMOVAL", "keywords": (), "focus": "maturity/refinancing/covenant/interest burden inflection"},
    "11": {"name": "POST_EARNINGS_REVISION_LAG", "keywords": (), "focus": "earnings/revision surprise versus abnormal price lag"},
    "12": {"name": "CUSTOMER_CONCENTRATION_BREAK_SECOND_LARGE_CUSTOMER", "keywords": (), "focus": "customer concentration break and second-customer economics"},
    "13": {"name": "FINTECH_HEALTHCARE_NON_SEMI_SOFTWARE_ROTATION", "keywords": ("software", "health", "medical", "biotech", "fintech", "payments", "cloud", "saas"), "focus": "branch KPI improvement plus relative-strength rotation"},
    "14": {"name": "AI_BOTTLENECK_EXPANSION_EXCEPTION", "keywords": ("ai", "artificial intelligence", "data center", "datacenter", "semiconductor", "gpu", "server", "optical", "power", "cooling", "network"), "focus": "AI bottleneck directness and per-share economics"},
}

DISPOSITIONS = {
    "DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY", "WATCH_STAGE0", "WATCH_RESET",
    "DISCOVERY_INSUFFICIENT", "TIME_HORIZON_MISMATCH", "PRICE_STAGE_MISMATCH",
    "STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL", "DATA_INTEGRITY_BLOCK",
}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _sid(row: dict[str, Any]) -> str:
    return str(row.get("security_id") or row.get("ticker") or "").upper().strip()


def _text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ("issuer_name", "name", "sector", "industry", "security_type")).casefold()


def _technical(values: dict[str, Any], sid: str) -> dict[str, Any]:
    item = values.get(sid) if isinstance(values, dict) else None
    return item if isinstance(item, dict) else {}


def _keyword_hit(text: str, words: tuple[str, ...]) -> bool:
    return bool(words) and any(word in text for word in words)


def _research_value(signal: str, unknowns: list[str], decisive_path: bool) -> str:
    if signal in {"STRONG", "MODERATE"} and decisive_path and 1 <= len(unknowns) <= 3:
        return "HIGH"
    if signal in {"STRONG", "MODERATE", "WEAK"} and decisive_path:
        return "MEDIUM"
    return "LOW"


def _evaluate(scanner_id: str, row: dict[str, Any], tech: dict[str, Any]) -> dict[str, Any]:
    sid = _sid(row)
    spec = SCANNERS[scanner_id]
    text = _text(row)
    cap = _num(row.get("market_cap")) or 0.0
    retw = _num(tech.get("return_window")) or 0.0
    ret1 = _num(tech.get("return_1")) or 0.0
    vol = _num(tech.get("volatility_window")) or 0.0
    volume_ratio = _num(tech.get("volume_ratio")) or 0.0
    last = _num(tech.get("last_price")) or _num(row.get("price")) or 0.0
    sma = _num(tech.get("sma_window")) or last
    signal, disposition = "NONE", "DISCOVERY_INSUFFICIENT"
    unknowns: list[str] = []
    missing: list[str] = []
    path = ""
    rationale = ""
    decisive = False

    if scanner_id == "02":
        if _keyword_hit(text, SCANNERS["14"]["keywords"]):
            disposition, rationale = "WATCH_STAGE0", "obvious AI/semi exposure belongs primarily to scanner 14"
        else:
            setup = retw >= -0.12 and last >= sma * 0.97
            anomaly = volume_ratio >= 1.25 or abs(ret1) >= 0.03 or vol >= 0.20
            signal = "MODERATE" if setup and anomaly else "WEAK"
            unknowns = ["fundamental_delta", "near_term_catalyst"]
            missing = ["latest issuer/SEC fundamental delta", "1-8W catalyst economics"]
            path, decisive = "issuer IR + SEC cheap facts + recent material news", signal == "MODERATE"
            rationale = "broad non-AI/non-semi setup retained for information-rich verification"
    elif scanner_id == "03":
        if row.get("ipoyear") not in (None, "", "N/A"):
            signal = "MODERATE" if retw <= 0.10 else "WEAK"
            unknowns = ["offer_price_bridge", "lockup_or_resale_overhang"]
            missing = ["IPO offer price/age", "growth/margin delta", "unlock/resale status"]
            path, decisive = "IPO prospectus/424B + latest 10-Q/earnings", True
            rationale = "IPO identity exists; verify dislocation and operating bridge"
        else:
            unknowns, missing, path = ["ipo_identity"], ["IPO year/offer price"], "SEC registration/prospectus if a separate signal emerges"
            rationale = "no IPO identity evidence in cheap universe packet"
    elif scanner_id == "04":
        if -0.20 <= retw <= 0.20 and (ret1 > 0 or last >= sma):
            signal = "MODERATE" if volume_ratio >= 1.15 or ret1 >= 0.02 else "WEAK"
            unknowns = ["operating_inflection", "one_off_vs_structural"]
            missing = ["revenue/margin/FCF trend", "guidance/revision bridge"]
            path, decisive = "last two earnings + SEC cash-flow/margin evidence", signal == "MODERATE"
            rationale = "early turnaround price setup exists but operating evidence is unresolved"
        else:
            rationale = "cheap turnaround setup absent"
    elif scanner_id in {"05", "06", "13", "14"}:
        if _keyword_hit(text, spec["keywords"]):
            signal = "MODERATE" if retw >= -0.15 and last >= sma * 0.95 else "WEAK"
            unknowns = {
                "05": ["funded_policy_amount", "issuer_materiality"],
                "06": ["funded_backlog", "contract_quality"],
                "13": ["branch_kpi_delta", "sector_relative_strength"],
                "14": ["ai_demand_directness", "bottleneck_economics"],
            }[scanner_id]
            missing = [f"verify {item}" for item in unknowns]
            path, decisive = "issuer IR + SEC + customer/government/major-media evidence", True
            rationale = f"scanner-specific identity/industry signal for {spec['name']}"
        else:
            rationale = "scanner-specific identity/industry signal absent in cheap packet"
    elif scanner_id == "07":
        if 300_000_000 <= cap <= 10_000_000_000:
            anomaly = volume_ratio >= 1.20 or ret1 >= 0.02 or vol >= 0.18
            signal = "MODERATE" if retw >= -0.10 and last >= sma and anomaly else "WEAK"
            unknowns = ["profitability_inflection", "cash_conversion"]
            missing = ["revenue/margin/FCF trajectory"]
            path, decisive = "latest 10-Q/earnings and cash-flow statement", signal == "MODERATE"
            rationale = "small/mid-cap size fits scanner; profitability evidence must be verified"
        else:
            rationale = "outside scanner-07 preferred capitalization band"
    elif scanner_id == "08":
        if vol >= 0.18 and volume_ratio >= 1.20 and ret1 >= -0.03:
            signal, decisive = "WEAK", True
            unknowns = ["recent_offering", "dilution_float_bridge", "absorption"]
            missing = ["424B/S-3/8-K terms", "post-deal float/volume absorption"]
            path = "SEC offering filings + post-deal market data"
            rationale = "volatility/volume pattern makes absorption verification decision-relevant"
        else:
            rationale = "cheap offering-absorption signal absent"
    elif scanner_id == "09":
        if row.get("insider_purchase") or row.get("buyback"):
            signal, decisive = "MODERATE", True
            unknowns = ["open_market_code_p", "buyback_net_of_sbc"]
            missing = ["Form 4 transaction code", "actual repurchase versus SBC"]
            path, rationale = "SEC Form 4 + 10-Q equity footnote", "insider/buyback hint exists"
        else:
            rationale = "no insider/buyback hint; do not manufacture one"
    elif scanner_id == "10":
        if row.get("debt_maturity") or row.get("refinancing") or (cap and cap < 5_000_000_000 and vol >= 0.20):
            signal, decisive = "WEAK", True
            unknowns = ["maturity_schedule", "refinancing_terms", "interest_coverage"]
            missing = ["debt maturity table", "new financing terms"]
            path = "10-Q/10-K debt footnote + 8-K financing"
            rationale = "capital structure makes refinancing verification potentially decision-relevant"
        else:
            rationale = "cheap refinancing signal absent"
    elif scanner_id == "11":
        if retw <= 0.08 and last >= sma * 0.92 and volume_ratio >= 1.0:
            signal, decisive = ("MODERATE" if ret1 >= -0.02 else "WEAK"), True
            unknowns = ["recent_earnings_surprise", "estimate_revision", "abnormal_return"]
            missing = ["earnings surprise/guidance", "revision evidence"]
            path = "latest earnings release + consensus/revision source"
            rationale = "price lag/volume setup warrants underreaction verification"
        else:
            rationale = "cheap post-earnings-lag setup absent"
    elif scanner_id == "12":
        if row.get("customer_concentration") or row.get("second_customer"):
            signal, decisive = "MODERATE", True
            unknowns = ["top_customer_share", "second_customer_economics"]
            missing = ["customer concentration disclosure", "second-customer revenue timing"]
            path, rationale = "10-Q/10-K concentration + contract/IR evidence", "customer concentration hint exists"
        else:
            rationale = "customer-concentration evidence absent in cheap packet"

    research_value = _research_value(signal, unknowns, decisive)
    if signal in {"STRONG", "MODERATE"} and research_value == "HIGH":
        disposition = "DEEP_DIVE_NOW" if len(unknowns) <= 1 else "DEEP_DIVE_SECONDARY"
    elif signal in {"MODERATE", "WEAK"} and research_value in {"HIGH", "MEDIUM"}:
        disposition = "DEEP_DIVE_SECONDARY"
    if retw > 0.60:
        disposition = "PRICE_STAGE_MISMATCH"
        rationale += "; price already ran too far for chase"
    if not sid:
        disposition, research_value = "DATA_INTEGRITY_BLOCK", "LOW"
        unknowns.append("security_identity")
    return {
        "security_id": sid, "scanner_id": scanner_id, "scanner_name": spec["name"],
        "signal_strength": signal, "research_value": research_value, "disposition": disposition,
        "unknowns": sorted(set(unknowns)), "missing_evidence": missing,
        "verification_path": path or "none",
        "recheck_trigger": "new primary/official evidence or material price reset" if disposition not in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"} else "none",
        "rationale": rationale or "no scanner-specific signal detected", "grade_authority": False,
    }


def _scanner_receipt(scanner_id: str, rows: list[dict[str, Any]], technical: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    evaluations: list[dict[str, Any]] = []
    rounds: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index in range(0, len(rows), SCANNER_ROUND_SIZE):
        batch = rows[index:index + SCANNER_ROUND_SIZE]
        values = [_evaluate(scanner_id, row, _technical(technical, _sid(row))) for row in batch]
        values = [item for item in values if item["security_id"]]
        ids = [item["security_id"] for item in values]
        duplicate = sum(1 for sid in ids if sid in seen)
        new_ids = [sid for sid in ids if sid not in seen]
        seen.update(new_ids)
        signal = sum(1 for item in values if item["signal_strength"] in {"STRONG", "MODERATE"})
        partial = sum(1 for item in values if item["signal_strength"] == "WEAK")
        secondary = sum(1 for item in values if item["disposition"] == "DEEP_DIVE_SECONDARY")
        high_value = sum(1 for item in values if item["research_value"] == "HIGH")
        rounds.append({
            "scanner_id": scanner_id, "round": index // SCANNER_ROUND_SIZE + 1,
            "new_unique_tickers": len(new_ids), "signal_detected": signal, "partial_signal": partial,
            "secondary": secondary, "high_research_value": high_value,
            "deep_dive_now": sum(1 for item in values if item["disposition"] == "DEEP_DIVE_NOW"),
            "duplicate_saturation": round(duplicate / max(1, len(ids)), 6),
            "signal_detection_rate": round(signal / max(1, len(ids)), 6),
            "partial_signal_rate": round(partial / max(1, len(ids)), 6),
            "secondary_queue_rate": round(secondary / max(1, len(ids)), 6),
            "high_research_value_rate": round(high_value / max(1, len(ids)), 6),
            "independent_evidence_yield": 0, "source_exhaustion": False,
        })
        evaluations.extend(values)
    expected = len({_sid(row) for row in rows if _sid(row)})
    actual = len({item["security_id"] for item in evaluations})
    receipt = {
        "version": DISCOVERY_RECALL_LITE_VERSION, "scanner_id": scanner_id,
        "scanner_name": SCANNERS[scanner_id]["name"], "focus": SCANNERS[scanner_id]["focus"],
        "status": "SIGNAL_SCAN_COMPLETE" if actual == expected else "SIGNAL_SCAN_PARTIAL",
        "execution_depth": "LITE_SIGNAL_ROUTING_WITH_FULL_SECONDARY_VERIFICATION",
        "primary_verification_delegated_to": ["SEC_CHEAP_PRESCREEN", "RESEARCH_PROVIDER", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "V8_STEP15_20"],
        "universe_seen": len(rows), "eligible_count": len(rows), "evaluated_count": actual,
        "raw_signal_count": sum(1 for item in evaluations if item["signal_strength"] in {"STRONG", "MODERATE"}),
        "partial_signal_count": sum(1 for item in evaluations if item["signal_strength"] == "WEAK"),
        "unknown_retained_count": sum(1 for item in evaluations if item["unknowns"] and item["disposition"] not in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"}),
        "secondary_count": sum(1 for item in evaluations if item["disposition"] == "DEEP_DIVE_SECONDARY"),
        "deep_count": sum(1 for item in evaluations if item["disposition"] == "DEEP_DIVE_NOW"),
        "structural_fail_count": sum(1 for item in evaluations if item["disposition"] == "STRUCTURAL_HARD_FAIL"),
        "source_exhaustion": False, "output_contract_complete": actual == expected, "grade_authority": False,
    }
    return receipt, evaluations, rounds

SENTINEL_PROMPT_ID = "v8_discovery_recall.rejection_sentinel"
SENTINEL_SCHEMA_ID = "V8DiscoveryRejectionSentinelV15"
SENTINEL_BODY = """# V8 Discovery Recall rejection sentinel\nYou are a false-negative discovery auditor, never a grade authority. UNKNOWN is neither PASS nor FAIL. Missing 1-8 week evidence is not a structural hard fail. Upgrade information-rich nonfatal cases to SECONDARY; downgrade weak cases to WATCH; identify data blocks. Never output Research Grade, PRE-A status, target, probability, execution action, position size or broker instruction. grade_authority=false."""


def _sentinel_schema() -> dict[str, Any]:
    item = {"type": "object", "properties": {
        "security_id": {"type": "string", "minLength": 1}, "scanner_id": {"type": "string", "enum": sorted(SCANNERS)},
        "finding": {"type": "string", "enum": ["OK", "UPGRADE_SECONDARY", "DOWNGRADE_WATCH", "MISCLASSIFIED_HARD_FAIL", "DATA_BLOCK"]},
        "rationale": {"type": "string", "minLength": 1},
    }, "required": ["security_id", "scanner_id", "finding", "rationale"], "additionalProperties": False}
    return {"type": "object", "properties": {
        "status": {"type": "string", "enum": ["COMPLETE", "INCOMPLETE"]}, "audits": {"type": "array", "items": item},
        "systematic_misclassification": {"type": "boolean"}, "grade_authority": {"const": False},
    }, "required": ["status", "audits", "systematic_misclassification", "grade_authority"], "additionalProperties": False}


def _install_sentinel(prompt_runtime: Any) -> None:
    prompt_runtime.registry.setdefault("schemas", {})[SENTINEL_SCHEMA_ID] = _sentinel_schema()
    prompt_runtime.prompts[SENTINEL_PROMPT_ID] = {
        "prompt_id": SENTINEL_PROMPT_ID, "version": "1.5", "prompt_kind": "LEAF", "output_schema": SENTINEL_SCHEMA_ID,
        "required_inputs": ["effective_rule_pack"], "optional_inputs": [], "compose_with": [], "requires_results": [], "requires_capabilities": [],
        "allowed_run_modes": ["HUNT_ONLY", "HUNT_AND_EXECUTION_REVIEW"], "_body": SENTINEL_BODY,
    }
    if not any(isinstance(item, dict) and item.get("prompt_id") == SENTINEL_PROMPT_ID for item in prompt_runtime.manifest.get("prompts", [])):
        prompt_runtime.manifest.setdefault("prompts", []).append({"prompt_id": SENTINEL_PROMPT_ID, "content_hash": canonical_hash(SENTINEL_BODY), "file": f"RUNTIME:{SENTINEL_PROMPT_ID}"})


def _sentinel_sample(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in evaluations:
        buckets.setdefault(str(item.get("disposition")), []).append(item)
    sample: list[dict[str, Any]] = []
    order = ["DISCOVERY_INSUFFICIENT", "PRICE_STAGE_MISMATCH", "TIME_HORIZON_MISMATCH", "WATCH_STAGE0", "WATCH_RESET", "DEEP_DIVE_SECONDARY"]
    per_bucket = max(1, SENTINEL_SAMPLE_SIZE // max(1, len(order)))
    for key in order:
        values = sorted(buckets.get(key, []), key=lambda item: canonical_hash({"sid": item.get("security_id"), "scanner": item.get("scanner_id")}))
        sample.extend(values[:per_bucket])
    used = {(item["security_id"], item["scanner_id"]) for item in sample}
    remaining = [item for item in evaluations if (item["security_id"], item["scanner_id"]) not in used]
    remaining.sort(key=lambda item: canonical_hash({"sid": item.get("security_id"), "scanner": item.get("scanner_id"), "fill": True}))
    sample.extend(remaining[:max(0, SENTINEL_SAMPLE_SIZE - len(sample))])
    return sample[:SENTINEL_SAMPLE_SIZE]


def _default_sentinel(sample: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "COMPLETE", "audits": [{"security_id": item["security_id"], "scanner_id": item["scanner_id"], "finding": "OK", "rationale": "deterministic fixture sentinel"} for item in sample], "systematic_misclassification": False, "grade_authority": False}


def _apply_sentinel(evaluations: list[dict[str, Any]], sentinel: dict[str, Any]) -> int:
    index = {(item["security_id"], item["scanner_id"]): item for item in evaluations}
    changed = 0
    for audit in sentinel.get("audits") or []:
        if not isinstance(audit, dict):
            continue
        item = index.get((str(audit.get("security_id")), str(audit.get("scanner_id"))))
        if item is None:
            continue
        finding = str(audit.get("finding") or "")
        if finding == "UPGRADE_SECONDARY" and item["disposition"] == "DISCOVERY_INSUFFICIENT" and item["research_value"] in {"HIGH", "MEDIUM"}:
            item["disposition"], changed = "DEEP_DIVE_SECONDARY", changed + 1
        elif finding == "DOWNGRADE_WATCH" and item["disposition"] == "DEEP_DIVE_SECONDARY":
            item["disposition"], changed = "WATCH_STAGE0", changed + 1
        elif finding == "DATA_BLOCK" and item["disposition"] not in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"}:
            item["disposition"], changed = "DATA_INTEGRITY_BLOCK", changed + 1
        elif finding == "MISCLASSIFIED_HARD_FAIL" and item["disposition"] in {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"}:
            item["disposition"], item["research_value"], changed = "DEEP_DIVE_SECONDARY", "HIGH", changed + 1
    return changed


def _aggregate(evaluations: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in evaluations:
        grouped.setdefault(item["security_id"], []).append(item)
    routed: dict[str, dict[str, Any]] = {}
    secondary: list[dict[str, Any]] = []
    near_miss: list[dict[str, Any]] = []
    for sid, items in grouped.items():
        if any(item["disposition"] == "STRUCTURAL_HARD_FAIL" for item in items):
            continue
        now = [item for item in items if item["disposition"] == "DEEP_DIVE_NOW"]
        sec = [item for item in items if item["disposition"] == "DEEP_DIVE_SECONDARY"]
        if now:
            best = sorted(now, key=lambda x: (x["research_value"] == "HIGH", x["signal_strength"] == "STRONG"), reverse=True)[0]
            routed[sid] = {"action": "DEEP_DIVE_NOW", "source": best}
        elif sec:
            best = sorted(sec, key=lambda x: (x["research_value"] == "HIGH", x["signal_strength"] in {"STRONG", "MODERATE"}), reverse=True)[0]
            routed[sid] = {"action": "DEEP_DIVE_SECONDARY", "source": best}
            secondary.append(best)
        else:
            high = [item for item in items if item["research_value"] == "HIGH"]
            if high:
                near_miss.append(high[0])
    return routed, secondary, near_miss


def _funnel(store: Any, run_id: str) -> dict[str, tuple[int, dict[str, Any]]]:
    result: dict[str, tuple[int, dict[str, Any]]] = {}
    for row in store.list_funnel(run_id):
        try:
            details = json.loads(row.get("details_json") or "{}")
        except (TypeError, ValueError):
            details = {}
        result[str(row.get("funnel_stage"))] = (int(row.get("count") or 0), details if isinstance(details, dict) else {})
    return result


def _set_outcome(store: Any, run_id: str, outcome: str) -> None:
    with store.transaction() as db:
        db.execute("UPDATE runs SET status='FAILED', outcome=? WHERE run_id=?", (outcome, run_id))


def _secondary_open(store: Any, run_id: str, item: dict[str, Any]) -> bool:
    sid = item["security_id"]
    for stage in ("CANDIDATE_ENGINEERING_FAILURE", "RESEARCH_PROVIDER_FAILURE", "SEC_PROVIDER_FAILURE", "SEC_STALE_DATA"):
        if store.get_stage_result(run_id, stage, sid):
            return True
    if store.get_stage_result(run_id, "V8_CERTIFICATION", sid) or store.get_stage_result(run_id, "DEEP_RESEARCH", sid):
        return False
    for stage in ("STAGE_GATE", "CAPITAL_PRESCREEN_GATE", "CATALYST_GATE"):
        row = store.get_stage_result(run_id, stage, sid)
        if not row:
            continue
        try:
            value = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError):
            value = {}
        if str(value.get("evaluation_status") or "").startswith("NOT_EVALUATED"):
            return True
        if str(value.get("decision") or "") == "REJECT":
            return False
    return True


class DiscoveryRecallNasdaqScreenerProvider(adapters_module.NasdaqScreenerMarketDataProvider):
    """Preserve public screener fields needed for scanner routing."""

    provider_name = "nasdaq-screener-discovery-recall-v15"

    @staticmethod
    def _signed_number(value: Any) -> float | None:
        if value in (None, "", "N/A", "NA", "null"):
            return None
        try:
            number = float(str(value).replace("$", "").replace(",", "").replace("%", "").strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def fetch_universe(self, query: dict[str, Any]):
        query = dict(query or {})
        markets = [str(item).upper().strip() for item in (query.get("markets") or [query.get("market") or "NASDAQ"]) if str(item).strip()]
        if not markets or any(item not in self.EXCHANGES for item in markets):
            raise ProviderError("Nasdaq screener markets must be NASDAQ, NYSE, or AMEX")
        limit = int(query.get("limit", 5000))
        if not 1 <= limit <= 5000:
            raise ProviderError("Nasdaq screener limit must be 1..5000")
        rows: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        times: list[str] = []
        for market in markets:
            exchange = self.EXCHANGES[market]
            url = f"{self.BASE_URL}?tableonly=true&limit={limit}&exchange={urllib.parse.quote(exchange)}"
            if _validate_public_https_url(url, label="Nasdaq screener request") != self._base_host:
                raise ProviderError("Nasdaq screener request crossed host boundary")
            request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "StockAgent/1.5 discovery-recall read-only"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    raw = response.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        raise ProviderError("Nasdaq screener response exceeds configured size limit")
                    if _validate_public_https_url(str(_response_final_url(response, url)), label="Nasdaq screener redirect") != self._base_host:
                        raise ProviderError("Nasdaq screener redirect crossed host boundary")
            except ProviderError:
                raise
            except (urllib.error.URLError, TimeoutError) as exc:
                raise ProviderError(f"Nasdaq screener request failed for {market}: {exc}") from exc
            try:
                document = json.loads(raw.decode("utf-8"))
                data = document.get("data") if isinstance(document, dict) else None
                table = data.get("table") if isinstance(data, dict) else None
                source = list(table.get("rows") or []) if isinstance(table, dict) else []
                asof = self._asof_date(data.get("asof") if isinstance(data, dict) else None)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ProviderError(f"Nasdaq screener payload malformed for {market}") from exc
            if not source:
                raise ProviderError(f"Nasdaq screener returned no rows for {market}")
            if asof:
                times.append(asof)
            sources.append({"exchange": market, "source_url": url, "asof": asof, "row_count": len(source)})
            for item in source:
                if not isinstance(item, dict):
                    continue
                ticker = str(item.get("symbol") or "").upper().strip()
                if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", ticker):
                    continue
                rows.append({
                    "security_id": ticker, "ticker": ticker, "issuer_name": str(item.get("name") or ticker),
                    "venue": market, "market": market, "security_type": "COMMON_STOCK", "currency": "USD",
                    "price": self._number(item.get("lastsale")), "market_cap": self._number(item.get("marketCap")),
                    "market_cap_source": url, "market_cap_observed_at": asof, "source_observed_at": asof, "source_url": url,
                    "sector": str(item.get("sector") or "").strip() or None, "industry": str(item.get("industry") or "").strip() or None,
                    "country": str(item.get("country") or "").strip() or None, "ipoyear": item.get("ipoyear"),
                    "netchange": self._signed_number(item.get("netchange")), "pctchange": self._signed_number(item.get("pctchange")),
                    "screener_volume": self._number(item.get("volume")),
                })
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            deduped.setdefault(row["security_id"], row)
        observed = max(times) if times else None
        payload = {"securities": list(deduped.values()), "markets": markets, "source": sources, "source_observed_at": observed,
                   "normalization_status": "BROAD_UNIVERSE_WITH_DISCOVERY_ROUTING_FIELDS", "provider": self.provider_name,
                   "discovery_recall_lite_version": DISCOVERY_RECALL_LITE_VERSION}
        return _artifact(self.provider_name, "NASDAQ_BROAD_UNIVERSE", payload, source_observed_at=observed, infer_source=False)

_PROVIDER_INSTALLED = False
_RUNTIME_INSTALLED = False


def install_discovery_recall_lite_provider() -> type:
    global _PROVIDER_INSTALLED
    current = adapters_module.CompositeLiveMarketContextProvider
    if _PROVIDER_INSTALLED or getattr(current, "discovery_recall_lite_version", None) == DISCOVERY_RECALL_LITE_VERSION:
        return current
    alpha_bootstrap.MAX_ALPHA_PROBE_LIMIT = max(int(getattr(alpha_bootstrap, "MAX_ALPHA_PROBE_LIMIT", 1000)), DEFAULT_ADV_PROBE_TARGET)
    adapters_module.NasdaqScreenerMarketDataProvider = DiscoveryRecallNasdaqScreenerProvider
    base_compact = runtime_module._compact_model_universe_rows
    if not getattr(base_compact, "_discovery_recall_v15", False):
        def compact(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            values = base_compact(rows)
            originals = {_sid(row): row for row in rows if isinstance(row, dict) and _sid(row)}
            for item in values:
                original = originals.get(_sid(item), {})
                for key in ("ipoyear", "country", "pctchange", "netchange", "screener_volume"):
                    if key in original:
                        item[key] = original[key]
            return values
        compact._discovery_recall_v15 = True  # type: ignore[attr-defined]
        runtime_module._compact_model_universe_rows = compact

    class DiscoveryRecallLiveMarketContextProvider(current):  # type: ignore[misc,valid-type]
        discovery_recall_lite_version = DISCOVERY_RECALL_LITE_VERSION
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.last_discovery_diagnostic: dict[str, Any] | None = None
        def fetch_universe(self, query: dict[str, Any]):
            values = dict(query or {})
            broad = not (values.get("symbols") or values.get("tickers")) and values.get("broad", True)
            if broad:
                values.setdefault("alpha_probe_limit", DEFAULT_ADV_PROBE_TARGET)
                values.setdefault("screener_limit", 5000)
                values.setdefault("technical_count", 100)
            try:
                artifact = super().fetch_universe(values)
            except Exception as exc:
                self.last_discovery_diagnostic = {"status": "PROVIDER_FAILURE", "error_type": type(exc).__name__, "error": str(exc)[:500],
                    "alpha_probe_limit": values.get("alpha_probe_limit"), "screener_limit": values.get("screener_limit")}
                raise
            payload = artifact.payload if isinstance(getattr(artifact, "payload", None), dict) else {}
            rows = payload.get("candidates") or payload.get("securities") or []
            self.last_discovery_diagnostic = {"status": "PASS" if isinstance(rows, list) and rows else "EMPTY_BROAD_UNIVERSE",
                "raw_rows": len(rows) if isinstance(rows, list) else 0, "probe_target": payload.get("probe_target", payload.get("probe_limit")),
                "probe_count": payload.get("probe_count"), "probe_success_count": payload.get("probe_success_count"),
                "probe_not_evaluated_count": payload.get("probe_not_evaluated_count"), "probe_errors": list(payload.get("probe_errors") or [])[:30],
                "source": list(payload.get("source") or [])[:10]}
            return artifact
    adapters_module.CompositeLiveMarketContextProvider = DiscoveryRecallLiveMarketContextProvider
    _PROVIDER_INSTALLED = True
    return DiscoveryRecallLiveMarketContextProvider


def install_discovery_recall_lite_runtime() -> type:
    global _RUNTIME_INSTALLED
    current = runtime_module.ProductionStockAgent
    if _RUNTIME_INSTALLED or getattr(current, "discovery_recall_lite_version", None) == DISCOVERY_RECALL_LITE_VERSION:
        return current

    class DiscoveryRecallLiteProductionStockAgent(current):  # type: ignore[misc,valid-type]
        discovery_recall_lite_version = DISCOVERY_RECALL_LITE_VERSION
        discovery_recall_forensic_audit_sha256 = FORENSIC_AUDIT_SHA256

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            _install_sentinel(self.prompts)
            self._discovery_recall_state: dict[str, dict[str, Any]] = {}

        def _profile_for_stage(self, stage: str) -> str:
            if stage == "DISCOVERY_REJECTION_SENTINEL" and "CRITICAL_AUDIT" in self.router.profiles:
                return "CRITICAL_AUDIT"
            return super()._profile_for_stage(stage)

        def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
            if stage != "STOCK_DISCOVERY":
                return super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            raw_input = payload.get("raw_input") if isinstance(payload, dict) else {}
            universe = list((raw_input or {}).get("universe") or []) if isinstance(raw_input, dict) else []
            technical = dict((raw_input or {}).get("technical_features") or {}) if isinstance(raw_input, dict) else {}
            scan_rows = [row for row in universe if isinstance(row, dict) and 300_000_000 <= (_num(row.get("market_cap")) or 0.0) <= PREFERRED_MAX_MARKET_CAP]
            evaluations: list[dict[str, Any]] = []
            receipts: dict[str, dict[str, Any]] = {}
            rounds: list[dict[str, Any]] = []
            zero_dep = self.store.dependency_hash([], run.rule_set.rule_set_hash, run.context_manifest_hash)
            for scanner_id in sorted(SCANNERS):
                receipt, items, scanner_rounds = _scanner_receipt(scanner_id, scan_rows, technical)
                receipts[scanner_id] = receipt
                evaluations.extend(items)
                rounds.extend(scanner_rounds)
                self.store.record_funnel(run.run_id, f"DISCOVERY_SCANNER_{scanner_id}_EXECUTION", receipt["evaluated_count"], receipt)
                self.store.record_stage_result(run.run_id, None, f"DISCOVERY_SCANNER_{scanner_id}_RECEIPT", None, receipt, [], zero_dep, 0)
            sample = _sentinel_sample(evaluations)
            sentinel = _default_sentinel(sample)
            sentinel_complete = False
            if sample:
                try:
                    sentinel = super()._work_stage(run, "DISCOVERY_REJECTION_SENTINEL", SENTINEL_PROMPT_ID,
                        {"raw_input": {"sample": sample, "rules": {"unknown_neutral": True, "hard_fail_taxonomy": sorted(DISPOSITIONS)}}, "default_payload": sentinel}, None, [], {})
                    valid = {(item["security_id"], item["scanner_id"]) for item in sample}
                    sentinel["audits"] = [audit for audit in (sentinel.get("audits") or []) if isinstance(audit, dict) and (str(audit.get("security_id")), str(audit.get("scanner_id"))) in valid]
                    returned = {(str(audit.get("security_id")), str(audit.get("scanner_id"))) for audit in sentinel["audits"]}
                    sentinel_complete = str(sentinel.get("status")) == "COMPLETE" and returned == valid
                except Exception as exc:
                    sentinel = {"status": "INCOMPLETE", "audits": [], "systematic_misclassification": True, "grade_authority": False, "error": f"{type(exc).__name__}:{str(exc)[:180]}"}
            changes = _apply_sentinel(evaluations, sentinel) if sentinel_complete else 0
            routed, secondary, near_miss = _aggregate(evaluations)
            self.store.record_funnel(run.run_id, "DISCOVERY_REJECTION_SENTINEL", len(sample), {"status": sentinel.get("status"), "complete": sentinel_complete,
                "systematic_misclassification": bool(sentinel.get("systematic_misclassification")), "routing_changes": changes, "sample_size": len(sample)})
            self.store.record_funnel(run.run_id, "DISCOVERY_SECONDARY_QUEUE", len(secondary), {"status": "OPEN", "candidates": secondary[:300], "grade_authority": False})
            self.store.record_funnel(run.run_id, "DISCOVERY_NEAR_MISS_QUEUE", len(near_miss), {"candidates": near_miss[:300], "grade_authority": False})
            for item in secondary:
                self.store.record_stage_result(run.run_id, None, "DISCOVERY_SECONDARY_QUEUE", item["security_id"], {**item, "queue_status": "OPEN", "grade_authority": False}, [], zero_dep, 0)
            for item in near_miss:
                self.store.record_stage_result(run.run_id, None, "DISCOVERY_NEAR_MISS", item["security_id"], {**item, "queue_status": "WATCH", "grade_authority": False}, [], zero_dep, 0)
            for item in rounds:
                self.store.record_funnel(run.run_id, f"DISCOVERY_ROUND_{item['scanner_id']}_{item['round']:03d}", int(item["new_unique_tickers"]), item)

            base = super()._work_stage(run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs)
            candidates = [dict(item) for item in (base.get("candidates") or []) if isinstance(item, dict)] if isinstance(base, dict) else []
            by_sid = {str(item.get("security_id") or ""): item for item in candidates if item.get("security_id")}
            for sid, route in routed.items():
                action, source = route["action"], route["source"]
                item = by_sid.get(sid)
                if item is None:
                    item = {"security_id": sid, "recommended_discovery_action": action, "proposed_stage": "STAGE_0",
                            "rationale": f"{DISCOVERY_RECALL_LITE_VERSION}:{source['scanner_id']}:{source['rationale']}", "evidence_ids": []}
                    candidates.append(item)
                    by_sid[sid] = item
                elif action == "DEEP_DIVE_NOW" or item.get("recommended_discovery_action") not in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}:
                    item["recommended_discovery_action"] = action
            base = dict(base) if isinstance(base, dict) else {}
            base["candidates"] = candidates
            evaluated = sorted({_sid(row) for row in scan_rows if _sid(row)})
            complete = len(receipts) == len(SCANNERS) and all(receipt.get("status") == "SIGNAL_SCAN_COMPLETE" and receipt.get("output_contract_complete") is True for receipt in receipts.values())
            self.store.record_funnel(run.run_id, "DISCOVERY_SIGNAL_COVERAGE", len(evaluated), {
                "version": DISCOVERY_RECALL_LITE_VERSION, "scanner_receipts_complete": complete, "mandatory_scanners": sorted(SCANNERS),
                "raw_model_universe": len(universe), "strategy_eligible_unique": len(evaluated), "minimum_signal_coverage": MIN_SIGNAL_COVERAGE,
                "preferred_signal_coverage": PREFERRED_SIGNAL_COVERAGE, "context_only_count": max(0, len(universe) - len(scan_rows)),
                "raw_breadth_is_signal_coverage": False, "grade_authority": False})
            self._discovery_recall_state[run.run_id] = {"receipts": receipts, "rounds": rounds, "evaluated": evaluated,
                "secondary": secondary, "near_miss": near_miss, "sentinel_complete": sentinel_complete,
                "sentinel_misclassification": bool(sentinel.get("systematic_misclassification")), "scanner_complete": complete}
            return base

        def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
            patched = copy.deepcopy(data)
            query = dict(patched.get("universe_query") or {})
            bounded = bool(query.get("symbols") or query.get("tickers"))
            if not bounded:
                query.setdefault("broad", True)
                query.setdefault("alpha_probe_limit", DEFAULT_ADV_PROBE_TARGET)
                query.setdefault("screener_limit", 5000)
                query.setdefault("technical_count", 100)
                patched["universe_query"] = query
            outcome = super()._run_strict(mode, patched)
            run_id = str(getattr(outcome, "run_id", "") or "")
            if bounded or not run_id or run_id == "unstarted":
                return outcome
            funnel = _funnel(self.store, run_id)
            raw_count = funnel.get("RAW_UNIVERSE", (0, {}))[0]
            adv_count = funnel.get("ADV_FILTER", (0, {}))[0]
            adv_probed = funnel.get("ADV_PROBED", (0, {}))[0]
            adv_unknown = funnel.get("ADV_NOT_EVALUATED", (0, {}))[0]
            state = self._discovery_recall_state.get(run_id)
            if not state:
                provider = getattr(self.config, "market_data_provider", None)
                diagnostic = getattr(provider, "last_discovery_diagnostic", None)
                if raw_count <= 0:
                    code = "NOT_EVALUABLE_DISCOVERY_PIPELINE"
                    reason = f"broad universe not materialized; upstream={getattr(outcome, 'blocked_reason', None) or outcome.outcome}; provider={json.dumps(diagnostic, sort_keys=True)[:1200]}"
                elif adv_count <= 0:
                    code = "NOT_EVALUABLE_DISCOVERY_DATA_INTEGRITY"
                    reason = f"raw universe={raw_count} but verified ADV=0; UNKNOWN liquidity is not rejection"
                else:
                    code = "NOT_EVALUABLE_SIGNAL_SCAN_INCOMPLETE"
                    reason = f"universe/filter progressed but 02-14 scanner receipts absent; ADV pass={adv_count}"
                self.store.record_funnel(run_id, "DISCOVERY_RECALL_TERMINAL", 0, {"status": code, "reason": reason, "raw_unique": raw_count,
                    "adv_verified": adv_count, "adv_probed": adv_probed, "adv_not_evaluated": adv_unknown, "provider_diagnostic": diagnostic})
                _set_outcome(self.store, run_id, code)
                return replace(outcome, outcome=code, blocked_reason=reason)

            evaluated = len(state["evaluated"])
            open_high = [item for item in state["secondary"] if item.get("research_value") == "HIGH" and _secondary_open(self.store, run_id, item)]
            round_numbers = sorted({int(item["round"]) for item in state["rounds"]})[-2:]
            tail = [item for item in state["rounds"] if int(item["round"]) in round_numbers]
            low_tail = bool(tail) and all(int(item.get("signal_detected") or 0) == 0 and int(item.get("secondary") or 0) == 0 and int(item.get("independent_evidence_yield") or 0) == 0 for item in tail)
            explicit_ceiling = adv_probed >= DEFAULT_ADV_PROBE_TARGET and adv_unknown > 0
            stop_allowed = bool(state["scanner_complete"]) and bool(state["sentinel_complete"]) and not bool(state["sentinel_misclassification"]) and not open_high and evaluated >= MIN_SIGNAL_COVERAGE and (adv_unknown == 0 or (low_tail and explicit_ceiling))
            stop_reason = "FULL_FUNNEL_EXHAUSTION" if adv_unknown == 0 else ("EXPLICIT_1000_NAME_ADV_CEILING_PLUS_LOW_MARGINAL_SIGNAL" if stop_allowed else "SEARCH_DEBT_REMAINS")
            self.store.record_funnel(run_id, "DISCOVERY_SEARCH_STOP_AUDIT", evaluated, {
                "search_stop_allowed": stop_allowed, "reason": stop_reason, "scanner_execution_complete": bool(state["scanner_complete"]),
                "sentinel_complete": bool(state["sentinel_complete"]), "open_high_research_value_secondary": len(open_high),
                "last_two_rounds_low_signal_secondary_evidence_yield": low_tail, "raw_unique": raw_count,
                "strategy_eligible_signal_coverage": evaluated, "adv_verified": adv_count, "adv_probed": adv_probed,
                "adv_not_evaluated": adv_unknown, "explicit_operational_ceiling_documented": explicit_ceiling,
                "deep_dive_yield_zero_alone_proves_exhaustion": False, "grade_authority": False})
            if evaluated < MIN_SIGNAL_COVERAGE:
                code, reason = "NOT_EVALUABLE_DISCOVERY_SIGNAL_COVERAGE", f"02-14 scanner-evaluated strategy universe {evaluated} < {MIN_SIGNAL_COVERAGE}; raw={raw_count}, ADV={adv_count}"
            elif not state["scanner_complete"]:
                code, reason = "NOT_EVALUABLE_SIGNAL_SCAN_INCOMPLETE", "LANE_TOUCHED != SCANNER_EXECUTED: mandatory 02-14 execution receipt incomplete"
            elif not state["sentinel_complete"] or state["sentinel_misclassification"]:
                code, reason = "NOT_EVALUABLE_DISCOVERY_SENTINEL", "rejection sentinel incomplete or systematic misclassification detected"
            elif open_high and str(outcome.outcome) in {"NO_QUALIFIED_CANDIDATE", "NOT_EVALUABLE_DISCOVERY_COVERAGE"}:
                code, reason = "NOT_EVALUABLE_SEARCH_DEBT_OPEN", f"{len(open_high)} HIGH research-value Secondary candidates remain unresolved"
            else:
                return outcome
            _set_outcome(self.store, run_id, code)
            return replace(outcome, outcome=code, blocked_reason=reason)

    runtime_module.ProductionStockAgent = DiscoveryRecallLiteProductionStockAgent
    _RUNTIME_INSTALLED = True
    return DiscoveryRecallLiteProductionStockAgent
