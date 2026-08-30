"""Raw provider normalization into typed observations and evidence."""
from __future__ import annotations

import math
from typing import Any

from .adapters import ProviderError
from .models import MarketExecutionSnapshot, MarketContextSnapshot, PortfolioSnapshot, PositionSnapshot, RawArtifact, SecurityIdentity, TechnicalFeatures, canonical_hash, utc_now


def _required(payload: dict[str, Any], *keys: str) -> None:
    missing = [key for key in keys if payload.get(key) in (None, "")]
    if missing:
        raise ProviderError(f"raw provider artifact missing required fields: {missing}")


class SecurityNormalizer:
    def normalize(self, artifact: RawArtifact) -> list[SecurityIdentity]:
        rows = artifact.payload.get("candidates") or artifact.payload.get("securities") or []
        result: list[SecurityIdentity] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            _required(row, "security_id", "ticker", "issuer_name", "venue")
            result.append(SecurityIdentity(str(row["security_id"]), str(row["ticker"]), str(row["issuer_name"]), str(row["venue"]), str(row["cik"]) if row.get("cik") else None))
        return result


class MarketNormalizer:
    def normalize_context(self, artifact: RawArtifact) -> MarketContextSnapshot:
        payload = dict(artifact.payload)
        if any(payload.get(key) in (None, "", "UNKNOWN") for key in ("regime", "breadth", "volatility")):
            # Market providers may expose only raw candles.  Compute the
            # contextual labels locally; never trust caller/LLM conclusions.
            from .adapters import deterministic_market_context_from_payload
            derived = deterministic_market_context_from_payload(payload.get("source") or payload.get("candles") or payload)
            for key, value in derived.items():
                if key not in payload or payload.get(key) in (None, "", "UNKNOWN"):
                    payload[key] = value
        _required(payload, "regime", "breadth", "volatility")
        return MarketContextSnapshot(artifact.artifact_id, artifact.observed_at, str(payload.get("as_of") or artifact.observed_at), str(payload["regime"]), str(payload["breadth"]), str(payload["volatility"]), dict(payload.get("sector_rotation") or {}), (artifact.artifact_id,), artifact.payload_hash)

    def normalize_execution(self, artifact: RawArtifact, security_id: str) -> MarketExecutionSnapshot:
        payload = artifact.payload
        if not isinstance(payload, dict):
            raise ProviderError("market execution artifact payload must be an object")
        _required(payload, "current_price", "execution_stop", "account_equity", "core_input_complete")
        price = float(payload["current_price"]); stop = float(payload["execution_stop"]); equity = float(payload["account_equity"])
        if price <= 0 or stop <= 0 or equity <= 0 or stop >= price:
            raise ProviderError("invalid market execution arithmetic inputs")
        if payload["core_input_complete"] is not True:
            raise ProviderError("market execution core input is incomplete")
        currency = str(payload.get("currency") or "UNKNOWN").upper()
        return MarketExecutionSnapshot(artifact.artifact_id, security_id, price, stop, artifact.observed_at, True, float(payload["gap_risk"]) if payload.get("gap_risk") is not None else None, float(payload["event_risk_pct"]) if payload.get("event_risk_pct") is not None else None, equity, (artifact.artifact_id,), artifact.payload_hash, currency)

    def normalize_execution_context(
        self,
        artifact: RawArtifact,
        security_id: str,
        *,
        account_equity: float,
        execution_stop: float,
        gap_risk: float | None = None,
        event_risk_pct: float | None = None,
        source_artifact_ids: tuple[str, ...] = (),
    ) -> MarketExecutionSnapshot:
        """Merge provider market facts with Python-owned execution inputs.

        Toss (and similar quote providers) owns the current price only.  The
        portfolio snapshot owns account equity, while the caller must supply a
        separately validated Python execution stop.  This method deliberately
        ignores provider ``core_input_complete`` and ``account_equity`` claims.
        """
        payload = artifact.payload
        if not isinstance(payload, dict):
            raise ProviderError("market execution artifact payload must be an object")
        if artifact.subject_id not in (None, "", security_id):
            raise ProviderError("market execution artifact subject does not match security")
        current_price = payload.get("current_price")
        if current_price in (None, ""):
            results = (payload.get("prices") or {}).get("result") if isinstance(payload.get("prices"), dict) else None
            if isinstance(results, list) and results and isinstance(results[0], dict):
                current_price = results[0].get("lastPrice")
        try:
            price = float(current_price)
            stop = float(execution_stop)
            equity = float(account_equity)
            gap = float(gap_risk or 0.0)
            event = float(event_risk_pct or 0.0)
        except (TypeError, ValueError) as exc:
            raise ProviderError("execution context contains non-numeric Python inputs") from exc
        if any(not math.isfinite(value) for value in (price, stop, equity, gap, event)):
            raise ProviderError("execution context contains non-finite inputs")
        if price <= 0 or stop <= 0 or equity <= 0 or stop >= price or gap < 0 or event < 0:
            raise ProviderError("invalid merged market execution arithmetic inputs")
        currency = str(payload.get("currency") or "UNKNOWN").upper()
        merged_ids = tuple(dict.fromkeys((artifact.artifact_id, *source_artifact_ids)))
        merged_hash = canonical_hash({
            "market_artifact_id": artifact.artifact_id,
            "source_artifact_ids": merged_ids,
            "security_id": security_id,
            "current_price": price,
            "execution_stop": stop,
            "account_equity": equity,
            "gap_risk": gap,
            "event_risk_pct": event,
            "currency": currency,
        })
        return MarketExecutionSnapshot(
            artifact.artifact_id, security_id, price, stop, artifact.observed_at,
            True, gap, event, equity, merged_ids, merged_hash, currency,
        )


