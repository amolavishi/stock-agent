"""Evidence-linked reverse valuation for HUNT qualification.

This module turns persisted market/research observations into deterministic
valuation arithmetic.  It never invents consensus, probabilities, multiples,
ExecutionAction, or position size.  Missing numeric evidence fails closed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from .models import EffectiveRuleSet, GateDecision, canonical_hash, utc_now


REVERSE_VALUATION_VERSION = "hunt-reverse-valuation-v1"
EXPECTATION_GAP_GATE_VERSION = "expectation-gap-gate-v1"


def _finite(value: Any, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    return number


def observed_market_price(row: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in ("price", "current_price", "last_price", "lastPrice"):
        value = _finite(row.get(key), positive=True)
        if value is not None:
            return value, key
    prices = row.get("prices")
    if isinstance(prices, list) and prices:
        value = _finite(prices[-1], positive=True)
        if value is not None:
            return value, "prices[-1]"
    return None, None


def _valuation_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    values = [payload]
    source = payload.get("source")
    if isinstance(source, dict):
        values.append(source)
    provider_payload = payload.get("provider_payload")
    if isinstance(provider_payload, dict):
        values.append(provider_payload)
        evidence = provider_payload.get("evidence")
        if isinstance(evidence, dict):
            values.append(evidence)
    return values


def extract_valuation_inputs(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return a structured provider observation without inferring missing data."""
    for source in _valuation_sources(payload):
        raw = source.get("valuation_inputs") or source.get("reverse_valuation_inputs")
        if isinstance(raw, dict):
            result = dict(raw)
            result.setdefault("source_url", source.get("source_url") or source.get("url") or payload.get("source_url"))
            result.setdefault("source_observed_at", source.get("source_observed_at") or source.get("published_at") or source.get("observed_at") or payload.get("source_observed_at") or payload.get("observed_at"))
            return result
    return None


@dataclass(frozen=True)
class ReverseValuationReceipt:
    receipt_type: str
    security_id: str
    status: str
    valuation_basis: str
    metric_name: str
    current_price: float
    diluted_shares: float
    net_cash: float
    forward_metric_value: float
    benchmark_multiple: float
    current_market_cap: float
    current_enterprise_value: float
    current_multiple: float
    benchmark_enterprise_value: float
    benchmark_equity_value: float
    benchmark_implied_price: float
    benchmark_implied_upside_pct: float
    target_30_price: float
    target_30_required_multiple: float
    target_30_required_metric: float
    target_30_required_metric_growth_pct: float
    target_60_price: float
    target_60_required_multiple: float
    target_60_required_metric: float
    target_60_required_metric_growth_pct: float
    benchmark_description: str
    market_artifact_id: str
    research_artifact_id: str
    market_evidence_id: str
    research_evidence_id: str
    source_result_ids: tuple[str, ...]
    source_url: str
    source_observed_at: str
    calculation_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_type": self.receipt_type,
            "security_id": self.security_id,
            "status": self.status,
            "valuation_basis": self.valuation_basis,
            "metric_name": self.metric_name,
            "current_price": self.current_price,
            "diluted_shares": self.diluted_shares,
            "net_cash": self.net_cash,
            "forward_metric_value": self.forward_metric_value,
            "benchmark_multiple": self.benchmark_multiple,
            "current_market_cap": self.current_market_cap,
            "current_enterprise_value": self.current_enterprise_value,
            "current_multiple": self.current_multiple,
            "benchmark_enterprise_value": self.benchmark_enterprise_value,
            "benchmark_equity_value": self.benchmark_equity_value,
            "benchmark_implied_price": self.benchmark_implied_price,
            "benchmark_implied_upside_pct": self.benchmark_implied_upside_pct,
            "target_30_price": self.target_30_price,
            "target_30_required_multiple": self.target_30_required_multiple,
            "target_30_required_metric": self.target_30_required_metric,
            "target_30_required_metric_growth_pct": self.target_30_required_metric_growth_pct,
            "target_60_price": self.target_60_price,
            "target_60_required_multiple": self.target_60_required_multiple,
            "target_60_required_metric": self.target_60_required_metric,
            "target_60_required_metric_growth_pct": self.target_60_required_metric_growth_pct,
            "benchmark_description": self.benchmark_description,
            "market_artifact_id": self.market_artifact_id,
            "research_artifact_id": self.research_artifact_id,
            "market_evidence_id": self.market_evidence_id,
            "research_evidence_id": self.research_evidence_id,
            "source_result_ids": list(self.source_result_ids),
            "source_url": self.source_url,
            "source_observed_at": self.source_observed_at,
            "calculation_hash": self.calculation_hash,
            "calculation_version": REVERSE_VALUATION_VERSION,
        }


