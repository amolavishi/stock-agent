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
    if args.command == "discord":
        run_chairman_bot(config, app)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
