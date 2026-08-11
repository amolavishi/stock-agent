from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from .features import known_field, value
from .schemas import CandidateFeatureSnapshot


@dataclass(frozen=True)
class FuelRules:
    minimum_independent_families: int = 2
    dominant_catalyst_min_strength: float = 85.0


def catalyst_weight(age_days: float, half_life_days: float) -> float:
    if age_days < 0 or half_life_days <= 0:
        return 0.0
    return math.exp(-math.log(2) * age_days / half_life_days)


class FuelEngine:
    def __init__(self, rules: FuelRules | None = None):
        self.rules = rules or FuelRules()

    def evaluate_preliminary(self, candidate: CandidateFeatureSnapshot) -> CandidateFeatureSnapshot:
        """Evaluate cheap fuel without turning missing fundamentals into a veto.

        A market-only candidate is allowed to remain in the enrichment funnel.
        ``PENDING_ENRICHMENT`` is deliberately distinct from a final fuel
        failure, so the final hard veto remains available after CompanyFacts
        hydration.
        """
        self._normalize(candidate)
        if self._passes(self._families(candidate)):
            status = "PASS"
        elif self._fundamental_hydration_attempted(candidate):
            status = "PRELIMINARY_FAIL"
        else:
            status = "PENDING_ENRICHMENT"
        candidate.gate_results["preliminary_fuel_gate"] = status
        candidate.gate_results["fuel_gate"] = status
        candidate.gate_results["final_fuel_evaluated"] = "NO"
        return candidate

    def evaluate(self, candidate: CandidateFeatureSnapshot) -> CandidateFeatureSnapshot:
        """Evaluate the final fuel gate after all available enrichment."""
        self._normalize(candidate)
        families = self._families(candidate)
        passed = self._passes(families)
        candidate.gate_results["fuel_gate"] = "PASS" if passed else "FAIL"
        candidate.gate_results["final_fuel_evaluated"] = "YES"
        candidate.gate_results["final_fuel_status"] = "PASS" if passed else "FAIL"
        return candidate

    def _normalize(self, candidate: CandidateFeatureSnapshot) -> None:
        families: dict[str, list[dict[str, Any]]] = {}
        for raw in candidate.fuel_events:
            event = dict(raw)
            family = str(event.get("signal_family") or event.get("family") or "UNKNOWN")
            if family == "UNKNOWN":
                continue
            event["event_id"] = str(event.get("event_id") or event.get("source_evidence_id") or family)
            event.setdefault("half_life_days", 30.0)
            event.setdefault("event_at", candidate.as_of[:10])
            try:
                age_days = max(0.0, (date.fromisoformat(candidate.as_of[:10]) -
                                     date.fromisoformat(str(event["event_at"])[:10])).days)
            except (TypeError, ValueError):
                age_days = 10_000.0
            event["age_days"] = age_days
            event["freshness_weight"] = catalyst_weight(age_days, float(event["half_life_days"]))
            event["effective_strength"] = float(event.get("strength", 0) or 0) * event["freshness_weight"]
            families.setdefault(family, []).append(event)
        candidate.signal_families = sorted(families)
        candidate.fuel_events = [event for group in families.values() for event in group]
        if not families and "NO_FUEL" not in candidate.risk_flags:
            candidate.risk_flags.append("NO_FUEL")

    @staticmethod
    def _families(candidate: CandidateFeatureSnapshot) -> dict[str, list[dict[str, Any]]]:
        families: dict[str, list[dict[str, Any]]] = {}
        for event in candidate.fuel_events:
            family = str(event.get("signal_family") or event.get("family") or "UNKNOWN")
            if family != "UNKNOWN":
                families.setdefault(family, []).append(event)
        return families

    @staticmethod
    def _fundamental_hydration_attempted(candidate: CandidateFeatureSnapshot) -> bool:
        fields = {
            "primary_financial_evidence", "revenue_growth_current_pct",
            "revenue_growth_acceleration_pp", "gross_margin_delta_pp",
            "operating_margin_delta_pp", "operating_cash_flow_inflection",
            "fcf_inflection",
        }
        return any(name in candidate.fields for name in fields)

    def _passes(self, families: dict[str, list[dict[str, Any]]]) -> bool:
        if len(families) >= self.rules.minimum_independent_families:
            return True
        return any(float(event.get("effective_strength", event.get("strength", 0)) or 0) >= self.rules.dominant_catalyst_min_strength
                   and event.get("material", False)
                   and event.get("source_evidence_id")
                   and float(event.get("freshness_weight", 0)) > 0
                   for events in families.values() for event in events)


def infer_fuel_events(candidate: CandidateFeatureSnapshot) -> list[dict[str, Any]]:
    # Preserve provider-sourced events, but replace events previously derived
    # by this resolver so re-inference after hydration is deterministic.
    events: list[dict[str, Any]] = [dict(event) for event in candidate.fuel_events
                                    if not event.get("_inferred")]
    acceleration_field = ("revenue_growth_acceleration_pp" if known_field(candidate, "revenue_growth_acceleration_pp")
                          else "revenue_growth_acceleration")
    if known_field(candidate, acceleration_field) and value(candidate, acceleration_field) > 0:
        events.append({"event_type": "REVENUE_ACCELERATION", "signal_family": "FUNDAMENTAL",
                       "strength": min(100, 50 + value(candidate, acceleration_field)), "material": True,
                       "source_evidence_id": next(iter(candidate.fields[acceleration_field].source_ids), ""),
                       "_inferred": True})
    margin_field = ("gross_margin_delta_pp" if known_field(candidate, "gross_margin_delta_pp") else "margin_delta")
    if known_field(candidate, margin_field) and value(candidate, margin_field) > 0:
        events.append({"event_type": "MARGIN_INFLECTION", "signal_family": "FUNDAMENTAL",
                       "strength": min(100, 50 + value(candidate, margin_field) * 2), "material": True,
                       "source_evidence_id": next(iter(candidate.fields[margin_field].source_ids), ""),
                       "_inferred": True})
    if known_field(candidate, "return_5d_pct") and known_field(candidate, "relative_volume_completed_bar"):
        if (value(candidate, "return_5d_pct") or 0) > 0 and (value(candidate, "relative_volume_completed_bar") or 0) >= 1.2:
            events.append({"event_type": "RELATIVE_STRENGTH_INFLECTION", "signal_family": "FLOW",
                           "strength": 60, "material": True, "_inferred": True})
    return events
