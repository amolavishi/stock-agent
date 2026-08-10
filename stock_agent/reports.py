from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .knowledge import ObsidianKnowledgeManager
from .lineage import build_material_numeric_lineage
from .schemas import (CompanyState, CriticReview, EvidenceItem, InvestmentDecision,
                      MarketSnapshot, ResearchAnalysis, RiskResult, CertificationResult)


def render_uncertified_report(run_id: str, ticker: str,
                              certification: CertificationResult,
                              request: dict[str, Any] | None = None,
                              *, market: MarketSnapshot | None = None,
                              debate_state: Any = None,
                              evidence: list[EvidenceItem] | None = None,
                              usage: dict[str, Any] | None = None) -> str:
    """Render diagnostics without presenting an investment conclusion as certified."""
    request = request or {}
    evidence = evidence or []
    usage = usage or {}
    debate = getattr(debate_state, "to_dict", lambda: debate_state or {})()
    evidence_rows = "\n".join(
        f"| `{item.evidence_id}` | {item.document_type} | {item.evidence_grade} | "
        f"{item.data_quality} |" for item in evidence
    ) or "| N/A | N/A | N/A | N/A |"
    market_text = "N/A"
    if market is not None:
        market_text = (f"{market.source}; observed={market.observed_at}; "
                       f"quality={market.data_quality}; mock={str(market.is_mock).lower()}")
    reasons = "\n".join(f"- `{reason}`" for reason in certification.reason_codes) or "- `UNSPECIFIED_BLOCKER`"
    warnings = "\n".join(f"- `{warning}`" for warning in certification.important_data_warnings) or "- 없음"
    return f"""# Analysis Certification Result

> 이 문서는 투자판단 보고서가 아닙니다. 데이터·근거·토론 무결성 조건을 충족하지 못해 결론이 차단된 진단 기록입니다.

- Ticker: `{ticker}`
- Run ID: `{run_id}`
- Action: **{certification.action}**
- Decision Confidence: **N/A**
- Execution Status: `{certification.execution_status}`
- Analysis Status: `{certification.analysis_status}`
- Certification Status: `{certification.certification_status}`
- Side Effect Status: `{certification.side_effect_status}`
- Trade Plan: **{certification.trade_plan_status}**
- Position Sizing: **{certification.position_sizing_status}**

## Blocking Reasons

{reasons}

## Important Data Warnings

{warnings}

## Request

> {request.get('original_text', '')}

- Intent: `{request.get('intent', 'ANALYZE')}`
- Analysis Intensity: `{request.get('analysis_intensity', 'NORMAL')}`

## Market Data Diagnostics

- {market_text}

## Debate Diagnostics

- Status: `{debate.get('status', 'UNKNOWN')}`
- Rounds: `{debate.get('round_no', 0)}`
- Critical Open Issues: `{debate.get('critical_open_issue_count', 0)}`
- Deadlock Reason: `{debate.get('deadlock_reason', '')}`

## Evidence Processing Diagnostics

| Evidence ID | Document | Grade | Data Quality |
|---|---|---|---|
{evidence_rows}

## Cost Diagnostics

- LLM Calls: `{usage.get('llm_calls', 0)}`
- Input / Output Tokens: `{usage.get('input_tokens', 0)}` / `{usage.get('output_tokens', 0)}`
- Estimated Cost: `${usage.get('estimated_cost_usd', 0):.6f}`

No TradePlan or PositionSizing object was generated for this uncertified run.
"""


def _lines(values: list[Any], empty: str = "없음") -> str:
    return "\n".join(f"- {value}" for value in values) or f"- {empty}"


def _inline(values: list[Any], empty: str = "없음") -> str:
    rendered = [value if isinstance(value, str) else str(value) for value in values]
    return ", ".join(rendered) or empty


