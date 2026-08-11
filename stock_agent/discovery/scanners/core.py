from __future__ import annotations

from ..features import known_field, value
from ..gates import route_gate
from ..schemas import CandidateFeatureSnapshot, DiscoveryContext, ScannerResult


class _CoreScanner:
    version = "discovery_scanners_v001"

    def _result(self, candidate, hit, strength, required, reasons=(), families=(), unknown=()):
        return ScannerResult(self.name, self.version, hit, max(0, min(100, strength)), required,
                             not required, tuple(reasons), tuple(families), (), (), tuple(unknown))


class GeneralInflectionScanner(_CoreScanner):
    name = "GENERAL_INFLECTION"

    def evaluate(self, candidate: CandidateFeatureSnapshot, context: DiscoveryContext) -> ScannerResult:
        route_ok, reasons = route_gate(candidate, self.name)
        required = route_ok and candidate.gate_results.get("global_gate") == "PASS" and candidate.gate_results.get("fuel_gate") == "PASS"
        if not required:
            return self._result(candidate, False, 0, False, reasons or ("GLOBAL_OR_FUEL_GATE",))
        rs, rv = value(candidate, "return_5d_pct") or 0, value(candidate, "relative_volume_completed_bar") or 1
        return self._result(candidate, True, 50 + rs * 2 + min(20, rv * 10), True,
                            families=tuple(candidate.signal_families))


class MomentumInflectionScanner(_CoreScanner):
    name = "MOMENTUM_INFLECTION"

    def evaluate(self, candidate: CandidateFeatureSnapshot, context: DiscoveryContext) -> ScannerResult:
        route_ok, reasons = route_gate(candidate, self.name)
        fields = ("return_5d_pct", "relative_volume_completed_bar", "return_20d_pct")
        unknown = tuple(field for field in fields if not known_field(candidate, field))
        if unknown or not route_ok:
            return self._result(candidate, False, 0, False, reasons or ("REQUIRED_FLOW_UNKNOWN",), unknown=unknown)
        r5, r20, rv = value(candidate, "return_5d_pct"), value(candidate, "return_20d_pct"), value(candidate, "relative_volume_completed_bar")
        hit = r5 > 0 and rv >= 1.2 and r20 < 40
        return self._result(candidate, hit, 45 + r5 * 3 + min(25, rv * 10), hit,
                            ("MOMENTUM_ACCELERATION" if hit else "NO_ACCELERATION",), ("FLOW",))


class ProfitabilityInflectionScanner(_CoreScanner):
    name = "PROFITABILITY_INFLECTION"

    def evaluate(self, candidate: CandidateFeatureSnapshot, context: DiscoveryContext) -> ScannerResult:
        required_fields = ("revenue_growth_acceleration_pp", "gross_margin_delta_pp", "operating_cash_flow_current")
        unknown = tuple(field for field in required_fields if not known_field(candidate, field))
        if unknown:
            return self._result(candidate, False, 0, False, ("FUNDAMENTAL_UNKNOWN",), unknown=unknown)
        margin_improved = value(candidate, "gross_margin_delta_pp") > 0 or (
            known_field(candidate, "operating_margin_delta_pp") and
            value(candidate, "operating_margin_delta_pp") > 0) or (
            known_field(candidate, "fcf_inflection") and value(candidate, "fcf_inflection") > 0)
        hit = value(candidate, "revenue_growth_acceleration_pp") > 0 and margin_improved and value(candidate, "operating_cash_flow_current") >= 0
        return self._result(candidate, hit, 70 if hit else 20, hit,
                            ("PROFITABILITY_INFLECTION" if hit else "NO_INFLECTION",), ("FUNDAMENTAL",))


class AIBottleneckExpansionScanner(_CoreScanner):
    name = "AI_BOTTLENECK_EXPANSION"

    def evaluate(self, candidate: CandidateFeatureSnapshot, context: DiscoveryContext) -> ScannerResult:
        numeric_fields = ("revenue_growth_acceleration_pp", "backlog_growth", "bookings_growth",
                          "arr_growth", "rpo_growth", "gross_margin_delta_pp")
        numeric_count = sum(known_field(candidate, field) and value(candidate, field) is not None
                            for field in numeric_fields)
        linkage = candidate.fields.get("customer_linkage")
        if numeric_count < 2 or linkage is None or not linkage.known or not bool(linkage.value):
            unknown = tuple(field for field in numeric_fields if not known_field(candidate, field))
            return self._result(candidate, False, 0, False, ("AI_NUMERIC_EVIDENCE_INSUFFICIENT",), unknown=unknown)
        if candidate.stage == "DISCOVERY_STAGE_3":
            return self._result(candidate, False, 0, False, ("STAGE3_NEW_ENTRY",))
        return self._result(candidate, True, 75, True, ("AI_BOTTLENECK_NUMERIC_AND_LINKAGE",), ("AI_BOTTLENECK",))