class PortfolioNormalizer:
    def normalize(self, artifact: RawArtifact) -> PortfolioSnapshot:
        payload = artifact.payload
        _required(payload, "as_of", "cash", "total_equity", "positions")
        positions: list[PositionSnapshot] = []
        for row in payload["positions"]:
            if not isinstance(row, dict):
                raise ProviderError("portfolio position must be an object")
            _required(row, "subject_id", "shares", "average_cost", "as_of")
            subject = str(row["subject_id"])
            positions.append(PositionSnapshot(subject, int(row["shares"]) > 0, int(row["shares"]), float(row["average_cost"]), str(row["as_of"]), str(row.get("snapshot_hash") or canonical_hash(row)), str(row.get("currency") or payload.get("currency") or "UNKNOWN").upper()))
        currency = str(payload.get("currency") or "UNKNOWN").upper()
        return PortfolioSnapshot(artifact.artifact_id, str(payload["as_of"]), float(payload["cash"]), float(payload["total_equity"]), tuple(positions), True, artifact.payload_hash, currency)


class TechnicalFeatureCalculator:
    """Deterministic feature calculation; no stage or action decision is made here."""

    version = "technical-features-v1"

    def calculate(self, security_id: str, prices: list[float], volumes: list[float] | None = None, as_of: str | None = None, source_artifact_ids: tuple[str, ...] = ()) -> TechnicalFeatures:
        if len(prices) < 2 or any(float(p) <= 0 for p in prices):
            raise ValueError("at least two positive prices are required")
        values = [float(p) for p in prices]
        features: dict[str, float | str | None] = {
            "last_price": values[-1],
            "return_1": values[-1] / values[-2] - 1.0,
            "return_window": values[-1] / values[0] - 1.0,
            "sma_window": sum(values) / len(values),
            "volatility_window": (max(values) - min(values)) / (sum(values) / len(values)),
            "volume_ratio": None,
        }
        if volumes and len(volumes) == len(values) and sum(volumes[:-1]) > 0:
            features["volume_ratio"] = float(volumes[-1]) / (sum(volumes[:-1]) / len(volumes[:-1]))
        payload_hash = canonical_hash({"security_id": security_id, "as_of": as_of or utc_now(), "features": features, "version": self.version})
        return TechnicalFeatures(security_id, as_of or utc_now(), features, self.version, tuple(source_artifact_ids), payload_hash)


def deterministic_stage_from_features(features: TechnicalFeatures) -> tuple[str, bool]:
    """Small versioned classifier used by StageGate; it never emits an action."""
    values = features.features
    return_window = float(values.get("return_window") or 0.0)
    last = float(values.get("last_price") or 0.0)
    sma = float(values.get("sma_window") or 0.0)
    if last <= 0 or sma <= 0:
        return "UNKNOWN", False
    if return_window >= 0.05 and last >= sma:
        return "STAGE_1", True
    if return_window >= -0.20:
        return "STAGE_0", True
    return "STAGE_2", False
