from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from stock_agent.tool_service import StockAgentToolService


mcp = FastMCP(
    "stock-agent",
    instructions=("Deterministic PAPER research tools only. No order execution, shell, arbitrary SQL, "
                  "file deletion, or secret access is exposed."),
    log_level="ERROR",
)
service = StockAgentToolService()


def _invoke(name: str, arguments: dict[str, Any], operation) -> dict[str, Any]:
    result = operation()
    service.audit_tool_invocation(name, arguments, result)
    return result


@mcp.tool(structured_output=True)
def market_get_snapshot(ticker: str) -> dict[str, Any]:
    """Get a real Toss market snapshot for one validated US ticker."""
    return _invoke("market_get_snapshot", locals(), lambda: service.market_get_snapshot(ticker))


@mcp.tool(structured_output=True)
def market_get_regime() -> dict[str, Any]:
    """Compute market regime from QQQ, IWM, and SOXX Toss snapshots."""
    return _invoke("market_get_regime", locals(), service.market_get_regime)


@mcp.tool(structured_output=True)
def market_get_benchmark_snapshots() -> dict[str, Any]:
    """Get benchmark snapshots used for market regime and PAPER alpha."""
    return _invoke("market_get_benchmark_snapshots", locals(), service.market_get_benchmark_snapshots)


@mcp.tool(structured_output=True)
def sec_get_filing_evidence(ticker: str, as_of: str = "", limit: int = 8) -> dict[str, Any]:
    """Get SEC filing evidence through metadata, download, cache, extraction and classification."""
    return _invoke("sec_get_filing_evidence", locals(), lambda: service.sec_get_filing_evidence(ticker, as_of, limit))


@mcp.tool(structured_output=True)
def sec_get_company_facts(ticker: str, as_of: str = "") -> dict[str, Any]:
    """Get structured SEC CompanyFacts/XBRL values."""
    return _invoke("sec_get_company_facts", locals(), lambda: service.sec_get_company_facts(ticker, as_of))


@mcp.tool(structured_output=True)
def sec_request_additional_evidence(ticker: str, request: dict[str, Any]) -> dict[str, Any]:
    """Perform one bounded additional SEC evidence request."""
    return _invoke("sec_request_additional_evidence", locals(), lambda: service.sec_request_additional_evidence(ticker, request))


@mcp.tool(structured_output=True)
def state_get_company_state(ticker: str) -> dict[str, Any]:
    """Read persisted company state from SQLite."""
    return _invoke("state_get_company_state", locals(), lambda: service.state_get_company_state(ticker))


@mcp.tool(structured_output=True)
def state_get_latest_thesis(ticker: str) -> dict[str, Any]:
    """Read the latest persisted Research output."""
    return _invoke("state_get_latest_thesis", locals(), lambda: service.state_get_latest_thesis(ticker))


@mcp.tool(structured_output=True)
def state_get_decision_history(ticker: str, limit: int = 10) -> dict[str, Any]:
    """Read bounded final-decision history."""
    return _invoke("state_get_decision_history", locals(), lambda: service.state_get_decision_history(ticker, limit))


@mcp.tool(structured_output=True)
def state_get_portfolio_state() -> dict[str, Any]:
    """Read PAPER portfolio positions."""
    return _invoke("state_get_portfolio_state", locals(), service.state_get_portfolio_state)


