from __future__ import annotations

from typing import Any


def build_material_numeric_lineage(market: Any, trade_plan: Any,
                                   capital_structure: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"claim": "current_price", "value": market.current, "source": market.source,
         "as_of": market.provider_observed_at or market.observed_at, "method": "PROVIDER_QUOTE"},
        {"claim": "ma20", "value": market.ma20, "source": market.source,
         "as_of": market.bar_end_at or market.candle_as_of, "method": "20_COMPLETED_BARS_MEAN"},
        {"claim": "ma50", "value": market.ma50, "source": market.source,
         "as_of": market.bar_end_at or market.candle_as_of, "method": "50_COMPLETED_BARS_MEAN"},
        {"claim": "relative_volume", "value": market.relative_volume, "source": market.source,
         "as_of": market.bar_end_at or market.candle_as_of,
         "method": "LAST_COMPLETED_SESSION_OVER_PRIOR_20_SESSION_MEAN"},
        {"claim": "entry_price", "value": trade_plan.entry_price, "source": market.snapshot_id,
         "as_of": market.provider_observed_at or market.observed_at,
         "method": "PYTHON_TRADE_PLAN_HEURISTIC"},
        {"claim": "stop_price", "value": trade_plan.stop_price, "source": market.snapshot_id,
         "as_of": market.provider_observed_at or market.observed_at,
         "method": "PYTHON_TRADE_PLAN_HEURISTIC"},
    ]
    for claim, key in (("atm_capacity", "atm_authorized_capacity"),
                       ("atm_used", "atm_used_amount"),
                       ("atm_remaining", "atm_remaining_amount")):
        metric = capital_structure.get(key)
        if not isinstance(metric, dict) or metric.get("value") is None:
            continue
        rows.append({"claim": claim, "value": metric.get("value"),
                     "source": metric.get("source_accession"), "as_of": metric.get("as_of"),
                     "method": metric.get("calculation_method")})
    validate_material_numeric_lineage(rows)
    return rows


def validate_material_numeric_lineage(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        if row.get("value") is None:
            raise ValueError(f"material numeric claim has no value: {row.get('claim')}")
        for field in ("source", "as_of", "method"):
            if not row.get(field):
                raise ValueError(f"material numeric claim lacks {field}: {row.get('claim')}")