def build_reverse_valuation_receipt(
    *,
    security_id: str,
    current_price: float,
    valuation_inputs: dict[str, Any],
    market_artifact_id: str,
    research_artifact_id: str,
    market_evidence_id: str,
    research_evidence_id: str,
    source_result_ids: list[str] | tuple[str, ...],
) -> ReverseValuationReceipt | None:
    """Compute reverse valuation only when every economic input is evidenced."""
    price = _finite(current_price, positive=True)
    shares = _finite(valuation_inputs.get("diluted_shares") or valuation_inputs.get("shares_diluted"), positive=True)
    metric_value = _finite(valuation_inputs.get("forward_metric_value") or valuation_inputs.get("metric_value"), positive=True)
    benchmark_multiple = _finite(valuation_inputs.get("benchmark_multiple"), positive=True)
    net_cash_raw = valuation_inputs.get("net_cash")
    net_debt_raw = valuation_inputs.get("net_debt")
    if net_cash_raw is not None and net_debt_raw is not None:
        return None
    if net_cash_raw is not None:
        net_cash = _finite(net_cash_raw)
    elif net_debt_raw is not None:
        debt = _finite(net_debt_raw)
        net_cash = -debt if debt is not None else None
    else:
        net_cash = None
    metric_name = str(valuation_inputs.get("metric_name") or "").strip().upper()
    valuation_basis = str(valuation_inputs.get("valuation_basis") or "").strip().upper()
    benchmark_description = str(valuation_inputs.get("benchmark_description") or "").strip()
    source_url = str(valuation_inputs.get("source_url") or "").strip()
    source_observed_at = str(valuation_inputs.get("source_observed_at") or "").strip()
    result_ids = tuple(sorted(set(str(item) for item in source_result_ids if item)))
    if any(value is None for value in (price, shares, metric_value, benchmark_multiple, net_cash)):
        return None
    if not metric_name or valuation_basis not in {"EV_REVENUE", "EV_EBITDA", "EV_FCF"}:
        return None
    expected_metric = {"EV_REVENUE": "REVENUE", "EV_EBITDA": "EBITDA", "EV_FCF": "FCF"}[valuation_basis]
    if expected_metric not in metric_name:
        return None
    if not benchmark_description or not source_url or not source_observed_at or not market_artifact_id or not research_artifact_id or not market_evidence_id or not research_evidence_id or not result_ids:
        return None

    market_cap = price * shares
    enterprise_value = market_cap - net_cash
    if enterprise_value <= 0:
        return None
    current_multiple = enterprise_value / metric_value
    benchmark_ev = benchmark_multiple * metric_value
    benchmark_equity = benchmark_ev + net_cash
    implied_price = benchmark_equity / shares
    if implied_price <= 0:
        return None
    implied_upside = implied_price / price - 1.0

    def target_math(upside: float) -> tuple[float, float, float, float]:
        target_price = price * (1.0 + upside)
        target_equity = target_price * shares
        target_ev = target_equity - net_cash
        required_multiple = target_ev / metric_value
        required_metric = target_ev / benchmark_multiple
        growth = required_metric / metric_value - 1.0
        return target_price, required_multiple, required_metric, growth

    t30_price, t30_mult, t30_metric, t30_growth = target_math(0.30)
    t60_price, t60_mult, t60_metric, t60_growth = target_math(0.60)
    values = {
        "receipt_type": "HuntReverseValuationReceiptV1",
        "security_id": security_id,
        "status": "COMPLETE",
        "valuation_basis": valuation_basis,
        "metric_name": metric_name,
        "current_price": price,
        "diluted_shares": shares,
        "net_cash": net_cash,
        "forward_metric_value": metric_value,
        "benchmark_multiple": benchmark_multiple,
        "current_market_cap": market_cap,
        "current_enterprise_value": enterprise_value,
        "current_multiple": current_multiple,
        "benchmark_enterprise_value": benchmark_ev,
        "benchmark_equity_value": benchmark_equity,
        "benchmark_implied_price": implied_price,
        "benchmark_implied_upside_pct": implied_upside,
        "target_30_price": t30_price,
        "target_30_required_multiple": t30_mult,
        "target_30_required_metric": t30_metric,
        "target_30_required_metric_growth_pct": t30_growth,
        "target_60_price": t60_price,
        "target_60_required_multiple": t60_mult,
        "target_60_required_metric": t60_metric,
        "target_60_required_metric_growth_pct": t60_growth,
        "benchmark_description": benchmark_description,
        "market_artifact_id": market_artifact_id,
        "research_artifact_id": research_artifact_id,
        "market_evidence_id": market_evidence_id,
        "research_evidence_id": research_evidence_id,
        "source_result_ids": list(result_ids),
        "source_url": source_url,
        "source_observed_at": source_observed_at,
        "calculation_version": REVERSE_VALUATION_VERSION,
    }
    calculation_hash = canonical_hash(values)
    return ReverseValuationReceipt(
        values["receipt_type"], security_id, "COMPLETE", valuation_basis, metric_name,
        price, shares, net_cash, metric_value, benchmark_multiple, market_cap,
        enterprise_value, current_multiple, benchmark_ev, benchmark_equity,
        implied_price, implied_upside, t30_price, t30_mult, t30_metric, t30_growth,
        t60_price, t60_mult, t60_metric, t60_growth, benchmark_description,
        market_artifact_id, research_artifact_id, market_evidence_id,
        research_evidence_id, result_ids, source_url, source_observed_at,
        calculation_hash,
    )


