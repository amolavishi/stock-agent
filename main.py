from __future__ import annotations

import argparse
import copy
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
    degraded = sub.add_parser("discovery-degraded-shadow")
    degraded.add_argument("--intensity", choices=("MINIMUM", "NORMAL", "MAXIMUM"), default="MINIMUM")
    sub.add_parser("discovery-schema-init")
    sub.add_parser("discord")
    args = parser.parse_args()

    config = load_config()
    # Security Master lifecycle commands are intentionally independent from
    # Orchestrator/PAPER initialization.  Bootstrap must work when Toss is not
    # configured, and health must not create or replace an enrichment snapshot.
    if args.command in {"discovery-bootstrap", "discovery-refresh", "discovery-health", "discovery-schema-init"}:
        from stock_agent.discovery.bootstrap import SecurityMasterBootstrapError, SecurityMasterBootstrapService
        if args.command == "discovery-schema-init":
            from stock_agent.database import Database
            Database(config["database_path"],
                     config.get("database", {}).get("busy_timeout_ms", 5000),
                     config.get("database", {}).get("wal", True)).init()
            print(json.dumps({"command": args.command, "status": "DATABASE_SCHEMA_READY",
                              "database_path": config["database_path"],
                              "paper_mutation": False}, ensure_ascii=False, indent=2))
            return 0
        service = SecurityMasterBootstrapService(config)
        try:
            if args.command == "discovery-health":
                result = service.health()
            else:
                result = service.bootstrap(refresh=args.command == "discovery-refresh")
                result["command"] = args.command
        except SecurityMasterBootstrapError as exc:
            result = {
                "command": args.command,
                "status": "BOOTSTRAP_REQUIRED",
                "reason_codes": [exc.reason_code],
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") in {
            "SECURITY_MASTER_READY", "MARKET_SCAN_READY", "ENRICHMENT_READY", "DEEP_HANDOFF_READY",
        } else 2

    if args.command == "discovery-degraded-shadow":
        from stock_agent.discovery.bootstrap import SecurityMasterBootstrapService
        from stock_agent.discovery.providers_live import (
            SECDiscoveryCapitalPreflightProvider, SECDiscoveryFundamentalProvider,
            TossDiscoveryBenchmarkProvider, TossDiscoveryMarketDataProvider,
        )
        from stock_agent.discovery.universe import InMemorySecurityMasterProvider
        from stock_agent.toss import TossClient
        from stock_agent.schemas import UserRequest

        service = SecurityMasterBootstrapService(config)
        records, diagnostic = service.diagnostic_candidate_records()
        credentials = config.get("credentials", {})
        if not records:
            print(json.dumps({"command": args.command, **diagnostic,
                              "deep_analyzed": 0, "actual_llm_calls": 0,
                              "paper_mutation": False}, ensure_ascii=False, indent=2))
            return 2
        if not credentials.get("sec_user_agent"):
            diagnostic["reason_codes"] = ["SEC_USER_AGENT_REQUIRED"]
            diagnostic["status"] = "BOOTSTRAP_REQUIRED"
            print(json.dumps({"command": args.command, **diagnostic,
                              "deep_analyzed": 0, "actual_llm_calls": 0,
                              "paper_mutation": False}, ensure_ascii=False, indent=2))
            return 2
        if not credentials.get("toss_app_key") or not credentials.get("toss_app_secret"):
            diagnostic["reason_codes"] = ["TOSS_CREDENTIALS_REQUIRED"]
            diagnostic["status"] = "BOOTSTRAP_REQUIRED"
            print(json.dumps({"command": args.command, **diagnostic,
                              "deep_analyzed": 0, "actual_llm_calls": 0,
                              "paper_mutation": False}, ensure_ascii=False, indent=2))
            return 2
        diagnostic_config = copy.deepcopy(config)
        diagnostic_config.setdefault("discovery", {})["enabled"] = True
        diagnostic_config["discovery"]["shadow_mode"] = True
        diagnostic_config["discovery"].setdefault("cost", {})["max_actual_llm_calls"] = 0
        diagnostic_config["discovery"]["cost"]["max_llm_calls_per_discovery"] = 0
        market = TossDiscoveryMarketDataProvider(
            TossClient(credentials["toss_app_key"], credentials["toss_app_secret"]))
        fundamental = SECDiscoveryFundamentalProvider(
            credentials["sec_user_agent"], config["discovery"]["bootstrap"]["fundamental_cache_dir"])
        capital = SECDiscoveryCapitalPreflightProvider(
            credentials["sec_user_agent"], config["discovery"]["bootstrap"]["sector_cache_dir"])
        app = Orchestrator(
            diagnostic_config,
            discovery_security_master=InMemorySecurityMasterProvider(records),
            discovery_market_data=market,
            discovery_fundamental_provider=fundamental,
            discovery_benchmark_provider=TossDiscoveryBenchmarkProvider(market),
            discovery_capital_provider=capital,
        )
        request = UserRequest(
            request_id="CLI_DEGRADED_DIAGNOSTIC_SHADOW", discord_message_id="CLI",
            discord_user_id="CLI", received_at="", original_text=args.command,
            intent="DISCOVER_MARKET", tickers=[], analysis_intensity=args.intensity,
            intensity_explicit=True, requested_sector="", discovery_mode="MARKET", shadow=True)
        result = app.discover_request(request)
        output = result.to_dict()
        output["diagnostic_mode"] = "DEGRADED_DIAGNOSTIC_SHADOW"
        output["security_master_diagnostic"] = diagnostic
        output["deep_analyzed"] = 0
        output["actual_llm_calls"] = 0
        output["paper_mutation"] = False
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if result.status not in {"BOOTSTRAP_REQUIRED", "BLOCKED_COVERAGE", "BLOCKED_MARKET_DATA"} else 2

    app = Orchestrator(config)
    # Shadow Discovery initializes only its schema through DiscoveryStore.  It
    # must not call Orchestrator.init(), which creates an explicit PAPER account
    # and initial cash ledger entry.
    if not (args.command in {"discover-market", "discover-sector"} and args.shadow):
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
    if args.command == "discord":
        run_chairman_bot(config, app)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
