from __future__ import annotations

import argparse
import json

from stock_agent.command_parser import CommandInterpreter
from stock_agent.config import load_config
from stock_agent.discord_runtime import run_chairman_bot
from stock_agent.orchestrator import Orchestrator
from stock_agent.health import local_health
from stock_agent.sec import EdgarError
from stock_agent.validation import AnalysisIncompleteError, InvalidTickerError, UnsupportedMockTickerError


def main() -> int:
    parser = argparse.ArgumentParser(description="Discord natural-language US stock PAPER research agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    for name in ("analyze", "price", "report"):
        item = sub.add_parser(name)
        item.add_argument("ticker")
        if name == "analyze":
            item.add_argument("--live-edgar", action="store_true")
    natural = sub.add_parser("parse")
    natural.add_argument("text")
    sub.add_parser("portfolio")
    sub.add_parser("system")
    sub.add_parser("doctor")
    discover_market = sub.add_parser("discover-market")
    discover_market.add_argument("--intensity", choices=("MINIMUM", "NORMAL", "MAXIMUM"), default="MINIMUM")
    discover_market.add_argument("--shadow", action="store_true", default=False)
    discover_sector = sub.add_parser("discover-sector")
    discover_sector.add_argument("sector")
    discover_sector.add_argument("--intensity", choices=("MINIMUM", "NORMAL", "MAXIMUM"), default="MINIMUM")
    discover_sector.add_argument("--shadow", action="store_true", default=False)
    report_discovery = sub.add_parser("discovery-report")
    report_discovery.add_argument("run_id", nargs="?")
    sub.add_parser("discovery-replay").add_argument("run_id")
    sub.add_parser("discovery-bootstrap")
    sub.add_parser("discovery-refresh")
    sub.add_parser("discovery-health")
    sub.add_parser("discord")
    args = parser.parse_args()

    config = load_config()
    app = Orchestrator(config)
    app.init()
    if args.command == "init":
        print(f"Initialized PAPER database: {config['database_path']}")
        return 0
    if args.command == "parse":
        print(json.dumps(CommandInterpreter().parse(args.text).__dict__, ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze":
        try:
            result = app.analyze(args.ticker, "live" if args.live_edgar else None)
        except (EdgarError, InvalidTickerError, UnsupportedMockTickerError, AnalysisIncompleteError) as exc:
            print(f"분석 실패: {exc}")
            return 2
        decision = result["decision"]
        print(f"[{decision.decision}] {decision.ticker} confidence={decision.confidence}/100")
        print(f"run_id={decision.run_id}\nreport={result['report_path']}")
        return 0
    if args.command == "price":
        snapshot = app.market_provider.snapshot(args.ticker)
        print(json.dumps(snapshot.__dict__ | {
            "relative_volume": snapshot.relative_volume,
            "atr_pct": snapshot.atr_pct,
        }, ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        row = app.db.latest_decision(args.ticker)
        print("저장된 보고서가 없습니다." if not row else
              f"Latest run={row['run_id']} decision={row['decision']} confidence={row['confidence']}")
        return 0 if row else 1
    if args.command == "portfolio":
        rows = app.db.portfolio_positions()
        print("PAPER portfolio: no positions" if not rows else "\n".join(
            f"{row['ticker']}: {row['quantity']} @ ${row['average_price']:.2f}" for row in rows))
        return 0
    if args.command == "system":
        print(f"mode={config['mode']} market={config['market_data_provider']} "
              f"agent={config['agent_provider']} model={config['hermes_model']}")
        return 0
    if args.command == "doctor":
        result = local_health(config, app.db)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["healthy"] else 2
    if args.command in {"discover-market", "discover-sector"}:
        from stock_agent.schemas import UserRequest
        request = UserRequest(
            request_id=f"CLI_DISCOVERY_{args.command}", discord_message_id="CLI", discord_user_id="CLI",
            received_at="", original_text=args.command, intent=("DISCOVER_SECTOR" if args.command == "discover-sector" else "DISCOVER_MARKET"),
            tickers=[], analysis_intensity=args.intensity, intensity_explicit=True,
            requested_sector=getattr(args, "sector", ""), discovery_mode=("SECTOR" if args.command == "discover-sector" else "MARKET"),
            shadow=args.shadow)
        result = app.discover_request(request)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.status in {"COMPLETED", "COMPLETED_SHADOW_MARKET_ONLY",
                                      "COMPLETED_SHADOW_ENRICHED", "READY_FOR_DEEP_HANDOFF",
                                      "FINAL_NONE"} else 2
    if args.command == "discovery-report":
        payload = app.discovery.store.latest(args.run_id) if args.run_id else app.discovery.store.latest_any()
        print(json.dumps(payload, ensure_ascii=False, indent=2) if payload else "Discovery report not found")
        return 0 if payload else 1
    if args.command == "discovery-replay":
        payload = app.discovery.store.latest(args.run_id)
        if not payload:
            print("Discovery run not found")
            return 1
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"discovery-bootstrap", "discovery-refresh", "discovery-health"}:
        from stock_agent.discovery.health import bootstrap_health
        bootstrap = app.config.get("discovery", {}).get("bootstrap", {})
        cost = app.config.get("discovery", {}).get("cost", {})
        health = bootstrap_health(app.db, app.discovery.security_master, app.discovery.market_data,
                                  app.discovery.benchmark_provider,
                                  min_accepted=int(bootstrap.get("min_accepted", 1)),
                                  min_identity_coverage_pct=float(bootstrap.get("min_identity_coverage_pct", 95.0)),
                                  min_sector_coverage_pct=float(bootstrap.get("min_sector_coverage_pct", 90.0)),
                                  fundamental_provider=app.discovery.fundamental_provider,
                                  capital_preflight_provider=app.discovery.capital_preflight_provider,
                                  max_actual_llm_calls=int(cost.get("max_actual_llm_calls",
                                                                   cost.get("max_llm_calls_per_discovery", 0)) or 0))
        health["command"] = args.command
        if args.command != "discovery-health" and health["status"] == "BOOTSTRAP_REQUIRED":
            health["message"] = "A real SecurityMasterProvider and MarketDataProvider are required; no placeholder universe is used."
        print(json.dumps(health, ensure_ascii=False, indent=2))
        return 0 if health["status"] in {"MARKET_SCAN_READY", "ENRICHMENT_READY", "DEEP_HANDOFF_READY"} else 2
    if args.command == "discord":
        run_chairman_bot(config, app)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