@dataclass(frozen=True)
class ExpectationGapGateReceipt:
    gate_type: str
    decision: GateDecision
    input_hash: str
    rule_set_hash: str
    evaluated_at: str
    receipt_hash: str
    core_input_complete: bool
    implied_upside_pct: float | None
    required_min_upside_pct: float
    strength: str
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_type": self.gate_type,
            "decision": self.decision.value,
            "input_hash": self.input_hash,
            "rule_set_hash": self.rule_set_hash,
            "evaluated_at": self.evaluated_at,
            "receipt_hash": self.receipt_hash,
            "core_input_complete": self.core_input_complete,
            "implied_upside_pct": self.implied_upside_pct,
            "required_min_upside_pct": self.required_min_upside_pct,
            "strength": self.strength,
            "reason_codes": list(self.reason_codes),
            "gate_version": EXPECTATION_GAP_GATE_VERSION,
        }


class ExpectationGapGate:
    """Require evidence completeness and strategy-level upside before pool entry."""

    def evaluate(self, receipt: ReverseValuationReceipt | None, rules: EffectiveRuleSet) -> ExpectationGapGateReceipt:
        reasons: list[str] = []
        if receipt is None or receipt.status != "COMPLETE":
            decision = GateDecision.INSUFFICIENT_EVIDENCE
            complete = False
            implied = None
            strength = "UNKNOWN"
            input_payload: Any = {"receipt": None}
        else:
            complete = True
            implied = float(receipt.benchmark_implied_upside_pct)
            input_payload = receipt.as_dict()
            if implied >= float(rules.hunt_strong_upside_pct):
                strength = "STRONG"
            elif implied >= float(rules.hunt_min_upside_pct):
                strength = "QUALIFYING"
            else:
                strength = "INSUFFICIENT_UPSIDE"
                reasons.append("EXPECTATION_GAP_BELOW_STRATEGY_THRESHOLD")
            decision = GateDecision.PASS if implied >= float(rules.hunt_min_upside_pct) else GateDecision.REJECT
        if not complete:
            reasons.append("EXPECTATION_GAP_NUMERIC_EVIDENCE_MISSING")
        input_hash = canonical_hash(input_payload)
        payload = {
            "gate_type": "ExpectationGapGate",
            "decision": decision.value,
            "input_hash": input_hash,
            "rule_set_hash": rules.rule_set_hash,
            "core_input_complete": complete,
            "implied_upside_pct": implied,
            "required_min_upside_pct": float(rules.hunt_min_upside_pct),
            "strength": strength,
            "reason_codes": reasons,
            "gate_version": EXPECTATION_GAP_GATE_VERSION,
        }
        receipt_hash = canonical_hash(payload)
        return ExpectationGapGateReceipt(
            "ExpectationGapGate", decision, input_hash, rules.rule_set_hash,
            utc_now(), receipt_hash, complete, implied,
            float(rules.hunt_min_upside_pct), strength, tuple(reasons),
        )

