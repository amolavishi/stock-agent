from __future__ import annotations

from dataclasses import replace
from typing import Any

from .features import known_field, value


class EvidencePreflight:
    """Blocks deep analysis when primary evidence is absent or materially unknown."""

    def evaluate(self, candidate) -> dict[str, Any]:
        if candidate.security.ticker == "":
            return {"status": "BLOCKED", "reason_codes": ["IDENTITY_CONFLICT"]}
        primary = candidate.fields.get("primary_financial_evidence")
        if primary is None or not primary.known:
            return {"status": "BLOCKED", "reason_codes": ["NO_PRIMARY_FINANCIAL_EVIDENCE"]}
        if primary.value is not True:
            return {"status": "BLOCKED", "reason_codes": ["NO_PRIMARY_FINANCIAL_EVIDENCE"]}
        overhang = candidate.fields.get("capital_overhang_status")
        if overhang is None or not overhang.known:
            return {"status": "BLOCKED", "reason_codes": ["CAPITAL_STRUCTURE_UNKNOWN_CRITICAL"]}
        if str(overhang.value).upper() in {"HIGH_RISK", "UNKNOWN"}:
            return {"status": "BLOCKED", "reason_codes": ["CAPITAL_STRUCTURE_UNKNOWN_CRITICAL"]}
        return {"status": "READY", "reason_codes": []}


def make_child_request(request, ticker: str):
    return replace(request, request_id=f"{request.request_id}:DISCOVERY:{ticker}",
                   discord_message_id=f"{request.discord_message_id}:DISCOVERY:{ticker}",
                   intent="ANALYZE", tickers=[ticker], paper_action_enabled=False,
                   need_debate=True, need_report=True, shadow=False)