def render_report(run_id: str, market: MarketSnapshot, state: CompanyState,
                  evidence: list[EvidenceItem], research: ResearchAnalysis,
                  critic: CriticReview, risk: RiskResult, decision: InvestmentDecision,
                  request: dict[str, Any] | None = None, market_regime: str = "UNKNOWN",
                  chairman: dict[str, Any] | None = None, position_size: Any = None,
                  run_status: str = "SUCCESS", debate_state: Any = None,
                  usage: dict[str, Any] | None = None,
                  fresh_delta: dict[str, Any] | None = None,
                  capital_structure: dict[str, Any] | None = None,
                  certification: CertificationResult | None = None) -> str:
    request = request or {}
    chairman = chairman or {}
    is_mock = market.is_mock or any(item.is_mock for item in evidence)
    evidence_rows = "\n".join(
        f"| `{e.evidence_id}` | {e.document_type} | {e.published_at} | {e.evidence_grade} | "
        f"{e.summary.replace('|', '/')} | [원문]({e.source_url}) |" for e in evidence)
    claims = "\n".join(
        f"- {item.get('claim', '')} → `{', '.join(item.get('evidence_ids') or [item.get('evidence_id', '')])}`"
        for item in research.claims) or "- 없음"
    failures = [item.get("scenario", str(item)) for item in critic.failure_scenarios]
    plan = decision.trade_plan
    sizing = getattr(position_size, "__dict__", position_size) or {}
    debate = getattr(debate_state, "to_dict", lambda: debate_state or {})()
    usage = usage or {}
    fresh_delta = fresh_delta or {}
    capital_structure = capital_structure or {}
    if certification is not None and certification.certification_status != "CERTIFIED":
        raise ValueError("normal investment report requires CERTIFIED status")
    numeric_lineage = build_material_numeric_lineage(market, decision.trade_plan,
                                                      capital_structure)
    lineage_rows = "\n".join(
        f"| {row['claim']} | {row['value']} | `{row['source']}` | {row['as_of']} | "
        f"{row['method']} |" for row in numeric_lineage)
    score_names = (("Signal Strength", "signal_strength"),
        ("Catalyst Quality", "catalyst_quality"), ("Expectation Gap", "expectation_gap"),
        ("Surge Elasticity", "surge_elasticity"), ("Entry Readiness", "entry_readiness"),
        ("Capital Structure Risk", "capital_structure_risk"), ("Strategy Fit", "strategy_fit"))
    score_rows = []
    for label, key in score_names:
        detail = research.score_details.get(key, {})
        score_rows.append(f"| {label} | {getattr(research, key)} | "
            f"{detail.get('coverage', 'N/A')} | {detail.get('rubric_version', 'scorecard_v2.1')} | "
            f"{', '.join(detail.get('evidence_ids', research.evidence_ids)) or 'NONE'} | "
            f"{', '.join(detail.get('missing_inputs', [])) or 'NONE'} |")
    evidence_grade_count = sum(item.evidence_grade in {"A", "B"} for item in evidence)
    evidence_confidence = round(evidence_grade_count / max(len(evidence), 1) * 100)
    data_confidence = 100 if market.data_quality == "OK" else 0
    certification_status = certification.certification_status if certification else "CERTIFIED"
    sizing_section = ""
    if position_size is not None and decision.decision in {"BUY", "CONDITIONAL_BUY"}:
        sizing_section = f"""# Position Sizing

- Quantity: `{sizing.get('quantity', 0)}` shares (PAPER)
- Notional: `${sizing.get('notional_usd', 0):,.2f}`
- Portfolio Weight: `{sizing.get('portfolio_weight_pct', 0):.2f}%`
- Initial Capital at Risk: `${sizing.get('initial_capital_at_risk_usd', 0):,.2f}`
- Pending Committed Risk: `${sizing.get('pending_committed_risk_usd', 0):,.2f}`
- Gross Exposure after Entry: `${sizing.get('gross_exposure_usd', 0):,.2f}`
- Risk Rule: `{sizing.get('risk_rule_version', 'portfolio_heat_v1')}`
- Limiting Rule: `{sizing.get('limiting_rule', 'NOT_APPLICABLE')}`
"""
    warning = ("> ⚠️ 이 문서는 실제 데이터에 근거한 PAPER 리서치이며 실제 주문이나 투자 권유가 아닙니다."
               if not is_mock else "> ⚠ MOCK DATA — 실제 투자판단 금지")
    return f"""# Executive Decision

{warning}

- Decision: **{decision.decision}**
- Data Confidence: **{data_confidence}/100**
- Evidence Confidence: **{evidence_confidence}/100**
- Thesis Confidence: **{research.confidence}/100**
- Decision Confidence: **{decision.confidence}/100**
- Time Horizon: `{request.get('time_horizon', '1-2M')}`
- Analysis Intensity: `{request.get('analysis_intensity', 'NORMAL')}`
- Run Status: `{run_status}`
- Execution Status: `{certification.execution_status if certification else 'SUCCESS'}`
- Analysis Status: `{certification.analysis_status if certification else 'COMPLETED'}`
- Certification Status: `{certification_status}`
- Side Effect Status: `{certification.side_effect_status if certification else 'NOT_AUTHORIZED'}`
- Run ID: `{run_id}`

# User Request

> {request.get('original_text', '직접 분석 요청')}

- Intent: `{request.get('intent', 'ANALYZE')}`
- Focus: `{', '.join(request.get('focus', [])) or '없음'}`

# Market Snapshot

- Ticker / Current: `{market.ticker}` / `${market.current:.2f}`
- 1D / 5D / 20D: `{market.change_1d_pct:+.2f}%` / `{market.return_5d_pct:+.2f}%` / `{market.return_20d_pct:+.2f}%`
- Relative Volume / ATR: `{market.relative_volume:.2f}x` / `{market.atr_pct:.2f}%`
- Stage: `{market.stage}`
- Source / Observed At: `{market.source}` / `{market.observed_at}`
- Data Quality / Mock: `{market.data_quality}` / `{str(market.is_mock).lower()}`
- Transport / Quote / Candle: `{market.transport_status}` / `{market.quote_freshness}` / `{market.candle_freshness}`
- Session / Bar / Volume / Indicators: `{market.market_session}` / `{market.bar_completeness}` / `{market.volume_validity}` / `{market.indicator_readiness}`

# Market Regime

`{market_regime}`

# Research Mode / Fresh Delta

- Mode: `{fresh_delta.get('research_mode', 'FULL_RESEARCH')}`
- Prior Run: `{fresh_delta.get('prior_run_id', 'NONE') or 'NONE'}`
- New Evidence: `{', '.join(fresh_delta.get('new_evidence_ids', [])) or '없음'}`
- Market Changes: `{fresh_delta.get('market_changes', {})}`

# Research Thesis

## Bull Case

{_lines(research.bull_case)}

## Bear Case

{_lines(research.bear_case)}

## Scorecard

| Metric | Score | Coverage | Rubric | Evidence IDs | Missing Inputs |
|---|---:|---:|---|---|---|
{chr(10).join(score_rows)}

# Critic Review

- Verdict: `{critic.verdict}`
- Critic Decision: `{critic.critic_decision}`
- Evidence Conflicts: `{_inline(critic.evidence_conflicts)}`

# Debate Resolution

{_lines(chairman.get('debate_resolution', []))}

- Status: `{debate.get('status', 'UNKNOWN')}`
- Rounds: `{debate.get('round_no', 0)}/{debate.get('max_rounds', 0)}`
- Stress Test: `{'PASSED' if debate.get('stress_test_completed') else 'NOT_RUN'}`
- Open / Critical Open Issues: `{debate.get('open_issue_count', 0)}` / `{debate.get('critical_open_issue_count', 0)}`
- Deadlock Reason: `{debate.get('deadlock_reason', '') or '없음'}`

## Thesis Change Log

{_lines(debate.get('thesis_change_log', []))}

## Minority Opinion

{_lines(chairman.get('minority_opinion', []))}

# Evidence Table

| Evidence ID | Document | Published | Grade | Summary | Source |
|---|---|---|---|---|---|
{evidence_rows}

## Claim–Evidence Links

{claims}

# Risk Engine

- Hard Filter: `{'PASS' if risk.hard_filter_pass else 'BLOCK'}`
- Risk Decision: `{risk.risk_decision}`
- Rule Version: `{risk.rule_version}`
- Warnings: `{', '.join(risk.warnings) or '없음'}`
- Failures: `{', '.join(risk.failures) or '없음'}`

# Capital Structure

- Shares Outstanding: `{capital_structure.get('shares_outstanding', 'UNKNOWN')}`
- ATM Capacity: `{capital_structure.get('atm_capacity', 'UNKNOWN')}`
- Warrants / Convertibles: `{capital_structure.get('warrants', 'UNKNOWN')}` / `{capital_structure.get('convertibles', 'UNKNOWN')}`
- Cash / Burn / Runway: `{capital_structure.get('cash', 'UNKNOWN')}` / `{capital_structure.get('cash_burn', 'UNKNOWN')}` / `{capital_structure.get('runway_months', 'UNKNOWN')}`
- Unknown Fields: `{', '.join(capital_structure.get('unknown_fields', [])) or '없음'}`

# TradePlan

- Entry: `${plan.entry_price:.2f}`
- Preferred Range: `${plan.preferred_price_min:.2f}–${plan.preferred_price_max:.2f}`
- Stop: `${plan.stop_price:.2f}`
- Target 1 / 2: `${plan.target_1:.2f}` / `${plan.target_2:.2f}`
- Reward/Risk: `{plan.reward_risk}`

{sizing_section}

# Scenario Analysis

{_lines(decision.top_reasons)}

# Failure Scenarios

{_lines(failures)}

- Scenario Probabilities: `WITHHELD_UNLESS_SEPARATELY_CALIBRATED`

# Material Numeric Data Lineage

| Claim | Value | Source | As Of | Method |
|---|---:|---|---|---|
{lineage_rows}

# Invalidation Conditions

{_lines(chairman.get('invalidation_conditions', []))}

# Final Decision

- Proposed by Chairman: `{chairman.get('decision', decision.decision)}`
- Final after Python Guard: `{decision.decision}`
- Top Risks: `{', '.join(decision.top_risks) or '없음'}`

# Run Metadata

- Run ID: `{run_id}`
- Requested At: `{request.get('received_at', '')}`
- As Of: `{datetime.now(timezone.utc).isoformat()}`
- Ticker: `{market.ticker}`
- Intent: `{request.get('intent', 'ANALYZE')}`
- Model: `{research.provider}/{research.model}`
- Research Prompt: `{research.prompt_version}`
- Critic Prompt: `{critic.prompt_version}`
- Chairman Prompt: `chairman_v001`
- Risk Rule: `{risk.rule_version}`
- Data Provider: `{market.source}`
- Run Status: `{run_status}`
- Price As Of: `{market.observed_at}`
- Candle As Of: `{market.candle_as_of}`
- SEC Latest Filed At: `{max((e.filed_at or e.published_at for e in evidence), default='UNKNOWN')}`
- CompanyFacts As Of: `{state.companyfacts_as_of or 'UNKNOWN'}`
- Market Regime As Of: `{debate.get('market_regime_as_of', 'see context')}`

# API Cost

- LLM Calls: `{usage.get('llm_calls', 0)}`
- Input / Output Tokens: `{usage.get('input_tokens', 0)}` / `{usage.get('output_tokens', 0)}`
- Reasoning Tokens: `{usage.get('reasoning_tokens', 0)}`
- Cache Tokens: `{usage.get('cached_tokens', 0)}`
- Estimated Cost: `${usage.get('estimated_cost_usd', 0):.6f}`
- Total LLM Latency: `{usage.get('latency_ms', 0)} ms`
"""


def write_report(vault_path: str, report: str, ticker: str, run_id: str) -> Path:
    return ObsidianKnowledgeManager(vault_path).write_report(ticker, run_id, report)


def write_run_report(report_dir: str, report: str, ticker_label: str, run_id: str) -> Path:
    root = Path(report_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    safe_label = "_vs_".join(part for part in ticker_label.split("_vs_") if part.isalnum())
    path = (root / f"{datetime.now(timezone.utc):%Y-%m-%d}_{safe_label}_Run-{run_id}.md").resolve()
    if root not in path.parents:
        raise ValueError("report path escaped configured directory")
    path.write_text(report, encoding="utf-8")
    return path