@mcp.tool(structured_output=True)
def risk_evaluate(research: dict[str, Any], critic: dict[str, Any], company_state: dict[str, Any],
                  market_snapshot: dict[str, Any], trade_plan: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic Python hard-risk rules."""
    return _invoke("risk_evaluate", locals(), lambda: service.risk_evaluate(research, critic, company_state, market_snapshot, trade_plan))


@mcp.tool(structured_output=True)
def sizing_calculate(trade_plan: dict[str, Any], equity_usd: float, cash_usd: float) -> dict[str, Any]:
    """Calculate PAPER quantity with deterministic loss and exposure caps."""
    return _invoke("sizing_calculate", locals(), lambda: service.sizing_calculate(trade_plan, equity_usd, cash_usd))


@mcp.tool(structured_output=True)
def guard_validate_claims(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate every core claim against real evidence IDs."""
    return _invoke("guard_validate_claims", locals(), lambda: service.guard_validate_claims(claims, evidence))


@mcp.tool(structured_output=True)
def guard_validate_trade_plan(trade_plan: dict[str, Any]) -> dict[str, Any]:
    """Validate the single shared TradePlan."""
    return _invoke("guard_validate_trade_plan", locals(), lambda: service.guard_validate_trade_plan(trade_plan))


@mcp.tool(structured_output=True)
def guard_validate_final(chairman_output: dict[str, Any], risk_output: dict[str, Any],
                         claims_valid: bool, trade_plan_valid: bool) -> dict[str, Any]:
    """Enforce Decision enum and Python risk override after Chairman."""
    return _invoke("guard_validate_final", locals(), lambda: service.guard_validate_final(chairman_output, risk_output, claims_valid, trade_plan_valid))


@mcp.tool(structured_output=True)
def audit_start_run(ticker: str, request_id: str = "") -> dict[str, Any]:
    """Start one idempotent PAPER analysis run."""
    return _invoke("audit_start_run", locals(), lambda: service.audit_start_run(ticker, request_id))


@mcp.tool(structured_output=True)
def audit_save_stage_output(run_id: str, ticker: str, stage: str,
                            payload: dict[str, Any]) -> dict[str, Any]:
    """Save a validated Research, Critic, Risk, or Chairman stage output."""
    return _invoke("audit_save_stage_output", locals(), lambda: service.audit_save_stage_output(run_id, ticker, stage, payload))


@mcp.tool(structured_output=True)
def audit_complete_run(run_id: str, ticker: str, final_decision: str,
                       confidence: int, manifest: dict[str, Any]) -> dict[str, Any]:
    """Complete a run and persist its RunManifest."""
    return _invoke("audit_complete_run", locals(), lambda: service.audit_complete_run(run_id, ticker, final_decision, confidence, manifest))


@mcp.tool(structured_output=True)
def audit_fail_run(run_id: str, run_status: str, error: str) -> dict[str, Any]:
    """Fail a run with one allowed non-investment RunStatus."""
    return _invoke("audit_fail_run", locals(), lambda: service.audit_fail_run(run_id, run_status, error))


@mcp.tool(structured_output=True)
def paper_record_prediction(decision: dict[str, Any], position_size: dict[str, Any]) -> dict[str, Any]:
    """Record an eligible BUY/CONDITIONAL_BUY prediction in PAPER only."""
    return _invoke("paper_record_prediction", locals(), lambda: service.paper_record_prediction(decision, position_size))


@mcp.tool(structured_output=True)
def paper_update_performance(run_id: str, entry_price: float, closes: list[float],
                             highs: list[float], lows: list[float], stop_price: float,
                             target_1: float, target_2: float,
                             benchmark_returns: dict[str, float] | None = None) -> dict[str, Any]:
    """Persist deterministic PAPER returns, alpha, MFE/MAE, stop and target hits."""
    return _invoke("paper_update_performance", locals(), lambda: service.paper_update_performance(
        run_id, entry_price, closes, highs, lows, stop_price, target_1, target_2,
        benchmark_returns))


@mcp.tool(structured_output=True)
def discord_publish_research(content: str) -> dict[str, Any]:
    """Publish Research output to the configured Debate channel."""
    return _invoke("discord_publish_research", locals(), lambda: service.discord_publish("research", content))


@mcp.tool(structured_output=True)
def discord_publish_critic(content: str) -> dict[str, Any]:
    """Publish Critic output to the configured Debate channel."""
    return _invoke("discord_publish_critic", locals(), lambda: service.discord_publish("critic", content))


@mcp.tool(structured_output=True)
def discord_publish_chairman(content: str) -> dict[str, Any]:
    """Publish the final Chairman report to the configured Report channel."""
    return _invoke("discord_publish_chairman", locals(), lambda: service.discord_publish("chairman", content))


@mcp.tool(structured_output=True)
def discord_publish_error(content: str) -> dict[str, Any]:
    """Publish a structured run error to the configured Report channel."""
    return _invoke("discord_publish_error", locals(), lambda: service.discord_publish("error", content))


if __name__ == "__main__":
    mcp.run(transport="stdio")
