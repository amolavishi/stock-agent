from __future__ import annotations

from ..features import known_field, value
from ..schemas import CandidateFeatureSnapshot, DiscoveryContext, ScannerResult
from .core import _CoreScanner


class _FieldsScanner(_CoreScanner):
    required: tuple[str, ...] = ()
    family = "EVENT"

    def evaluate(self, candidate: CandidateFeatureSnapshot, context: DiscoveryContext) -> ScannerResult:
        unknown = tuple(field for field in self.required if not known_field(candidate, field))
        if unknown:
            return self._result(candidate, False, 0, False, ("REQUIRED_EVIDENCE_UNKNOWN",), unknown=unknown)
        hit, reason = self._hit(candidate)
        return self._result(candidate, hit, 75 if hit else 20, hit, (reason,), (self.family,))

    def _hit(self, candidate):
        return False, "RULE_NOT_IMPLEMENTED"


class TurnaroundScanner(_FieldsScanner):
    name, required, family = "TURNAROUND", ("revenue_growth_acceleration_pp", "gross_margin_delta_pp", "operating_cash_flow_current"), "FUNDAMENTAL"

    def _hit(self, candidate):
        hit = value(candidate, "revenue_growth_acceleration_pp") >= 0 and value(candidate, "gross_margin_delta_pp") > 0 and value(candidate, "operating_cash_flow_current") >= 0
        return hit, "TURNAROUND_REAL" if hit else "TURNAROUND_FAKE_OR_INCOMPLETE"


class PolicyDefenseEnergySecurityScanner(_FieldsScanner):
    name, required, family = "POLICY_DEFENSE_ENERGY_SECURITY", ("award_amount_usd", "trailing_revenue_usd", "funded_status"), "EVENT"

    def _hit(self, candidate):
        amount, revenue = value(candidate, "award_amount_usd"), value(candidate, "trailing_revenue_usd")
        hit = bool(value(candidate, "funded_status")) and revenue > 0 and amount / revenue >= .05
        return hit, "FUNDED_MATERIAL_AWARD" if hit else "UNFUNDED_OR_IMMATERIAL_AWARD"


class OfferingSecondaryRecoveryScanner(_FieldsScanner):
    name, required, family = "OFFERING_SECONDARY_RECOVERY", ("offering_type", "offer_price", "post_event_drawdown_pct", "post_event_low_defense", "capital_overhang_status"), "EVENT"

    def _hit(self, candidate):
        offering = str(value(candidate, "offering_type")).upper()
        hit = offering in {"SECONDARY", "BLOCK_TRADE", "RESALE_REGISTRATION"} and value(candidate, "post_event_low_defense") is True and str(value(candidate, "capital_overhang_status")).upper() == "CLEAR"
        return hit, "SECONDARY_DIGESTION" if hit else "OVERHANG_NOT_CLEARED"


class InsiderBuybackScanner(_FieldsScanner):
    name, required, family = "INSIDER_BUY_BUYBACK", ("insider_transaction_type",), "FLOW"

    def _hit(self, candidate):
        kind = str(value(candidate, "insider_transaction_type")).upper()
        hit = kind in {"OPEN_MARKET_PURCHASE", "ACTUAL_BUYBACK_EXECUTION", "ASR"}
        return hit, "VERIFIED_OPEN_MARKET_OR_EXECUTION" if hit else "AUTHORIZATION_OR_NON_PURCHASE"


class RefinancingDistressRemovalScanner(_FieldsScanner):
    name, required, family = "REFINANCING_DISTRESS_REMOVAL", ("maturity_extension", "liquidity_runway_months", "effective_interest_cost_delta", "dilution_status"), "EVENT"

    def _hit(self, candidate):
        hit = value(candidate, "maturity_extension") is True and value(candidate, "liquidity_runway_months") >= 6 and value(candidate, "effective_interest_cost_delta") <= 0 and str(value(candidate, "dilution_status")).upper() != "WORSENING"
        return hit, "DISTRESS_REMOVAL_VERIFIED" if hit else "DISTRESS_NOT_REMOVED"


class PostEarningsRevisionDriftScanner(_FieldsScanner):
    name, required, family = "POST_EARNINGS_REVISION_DRIFT", ("beat_verified", "price_response_not_extended", "relative_strength_positive"), "EARNINGS_EVENT"

    def _hit(self, candidate):
        hit = value(candidate, "beat_verified") is True and value(candidate, "price_response_not_extended") is True and value(candidate, "relative_strength_positive") is True
        return hit, "BEAT_WITH_UNEXTENDED_RESPONSE" if hit else "EARNINGS_DRIFT_NOT_VERIFIED"


class CustomerDiversificationScanner(_FieldsScanner):
    name, required, family = "CUSTOMER_DIVERSIFICATION", ("top_customer_pct_delta", "new_vertical_contribution"), "FUNDAMENTAL"

    def _hit(self, candidate):
        hit = value(candidate, "top_customer_pct_delta") < 0 and value(candidate, "new_vertical_contribution") > 0
        return hit, "CUSTOMER_CONCENTRATION_IMPROVING" if hit else "DIVERSIFICATION_NOT_NUMERIC"
