from __future__ import annotations

from typing import Any

from .schemas import EvidenceItem, MarketSnapshot


def build_fresh_delta(prior: dict[str, Any] | None, market: MarketSnapshot,
                      evidence: list[EvidenceItem], market_regime: dict[str, Any],
                      company_facts_as_of: str = "") -> dict[str, Any]:
    trusted = bool(prior and (prior.get("certification") or {}).get(
        "certification_status") == "CERTIFIED")
    if not trusted:
        return {"research_mode": "FULL_RESEARCH", "first_touch": True,
                "new_evidence_ids": [item.evidence_id for item in evidence],
                "market_changes": {}, "prior_run_id": "",
                "diagnostic_prior_run_id": ((prior or {}).get("run") or {}).get("run_id", ""),
                "companyfacts_as_of": company_facts_as_of,
                "latest_sec_accession": max((item.accession for item in evidence if item.accession),
                                            default=""),
                "latest_sec_filed_at": max((item.filed_at or item.published_at
                                            for item in evidence), default=""),
                "market_observed_at": market.provider_observed_at or market.observed_at}
    prior_market = prior.get("market") or {}
    prior_manifest = prior.get("manifest") or {}
    prior_ids = set(prior_manifest.get("evidence_ids") or [])
    current_ids = [item.evidence_id for item in evidence]

    def change(name: str, current: float | None) -> dict[str, Any] | None:
        before = prior_market.get(name)
        if before is None or current is None:
            return None
        try:
            return {"before": float(before), "after": float(current),
                    "delta": round(float(current) - float(before), 4)}
        except (TypeError, ValueError):
            return None

    changes = {name: value for name, value in {
        "current": change("current", market.current),
        "return_20d_pct": change("return_20d_pct", market.return_20d_pct),
        "relative_volume": change("relative_volume", market.relative_volume),
        "atr_pct": change("atr_pct", market.atr_pct),
        "ma20": change("ma20", market.ma20), "ma50": change("ma50", market.ma50),
    }.items() if value is not None}
    return {
        "research_mode": "DELTA_RESEARCH", "first_touch": False,
        "prior_run_id": prior["run"]["run_id"], "prior_finished_at": prior["run"].get("finished_at"),
        "prior_decision": (prior.get("decision") or {}).get("decision"),
        "prior_confidence": (prior.get("decision") or {}).get("confidence"),
        "new_evidence_ids": [value for value in current_ids if value not in prior_ids],
        "unchanged_evidence_ids": [value for value in current_ids if value in prior_ids],
        "market_changes": changes, "market_regime": market_regime,
        "companyfacts_as_of": company_facts_as_of,
        "latest_sec_accession": max((item.accession for item in evidence if item.accession),
                                    default=""),
        "latest_sec_filed_at": max((item.filed_at or item.published_at for item in evidence),
                                   default=""),
        "market_observed_at": market.provider_observed_at or market.observed_at,
    }
