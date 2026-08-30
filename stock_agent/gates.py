from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
import re
from typing import Any

from .models import EffectiveRuleSet, ExecutionAction, FailurePath, GateDecision, RunMode, canonical_hash, utc_now


class ContractViolation(ValueError):
    pass


def _parse_timestamp(timestamp: str) -> datetime:
    try:
        value = str(timestamp).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ContractViolation(f"invalid source timestamp: {timestamp}") from exc
    return parsed.astimezone(timezone.utc)


def age_seconds(timestamp: str, now: datetime | None = None, max_future_skew_seconds: float = 300.0) -> float:
    parsed = _parse_timestamp(timestamp)
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    delta = (reference - parsed).total_seconds()
    if delta < -float(max_future_skew_seconds):
        raise ContractViolation("source timestamp is in the future beyond allowed clock skew")
    return max(0.0, delta)


def require_fresh(timestamp: str, max_age_seconds: float, label: str, max_future_skew_seconds: float = 300.0) -> None:
    if age_seconds(timestamp, max_future_skew_seconds=max_future_skew_seconds) > float(max_age_seconds):
        raise ContractViolation(f"stale {label} input exceeds max-age")


def require_artifact_fresh(artifact: Any, max_age_seconds: float, label: str, max_future_skew_seconds: float = 300.0, *, now: datetime | None = None) -> None:
    source_time = getattr(artifact, "source_observed_at", None)
    if not source_time:
        raise ContractViolation(f"{label} artifact has no source_observed_at")
    retrieved = getattr(artifact, "retrieved_at", None)
    if retrieved:
        source_dt = _parse_timestamp(str(source_time))
        retrieved_dt = _parse_timestamp(str(retrieved))
        if source_dt > retrieved_dt + timedelta(seconds=float(max_future_skew_seconds)):
            raise ContractViolation(f"{label} source_observed_at is after retrieved_at beyond allowed clock skew")
    if age_seconds(str(source_time), now=now, max_future_skew_seconds=max_future_skew_seconds) > float(max_age_seconds):
        raise ContractViolation(f"stale {label} input exceeds max-age")


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    cursor = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    holiday = date(year, month, day)
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


def _us_market_holidays(year: int) -> set[date]:
    """Return deterministic weekday closures for common U.S. sessions."""
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 6, 19),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    # Anonymous Gregorian Easter calculation, then Good Friday.
    a = year % 19; b = year // 100; c = year % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451; month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    holidays.add(date(year, month, day) - timedelta(days=2))
    return holidays


def _latest_completed_us_session_date(now: datetime | None = None) -> date:
    """Return the latest completed U.S. equity session date in UTC semantics."""
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidate = reference.date()
    if reference.weekday() >= 5 or reference.hour < 22:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in _us_market_holidays(candidate.year):
        candidate -= timedelta(days=1)
    return candidate


