from __future__ import annotations

from .schemas import CompanyState, CriticReview, EvidenceItem, MarketSnapshot, ResearchAnalysis


class MockResearchAgent:
    """Deterministic placeholder; never presented as an LLM analysis."""

    def run(self, state: CompanyState, market: MarketSnapshot, evidence: list[EvidenceItem]) -> ResearchAnalysis:
        signal = max(0, min(100, round(55 + market.return_20d_pct + market.relative_volume * 5)))
        catalyst = 55 if evidence else 30
        expectation = max(20, min(95, round(70 - max(market.current - market.ma20, 0) / market.current * 100)))
        elasticity = max(0, min(100, round(55 + market.relative_volume * 15)))
        entry = max(0, min(100, round(50 - market.atr_pct + (10 if market.current <= market.ma20 else 0))))
        fit = max(0, min(100, round((signal + catalyst + expectation) / 3)))
        proposed = "CONDITIONAL_BUY" if fit >= 72 else "WAIT"
        confidence = max(40, min(75, round((signal + catalyst + fit) / 3)))
        claims = []
        if evidence:
            labels = ("사업 근거", "보조 근거", "촉매 근거", "위험 근거",
                      "시장 관심 근거", "검증 근거", "추적 근거")
            for index, label in enumerate(labels):
                item = evidence[index % len(evidence)]
                claims.append({"claim": f"[MOCK] {label}가 존재한다",
                               "evidence_id": item.evidence_id,
                               "evidence_ids": [item.evidence_id], "confidence": 0.50,
                               "materiality": "MATERIAL", "domain": "SEC_FILING",
                               "claim_type": "FACT", "minimum_evidence_grade": "UNCLASSIFIED"})
        return ResearchAnalysis(ticker=state.ticker, market_regime="MOCK risk-on / high volatility",
            sector=market.sector_name, signal_strength=signal, catalyst_quality=catalyst,
            expectation_gap=expectation, surge_elasticity=elasticity, entry_readiness=entry,
            capital_structure_risk=state.dilution_risk, strategy_fit=fit,
            bull_case=[f"[MOCK] {x}" for x in state.catalysts[:3]],
            bear_case=[f"[MOCK] {x}" for x in state.known_risks[:3]],
            suggested_decision=proposed, confidence=confidence,
            evidence_ids=[e.evidence_id for e in evidence], claims=claims,
            consensus_ready=True)


class MockCriticAgent:
    """Deterministic placeholder with at least three explicit failure scenarios."""

    def run(self, research: ResearchAnalysis, state: CompanyState, market: MarketSnapshot) -> CriticReview:
        flaws = []
        if research.expectation_gap < 55:
            flaws.append({"severity": "HIGH", "issue": "현재 가격이 기대를 상당 부분 반영했을 가능성"})
        if market.atr_pct >= 8:
            flaws.append({"severity": "MEDIUM", "issue": "변동성이 높아 진입 시점 리스크가 큼"})
        if state.atm_active:
            flaws.append({"severity": "HIGH", "issue": "ATM 또는 희석 리스크를 보수적으로 반영해야 함"})
        scenarios = [
            {"scenario": "[MOCK] 섹터 모멘텀 둔화", "probability": 0.25, "impact": "HIGH"},
            {"scenario": "[MOCK] 사업 진척이 매출로 전환되지 않음", "probability": 0.30, "impact": "HIGH"},
            {"scenario": "[MOCK] 추가 자금조달 또는 희석", "probability": 0.20, "impact": "MEDIUM"},
        ]
        challenge = len(flaws) >= 2 or research.expectation_gap < 60
        return CriticReview(ticker=research.ticker, research_decision=research.suggested_decision,
            verdict="CHALLENGE" if challenge else "PASS", critical_flaws=flaws,
            failure_scenarios=scenarios, evidence_conflicts=[],
            critic_decision="WAIT" if challenge else research.suggested_decision,
            confidence=65 if challenge else 60, consensus_ready=True)