def validate_economic_assessment(receipt: dict[str, Any], target_security_id: str, candidate_evidence_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Validate an evidence-linked economic receipt before RiskEngine use."""
    if not isinstance(receipt, dict) or receipt.get("receipt_type") != "EconomicAssessmentReceiptV2":
        raise ContractViolation("execution requires EconomicAssessmentReceiptV2")
    if receipt.get("security_id") != target_security_id:
        raise ContractViolation("economic receipt security mismatch")
    required = ("current_price", "bull_value", "base_value", "bear_value", "bull_probability", "base_probability", "bear_probability", "probability_weighted_ev", "structural_asymmetry", "opportunity_cost_score", "evidence_ids", "source_result_ids", "calculation_hash")
    if any(key not in receipt for key in required):
        raise ContractViolation("economic receipt is incomplete")
    if not all(isinstance(receipt[key], (int, float)) for key in required[0:10]):
        raise ContractViolation("economic receipt numeric fields are invalid")
    probabilities = [float(receipt[key]) for key in ("bull_probability", "base_probability", "bear_probability")]
    if any(value < 0 or value > 1 for value in probabilities) or abs(sum(probabilities) - 1.0) > 1e-6:
        raise ContractViolation("economic probabilities must be non-negative and sum to one")
    if float(receipt["current_price"]) <= 0 or float(receipt["bear_value"]) <= 0:
        raise ContractViolation("economic values must be positive")
    if not (float(receipt["bear_value"]) <= float(receipt["base_value"]) <= float(receipt["bull_value"])):
        raise ContractViolation("economic scenario ordering must be bear <= base <= bull")
    expected_ev = sum(float(receipt[p]) * float(receipt[v]) for p, v in (("bull_probability", "bull_value"), ("base_probability", "base_value"), ("bear_probability", "bear_value"))) - float(receipt["current_price"])
    if abs(expected_ev - float(receipt["probability_weighted_ev"])) > 1e-6:
        raise ContractViolation("probability_weighted_ev does not match scenario arithmetic")
    evidence_ids = sorted(set(str(item) for item in (receipt.get("evidence_ids") or [])))
    source_result_ids = sorted(set(str(item) for item in (receipt.get("source_result_ids") or [])))
    candidate_ids = set(str(item) for item in candidate_evidence_ids)
    if not evidence_ids or not source_result_ids or not set(evidence_ids).issubset(candidate_ids):
        raise ContractViolation("economic receipt must reference candidate evidence and source results")
    canonical = {key: receipt[key] for key in ("receipt_type", "security_id", *required) if key != "calculation_hash"}
    if canonical_hash(canonical) != receipt.get("calculation_hash"):
        raise ContractViolation("economic receipt calculation hash mismatch")
    return receipt


def make_economic_assessment_receipt(*, security_id: str, current_price: float, bull_value: float, base_value: float, bear_value: float, bull_probability: float, base_probability: float, bear_probability: float, opportunity_cost_score: float, evidence_ids: list[str] | tuple[str, ...], source_result_ids: list[str] | tuple[str, ...]) -> dict[str, Any]:
    """Create the canonical receipt from validated scenario inputs.

    This helper performs arithmetic only; callers still need to prove the
    evidence/result IDs exist in the repository before final allocation.
    """
    denominator = max(float(current_price) - float(bear_value), 1e-9)
    structural_asymmetry = (float(bull_value) - float(current_price)) / denominator
    probability_weighted_ev = float(bull_probability) * float(bull_value) + float(base_probability) * float(base_value) + float(bear_probability) * float(bear_value) - float(current_price)
    receipt = {"receipt_type": "EconomicAssessmentReceiptV2", "security_id": security_id, "current_price": float(current_price), "bull_value": float(bull_value), "base_value": float(base_value), "bear_value": float(bear_value), "bull_probability": float(bull_probability), "base_probability": float(base_probability), "bear_probability": float(bear_probability), "probability_weighted_ev": probability_weighted_ev, "structural_asymmetry": structural_asymmetry, "opportunity_cost_score": float(opportunity_cost_score), "evidence_ids": sorted(set(str(item) for item in evidence_ids)), "source_result_ids": sorted(set(str(item) for item in source_result_ids))}
    receipt["calculation_hash"] = canonical_hash({key: value for key, value in receipt.items() if key != "calculation_hash"})
    return receipt


def validate_sec_artifacts(artifacts: list[Any]) -> None:
    """Deterministic completeness gate; LLM cannot declare SEC COMPLETE."""
    by_type = {getattr(artifact, "artifact_type", ""): artifact for artifact in artifacts}
    required = {"SEC_SUBMISSIONS", "SEC_FACTS"}
    if not required.issubset(by_type) or not any(key in by_type for key in ("SEC_FILINGS", "SEC_FILINGS_INDEX", "SEC_FILING_DOCUMENT")):
        raise ContractViolation("full SEC forensic requires submissions, facts, and filings")
    submissions = by_type["SEC_SUBMISSIONS"].payload
    facts = by_type["SEC_FACTS"].payload
    filing_artifact = by_type.get("SEC_FILING_DOCUMENT") or by_type.get("SEC_FILINGS") or by_type.get("SEC_FILINGS_INDEX")
    filings = filing_artifact.payload if filing_artifact is not None else {}
    if not isinstance(submissions, dict) or not submissions.get("name") and not submissions.get("filings"):
        raise ContractViolation("SEC submissions artifact is empty")
    if not isinstance(facts, dict) or not facts.get("facts"):
        raise ContractViolation("SEC companyfacts artifact is empty")
    document = filings.get("document") or filings.get("filing_document")
    accession = filings.get("accession_number")
    artifact_type = getattr(filing_artifact, "artifact_type", "")
    if artifact_type == "SEC_FILINGS_INDEX":
        raise ContractViolation("SEC filing index is not a full forensic document")
    if not isinstance(document, str) or not document.strip() or not accession:
        raise ContractViolation("SEC filing document/accession is incomplete")
    if re.search(r"(?:filing|document)\s+index", document[:500], re.I) and not re.search(r"(?:item\s+1|financial statements|liquidity|capital structure)", document, re.I):
        raise ContractViolation("SEC filing payload is index-only")


@dataclass(frozen=True)
class GateReceipt:
    gate_type: str
    decision: GateDecision
    input_hash: str
    rule_set_hash: str
    evaluated_at: str
    receipt_hash: str
    core_input_complete: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {"gate_type": self.gate_type, "decision": self.decision.value, "input_hash": self.input_hash, "rule_set_hash": self.rule_set_hash, "evaluated_at": self.evaluated_at, "receipt_hash": self.receipt_hash}
        if self.core_input_complete is not None:
            result["core_input_complete"] = self.core_input_complete
        return result


def _receipt(gate_type: str, decision: GateDecision, payload: Any, rules: EffectiveRuleSet, core_input_complete: bool | None = None) -> GateReceipt:
    input_hash = canonical_hash(payload)
    receipt_hash = canonical_hash({"gate_type": gate_type, "decision": decision.value, "input_hash": input_hash, "rule_set_hash": rules.rule_set_hash})
    return GateReceipt(gate_type, decision, input_hash, rules.rule_set_hash, utc_now(), receipt_hash, core_input_complete)


class StageGate:
    def evaluate(self, proposed_stage: str, deterministic_eligibility: bool, rules: EffectiveRuleSet) -> GateReceipt:
        decision = GateDecision.PASS if deterministic_eligibility and proposed_stage in {"STAGE_0", "STAGE_1", "STAGE_2"} else GateDecision.REJECT
        return _receipt("StageGate", decision, {"proposed_stage": proposed_stage, "eligible": deterministic_eligibility}, rules)


class CapitalPrescreenGate:
    HARD_EXCLUSIONS = {
        "active_atm", "large_shelf_and_financing_need", "toxic_convertible",
        "material_warrant", "imminent_financing", "cash_runway_critical",
        "identity_conflict", "accounting_red_flag", "liquidity_failure", "hard_exclusion",
    }
    CANONICAL_FIELDS = {"active_atm", "large_shelf_and_financing_need", "toxic_convertible", "material_warrant", "imminent_financing", "cash_runway_critical"}

    @staticmethod
    def normalize_tri_state(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("state", "UNKNOWN")
        if value is True:
            return "TRUE"
        if value is False:
            return "FALSE"
        normalized = str(value).upper()
        return normalized if normalized in {"TRUE", "FALSE", "UNKNOWN"} else "UNKNOWN"

    def evaluate(self, extraction: dict[str, Any], rules: EffectiveRuleSet) -> GateReceipt:
        state = self.normalize_tri_state
        present_fields = self.CANONICAL_FIELDS | (self.HARD_EXCLUSIONS - self.CANONICAL_FIELDS).intersection(extraction)
        excluded = sorted(k for k in present_fields if state(extraction.get(k, "UNKNOWN")) == "TRUE")
        unknown_blockers = sorted(k for k in present_fields if state(extraction.get(k, "UNKNOWN")) == "UNKNOWN")
        complete = extraction.get("complete") is True or extraction.get("extraction_status") == "COMPLETE"
        if not complete:
            unknown_blockers.append("complete")
        # In strict live discovery an incomplete cheap extraction is an
        # escalation signal, not proof that an otherwise ordinary issuer is
        # unsafe.  The flag is set only by the Python runtime after it has
        # bound the cheap SEC artifacts; callers cannot turn UNKNOWN into a
        # clean PASS.  The default remains fail-closed for all other callers.
        escalate = extraction.get("allow_full_forensic_escalation") is True
        if excluded:
            decision = GateDecision.REJECT
        elif unknown_blockers and escalate:
            decision = GateDecision.PASS_WITH_CONSTRAINTS
        else:
            decision = GateDecision.INSUFFICIENT_EVIDENCE if unknown_blockers else GateDecision.PASS
        return _receipt("CapitalPrescreenGate", decision, {"extraction": extraction, "excluded": excluded, "unknown_blockers": unknown_blockers, "escalation_required": bool(unknown_blockers and escalate)}, rules)


class MarketContextGate:
    # Canonical U.S. risk-context groups. SOXX and SMH are interchangeable
    # semiconductor proxies; every other group requires one exact observation.
    REQUIRED_ASSET_GROUPS = (
        ("SPY",), ("QQQ",), ("IWM",), ("SOXX", "SMH"), ("VIX",),
        ("US10Y",), ("DXY",), ("WTI",), ("BTC",), ("ETH",),
    )

    _ASSET_GROUP_DEFAULTS = {
        "SPY": "exchange", "QQQ": "exchange", "IWM": "exchange",
        "SOXX": "exchange", "SMH": "exchange", "VIX": "daily",
        "US10Y": "daily", "WTI": "daily", "DXY": "fx",
        "BTC": "crypto", "ETH": "crypto",
    }

    _ASSET_UNITS = {
        "SPY": ("USD_PER_SHARE", "USD"), "QQQ": ("USD_PER_SHARE", "USD"),
        "IWM": ("USD_PER_SHARE", "USD"), "SOXX": ("USD_PER_SHARE", "USD"),
        "SMH": ("USD_PER_SHARE", "USD"), "VIX": ("INDEX_POINTS", None),
        "US10Y": ("PERCENT", None), "DXY": ("INDEX_POINTS", None),
        "WTI": ("USD_PER_BARREL", "USD"), "BTC": ("USD_PER_COIN", "USD"),
        "ETH": ("USD_PER_COIN", "USD"),
    }

    @classmethod
    def _validate_live_asset(cls, symbol: str, receipt: dict[str, Any]) -> bool:
        """Validate normalized live value/unit/provenance when present.

        Older recorded contract fixtures intentionally omit these optional
        fields; strict provider observations always include them and are held
        to the complete semantic contract here.
        """
        if "value" not in receipt and "unit" not in receipt:
            return True
        if receipt.get("symbol") and str(receipt.get("symbol")).upper() != symbol:
            return False
        expected = cls._ASSET_UNITS.get(symbol)
        if expected is None or receipt.get("unit") != expected[0]:
            return False
        currency = receipt.get("currency")
        if expected[1] is not None and currency != expected[1]:
            return False
        if expected[1] is None and currency not in (None, ""):
            return False
        try:
            value = float(receipt.get("value"))
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value) or value <= 0:
            return False
        required_provenance = ("source_identifier", "raw_artifact_id", "evidence_id", "payload_hash")
        return all(str(receipt.get(key) or "").strip() for key in required_provenance)

    @staticmethod
    def _group_policy(rules: EffectiveRuleSet, group: str) -> tuple[float, float]:
        """Return (max age, max intra-group spread) in seconds.

        The legacy group deliberately retains the original global policy so
        recorded contracts that do not carry group metadata remain strict.
        """
        if group == "exchange":
            return (rules.max_age_market_context_exchange_hours * 3600.0,
                    rules.max_market_context_sync_exchange_hours * 3600.0)
        if group == "daily":
            return (rules.max_age_market_context_daily_hours * 3600.0,
                    rules.max_market_context_sync_daily_hours * 3600.0)
        if group == "fx":
            return (rules.max_age_market_context_fx_hours * 3600.0,
                    rules.max_market_context_sync_fx_hours * 3600.0)
        if group == "crypto":
            return (rules.max_age_market_context_crypto_hours * 3600.0,
                    rules.max_market_context_sync_crypto_hours * 3600.0)
        return (rules.max_age_market_context_hours * 3600.0,
                rules.max_market_context_sync_spread_hours * 3600.0)

    def evaluate(self, context: dict[str, Any], rules: EffectiveRuleSet, *, evaluation_time: datetime | None = None) -> GateReceipt:
        assets = context.get("assets") if isinstance(context.get("assets"), dict) else {}
        missing_groups: list[str] = []
        invalid_assets: list[str] = []
        observed_times: list[datetime] = []
        selected_assets: list[str] = []
        max_age_seconds = float(rules.max_age_market_context_hours) * 3600.0
        sync_spread_seconds = float(rules.max_market_context_sync_spread_hours) * 3600.0
        group_times: dict[str, list[datetime]] = {}
        group_diagnostics: dict[str, dict[str, Any]] = {}
        latest_session_date = _latest_completed_us_session_date(evaluation_time)

        for group in self.REQUIRED_ASSET_GROUPS:
            selected = next((symbol for symbol in group if isinstance(assets.get(symbol), dict)), None)
            if selected is None:
                missing_groups.append("/".join(group))
                continue
            receipt = assets[selected]
            observed_at = receipt.get("observed_at")
            if (not observed_at or not receipt.get("source") or
                    int(receipt.get("observation_count") or 0) < 2 or
                    not self._validate_live_asset(selected, receipt)):
                invalid_assets.append(selected)
                continue
            try:
                # Provider/normalizer must explicitly attach a synchronization
                # group.  Bare recorded contracts stay on the legacy global
                # policy; the gate never guesses a more permissive clock from
                # an asset symbol alone.
                group = str(receipt.get("sync_group") or "legacy")
                asset_max_age, _ = self._group_policy(rules, group)
                observed_dt = _parse_timestamp(str(observed_at))
                if age_seconds(str(observed_at), now=evaluation_time, max_future_skew_seconds=rules.max_future_skew_seconds) > asset_max_age:
                    invalid_assets.append(selected)
                    continue
                # Exchange-session assets must belong to the latest completed U.S.
                # equity session. FX observations use their own clock and can
                # legitimately lag the U.S. equity session date while still
                # satisfying FX max-age and synchronization rules.
                if group == "exchange" and observed_dt.date() < latest_session_date:
                    invalid_assets.append(selected)
                    continue
                observed_times.append(observed_dt)
                group_times.setdefault(group, []).append(observed_dt)
                selected_assets.append(selected)
            except ContractViolation:
                invalid_assets.append(selected)

        synchronized = False
        spread_seconds: float | None = None
        if observed_times and len(observed_times) == len(self.REQUIRED_ASSET_GROUPS):
            spread_seconds = (max(observed_times) - min(observed_times)).total_seconds()
            synchronized = True
            for group, timestamps in group_times.items():
                _, group_max_spread = self._group_policy(rules, group)
                group_spread = (max(timestamps) - min(timestamps)).total_seconds()
                group_diagnostics[group] = {
                    "spread_seconds": group_spread,
                    "max_spread_seconds": group_max_spread,
                    "synchronized": group_spread <= group_max_spread,
                    "asset_count": len(timestamps),
                }
                synchronized = synchronized and group_spread <= group_max_spread
            # Legacy recorded contexts have no group metadata.  Preserve the
            # original all-assets synchronization contract for those inputs.
            if not group_diagnostics or set(group_diagnostics) == {"legacy"}:
                synchronized = spread_seconds <= sync_spread_seconds

        labels_complete = all(context.get(key) not in (None, "", "UNKNOWN") for key in ("regime", "breadth", "volatility"))
        normalized_complete = context.get("normalization_status") == "COMPLETE"
        computed_complete = not missing_groups and not invalid_assets and synchronized and labels_complete and normalized_complete
        payload = {
            **context,
            "provider_complete_claim": context.get("complete"),
            "complete": computed_complete,
            "required_asset_groups": [list(group) for group in self.REQUIRED_ASSET_GROUPS],
            "selected_assets": selected_assets,
            "missing_asset_groups": missing_groups,
            "invalid_assets": invalid_assets,
            "synchronized": synchronized,
            "observation_spread_seconds": spread_seconds,
            "max_age_seconds": max_age_seconds,
            "max_sync_spread_seconds": sync_spread_seconds,
            "synchronization_groups": group_diagnostics,
        }
        decision = GateDecision.PASS if computed_complete else GateDecision.INSUFFICIENT_EVIDENCE
        return _receipt("MarketContextGate", decision, payload, rules, core_input_complete=computed_complete)


class SectorGate:
    def evaluate(self, sector: dict[str, Any], rules: EffectiveRuleSet) -> GateReceipt:
        decision = GateDecision.PASS if sector.get("eligible") is True else GateDecision.INSUFFICIENT_EVIDENCE
        return _receipt("SectorGate", decision, sector, rules)


class MarketExecutionGate:
    def evaluate(self, market: dict[str, Any], rules: EffectiveRuleSet) -> GateReceipt:
        complete = market.get("core_input_complete") is True
        if not complete:
            decision = GateDecision.INSUFFICIENT_EVIDENCE
        elif market.get("reject"):
            decision = GateDecision.REJECT
        elif market.get("constraints"):
            decision = GateDecision.PASS_WITH_CONSTRAINTS
        else:
            decision = GateDecision.PASS
        return _receipt("MarketExecutionGate", decision, market, rules, core_input_complete=complete)


class RiskEngine:
    def assess(self, current_price: float, execution_stop: float, structural_asymmetry: float, probability_weighted_ev: float, account_equity: float, risk_budget_pct: float = 1.0, worst_plausible_gap: float = 0.0, event_risk_pct: float = 0.0, max_position_shares: int | None = None) -> dict[str, Any]:
        if current_price <= 0 or execution_stop <= 0 or current_price <= execution_stop:
            raise ContractViolation("execution stop must be below current price")
        if account_equity <= 0 or risk_budget_pct <= 0:
            raise ContractViolation("account equity and risk budget must be positive")
        gap = max(0.0, float(worst_plausible_gap))
        risk_per_share = (current_price - execution_stop) + gap
        risk_budget = account_equity * risk_budget_pct / 100.0
        if event_risk_pct > 0:
            risk_budget *= max(0.0, 1.0 - min(float(event_risk_pct) / 100.0, 1.0))
        shares = max(0, int(risk_budget // risk_per_share))
        if max_position_shares is not None:
            shares = min(shares, int(max_position_shares))
        execution_rr = structural_asymmetry / risk_per_share if risk_per_share else 0.0
        return {"arithmetic_source": "PYTHON_RISK_ENGINE", "risk_per_share": risk_per_share, "risk_budget": risk_budget, "shares": shares, "risk_target_position_shares": shares, "execution_rr": execution_rr, "probability_weighted_ev": probability_weighted_ev, "worst_plausible_gap": gap, "event_risk_pct": event_risk_pct, "max_position_shares": max_position_shares}


class PositionSizer:
    """Thin explicit sizing layer; callers cannot supply authoritative shares."""

    def __init__(self, risk_engine: RiskEngine | None = None) -> None:
        self.risk_engine = risk_engine or RiskEngine()

    def size(self, *, current_price: float, execution_stop: float, account_equity: float, per_position_budget_pct: float, portfolio_budget_pct: float, worst_plausible_gap: float = 0.0, event_risk_pct: float = 0.0, maximum_position_shares: int | None = None, structural_asymmetry: float = 0.0, probability_weighted_ev: float = 0.0) -> dict[str, Any]:
        if portfolio_budget_pct <= 0 or per_position_budget_pct <= 0:
            raise ContractViolation("risk budgets must be positive")
        position_budget = min(float(per_position_budget_pct), float(portfolio_budget_pct))
        unconstrained = self.risk_engine.assess(current_price, execution_stop, structural_asymmetry, probability_weighted_ev, account_equity, position_budget, worst_plausible_gap, event_risk_pct, None)
        result = dict(unconstrained)
        result["maximum_allowed_position"] = unconstrained["shares"]
        result["shares"] = min(unconstrained["shares"], int(maximum_position_shares)) if maximum_position_shares is not None else unconstrained["shares"]
        result["max_position_shares"] = maximum_position_shares
        result.update({"per_position_budget_pct": float(per_position_budget_pct), "portfolio_budget_pct": float(portfolio_budget_pct)})
        return result


def validate_failure_paths(paths: list[dict[str, Any]], minimum: int = 3) -> None:
    if len(paths) < minimum:
        raise ContractViolation("at least three failure paths are required")
    categories = [path.get("category") for path in paths]
    if len(set(categories)) < minimum:
        raise ContractViolation("failure path categories must be independent")
    keys = [(str(path.get("scenario", "")).strip().casefold(), str(path.get("causal_path", "")).strip().casefold()) for path in paths]
    if len(keys) != len(set(keys)):
        raise ContractViolation("failure path scenario and causal path pairs must be unique")
    if any(path.get("severity") == "CRITICAL" and not path.get("source_evidence_ids") for path in paths):
        raise ContractViolation("critical failure paths require evidence")


def validate_starter_plan(plan: dict[str, Any], rules: EffectiveRuleSet) -> None:
    required = {"starter_zone", "starter_shares", "starter_capital_pct", "maximum_position", "execution_stop", "thesis_stop", "structural_bear", "worst_plausible_gap", "maximum_account_loss", "maximum_holding_period", "time_stop_or_reassessment_condition", "breakout_response", "pullback_response", "planned_add"}
    missing = sorted(required - set(plan))
    if missing:
        raise ContractViolation(f"StarterPlanV2 missing fields: {missing}")
    starter = int(plan["starter_shares"])
    starter_pct = plan["starter_capital_pct"]
    maximum = plan["maximum_position"]
    planned = plan["planned_add"]
    resulting = planned["resulting_position_cap"]
    if starter <= 0 or float(starter_pct) <= 0:
        raise ContractViolation("starter position must be positive")
    if int(maximum["shares"]) <= 0 or float(maximum["capital_pct"]) <= 0:
        raise ContractViolation("maximum position must be positive")
    if starter > maximum["shares"] or starter_pct > maximum["capital_pct"]:
        raise ContractViolation("starter exceeds maximum position")
    if resulting["shares"] < starter or resulting["capital_pct"] < starter_pct:
        raise ContractViolation("resulting position is below starter")
    if planned.get("planned_add_shares") is not None and starter + planned["planned_add_shares"] > resulting["shares"]:
        raise ContractViolation("starter plus planned add exceeds resulting cap")
    if planned.get("planned_add_capital_pct") is not None and starter_pct + planned["planned_add_capital_pct"] > resulting["capital_pct"]:
        raise ContractViolation("starter plus planned add percentage exceeds resulting cap")
    if resulting["shares"] > maximum["shares"] or resulting["capital_pct"] > maximum["capital_pct"]:
        raise ContractViolation("resulting position exceeds maximum")
    holding = plan["maximum_holding_period"]
    if holding["minimum_days"] > holding["maximum_days"] or holding["maximum_days"] > rules.strategy_max_days:
        raise ContractViolation("starter holding horizon is outside active EffectiveRuleSet")
    if not planned.get("trigger_id") or not planned.get("required_evidence_classes"):
        raise ContractViolation("planned add requires trigger and strengthening evidence classes")


def validate_add_lineage(target_security_id: str, plan: dict[str, Any], position: dict[str, Any], prior: dict[str, Any], delta: dict[str, Any], strengthening: dict[str, Any]) -> None:
    if position.get("receipt_type") != "PositionSnapshotReceiptV2" or position.get("subject_id") != target_security_id or position.get("position_exists") is not True:
        raise ContractViolation("invalid PositionSnapshotReceiptV2")
    if prior.get("receipt_type") != "PriorAddTriggerReceiptV2" or prior.get("subject_id") != target_security_id or prior.get("trigger_id") != plan.get("trigger_id") or prior.get("trigger_type") != plan.get("trigger_type"):
        raise ContractViolation("invalid prior ADD trigger lineage")
    if delta.get("receipt_type") != "FreshnessDeltaReceiptV2" or delta.get("subject_id") != target_security_id or delta.get("delta_state") != "STRENGTHENED":
        raise ContractViolation("ADD requires strengthened freshness delta")
    if strengthening.get("receipt_type") != "StrengtheningEvidenceReceiptV2" or strengthening.get("subject_id") != target_security_id:
        raise ContractViolation("invalid strengthening evidence receipt")
    if strengthening.get("security_id", target_security_id) != target_security_id:
        raise ContractViolation("strengthening evidence security mismatch")
    planned = set(plan.get("strengthening_evidence_ids") or [])
    if not planned or not planned.issubset(set(delta.get("strengthening_evidence_ids") or [])) or not planned.issubset(set(strengthening.get("strengthening_evidence_ids") or [])):
        raise ContractViolation("ADD evidence lineage is not a non-empty subset")


def validate_recommendation_identity(action: ExecutionAction, target_security_id: str, position: dict[str, Any]) -> None:
    if action in {ExecutionAction.ADD, ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT}:
        if position.get("subject_id") != target_security_id or position.get("position_exists") is not True:
            raise ContractViolation("position receipt subject does not match target security")


def validate_final_allocation_contract(action: ExecutionAction, allocation: dict[str, Any], rules: EffectiveRuleSet) -> None:
    """Validate transaction-delta versus target-position semantics before DB commit.

    RiskEngine's target is a resulting position ceiling/target. The allocation
    shares field is the transaction delta; they must never be compared directly.
    """
    def _int(value: Any, name: str, *, minimum: int = 0) -> int:
        if isinstance(value, bool):
            raise ContractViolation(f"{name} must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ContractViolation(f"{name} must be an integer") from exc
        if parsed < minimum:
            raise ContractViolation(f"{name} is below the allowed minimum")
        return parsed

    tx = _int(allocation.get("shares", allocation.get("transaction_shares", 0)), "transaction_shares")
    try:
        capital = float(allocation.get("capital_pct", 0.0))
    except (TypeError, ValueError) as exc:
        raise ContractViolation("capital_pct must be finite") from exc
    if not math.isfinite(capital) or capital < 0:
        raise ContractViolation("allocation capital_pct must be finite and non-negative")

    risk = allocation.get("risk") or {}
    target_raw = risk.get("risk_target_position_shares")
    if target_raw is None:
        target_raw = risk.get("shares")
    target = _int(target_raw, "risk_target_position_shares")
    current = _int(allocation.get("current_position_shares", 0), "current_position_shares")
    resulting = _int(allocation.get("resulting_position_shares", current + tx), "resulting_position_shares")

    if action in {ExecutionAction.NO_TRADE, ExecutionAction.WATCH}:
        if tx != 0 or capital != 0 or resulting != current:
            raise ContractViolation("non-actionable action must have zero transaction and capital")
        return

    if action in {ExecutionAction.STARTER, ExecutionAction.ADD, ExecutionAction.FULL}:
        if risk.get("arithmetic_source") != "PYTHON_RISK_ENGINE":
            raise ContractViolation("allocation must carry Python RiskEngine assessment")
        if not risk.get("risk_budget_source"):
            raise ContractViolation("allocation must identify the Python risk-budget source")

    if action == ExecutionAction.STARTER:
        if current != 0 or tx <= 0 or resulting != tx or resulting > target or capital <= 0:
            raise ContractViolation("STARTER transaction/resulting position violates target semantics")
        plan = allocation.get("starter_plan")
        if not isinstance(plan, dict):
            raise ContractViolation("STARTER allocation requires starter_plan")
        validate_starter_plan(plan, rules)
        if resulting > int(plan["maximum_position"]["shares"]):
            raise ContractViolation("STARTER exceeds maximum position")

    elif action == ExecutionAction.ADD:
        if current <= 0 or tx <= 0 or resulting != current + tx or resulting > target or capital <= 0:
            raise ContractViolation("ADD transaction/resulting position violates target semantics")
        plan = allocation.get("add_plan") or {}
        cap = (plan.get("resulting_position_cap") or {}).get("shares")
        if cap is None or resulting > _int(cap, "AddPlan resulting_position_cap.shares"):
            raise ContractViolation("ADD exceeds AddPlan resulting position cap")
        if not allocation.get("strengthening_evidence_ids"):
            raise ContractViolation("ADD allocation requires strengthening evidence ids")

    elif action == ExecutionAction.FULL:
        if current <= 0 or target < current or tx != target - current or resulting != target:
            raise ContractViolation("FULL transaction must bridge current position to risk target")
        if tx == 0 and capital != 0:
            raise ContractViolation("zero-delta FULL must have zero capital allocation")
        if tx > 0 and capital <= 0:
            raise ContractViolation("positive-delta FULL requires positive capital allocation")

    elif action == ExecutionAction.TRIM:
        if current <= 0 or tx <= 0 or resulting != current - tx or resulting <= 0:
            raise ContractViolation("TRIM must reduce but not liquidate the existing position")
        if target != resulting:
            raise ContractViolation("TRIM resulting position must equal RiskEngine target")

    elif action == ExecutionAction.EXIT:
        if current <= 0 or tx != current or resulting != 0:
            raise ContractViolation("EXIT must transact the full existing position")

    else:
        raise ContractViolation(f"unsupported ExecutionAction: {action}")


class QualifiedCandidateGate:
    """Repository-backed pool gate; candidate flags are never sufficient."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def evaluate(self, run_id: str, security_id: str) -> GateReceipt:
        qualified, missing = self.store.qualified_candidate_status(run_id, security_id)
        run = self.store.get_run(run_id)
        return _receipt("QualifiedCandidateGate", GateDecision.PASS if qualified else GateDecision.INSUFFICIENT_EVIDENCE, {"security_id": security_id, "missing": missing}, run.rule_set)


class FinalAllocationGate:
    """Single Python writer facade around the transaction-scoped store gate."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def commit(self, run: Any, action: ExecutionAction, allocation: dict[str, Any]) -> str:
        validate_final_allocation_contract(action, allocation, run.rule_set)
        return self.store.commit_final_allocation(run, action.value, allocation)
