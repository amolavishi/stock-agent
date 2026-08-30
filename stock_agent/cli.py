from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .config import load_environment, require_secret
from .models import RunMode, canonical_hash, utc_now
from .paths import canonical_prompt_library_root
from .runtime import ProductionStockAgent, StockAgent, StockAgentConfig
from .adapters import (HttpJsonSECProvider, RecordedMarketDataProvider, RecordedPortfolioProvider,
                       RecordedResearchEvidenceProvider, RecordedSECProvider, TossMarketDataProvider,
                       TossPortfolioProvider, ConfiguredResearchEvidenceProvider,
                       IssuerIRWebEvidenceProvider,
                       CompositeResearchEvidenceProvider,
                       CompositeLiveMarketContextProvider)
from .gates import MarketContextGate
from .models import EffectiveRuleSet
from .providers import CodexExecProvider, DeepSeekProvider, FakeProvider, ModelProfile, ModelRouter, OpenAICompatibleProvider, OpenAIResponsesProvider
from .reporting import AuthoritativeHuntReportRenderer, ReportContractError
from .shadow import DailyShadowRunner, LunaHealthChecker, SHADOW_VERSION, persist_outcomes, reproducibility_metadata
from .daily_orchestrator import PrimaryV8DailyOrchestrator
from .store import SQLiteStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Stock Agent Architecture v1.1 runtime")
    parser.add_argument("--mode", choices=[mode.value for mode in RunMode])
    parser.add_argument("--input", type=Path, help="JSON runtime input fixture")
    default_library = canonical_prompt_library_root()
    parser.add_argument("--library-root", type=Path, default=default_library)
    parser.add_argument("--database", type=Path, default=Path("stock_agent.db"))
    parser.add_argument("--report-output", type=Path, help="Render a run_id-bound authoritative Markdown report")
    parser.add_argument("--strict", action="store_true", help="Use provider-backed fail-closed production path")
    parser.add_argument("--llm-provider", choices=["fake", "deepseek", "luna", "codex"], default="fake")
    parser.add_argument("--market-provider", choices=["recorded", "toss", "live"], default="recorded")
    parser.add_argument("--sec-provider", choices=["recorded", "sec"], default="recorded")
    parser.add_argument("--portfolio-provider", choices=["recorded", "toss"], default="recorded")
    parser.add_argument("--research-provider", choices=["recorded", "configured", "issuer_ir", "unavailable"], default="recorded")
    parser.add_argument("--smoke-deepseek", action="store_true", help="Send one minimal JSON call to DeepSeek and exit")
    parser.add_argument("--smoke-luna", action="store_true", help="Validate one structured OpenAI Responses call to gpt-5.6-luna and exit")
    parser.add_argument("--smoke-codex", action="store_true", help="Send one read-only canonical JSON call through the authenticated codex exec CLI and exit")
    parser.add_argument("--smoke-toss-accounts", action="store_true", help="Verify Toss accounts read-only with account identifiers redacted")
    parser.add_argument("--smoke-toss-portfolio", action="store_true", help="Read Toss holdings and buying power using auto-discovered accountSeq; never calls order APIs")
    parser.add_argument("--smoke-market-context", action="store_true", help="Read the ten required MarketContext assets; never calls order APIs")
    parser.add_argument("--daily-shadow-run", action="store_true", help="Run one operator-triggered SHADOW_V1.1 cycle; broker writes remain disabled")
    parser.add_argument("--daily-shadow-with-v8", action="store_true", help="Run Primary SHADOW_V1.1, export a PIT snapshot, then isolated V8 Challenger 00A~18")
    parser.add_argument("--v8-bundle", type=Path, help="V8 prompt ZIP/directory for --daily-shadow-with-v8")
    parser.add_argument("--v8-reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"), default=os.getenv("LUNA_DEFAULT_REASONING_EFFORT", "medium"))
    parser.add_argument("--shadow-output", type=Path, default=Path("shadow_runs"), help="Pinned root for immutable Shadow artifacts")
    parser.add_argument("--resume-shadow-run", help="Resume an interrupted Shadow run at an authoritative checkpoint")
    parser.add_argument("--shadow-version", default=SHADOW_VERSION)
    parser.add_argument("--update-shadow-outcomes", metavar="DECISION_ID", help="Append PIT outcome observations for one immutable decision")
    parser.add_argument("--outcome-bars", type=Path, help="JSON array of observed daily OHLC bars")
    parser.add_argument("--outcome-as-of", help="Observation cutoff timestamp/date for outcome append")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    load_environment(project_root)
    if args.update_shadow_outcomes:
        if args.outcome_bars is None or not args.outcome_as_of:
            parser.error("--update-shadow-outcomes requires --outcome-bars and --outcome-as-of")
        store = SQLiteStore(args.database)
        try:
            row = store.connection.execute("SELECT decision_json FROM shadow_decisions WHERE decision_id=?", (args.update_shadow_outcomes,)).fetchone()
            if row is None:
                raise RuntimeError("unknown shadow decision_id")
            decision = json.loads(row["decision_json"])
            bars = json.loads(args.outcome_bars.read_text(encoding="utf-8"))
            if not isinstance(bars, list):
                raise RuntimeError("outcome bars must be one JSON array")
            ids = persist_outcomes(store, decision, bars, as_of=args.outcome_as_of)
            print(json.dumps({"decision_id": args.update_shadow_outcomes, "outcome_ids": ids, "broker_writes": 0}, ensure_ascii=False, indent=2))
            return 0
        finally:
            store.close()
    if args.smoke_deepseek:
        provider = DeepSeekProvider(require_secret("DEEPSEEK_API_KEY"), os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com/chat/completions"))
        try:
            payload, telemetry = provider.call({"messages": [{"role": "user", "content": 'Return valid JSON containing {"ping":"pong"}.'}], "temperature": 0, "attempt": 1})
        except RuntimeError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({"ok": True, "response_keys": sorted(payload), "telemetry": telemetry}, ensure_ascii=False, indent=2))
        return 0
    if args.smoke_luna:
        results = []
        try:
            api_key = require_secret("OPENAI_API_KEY")
            model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
            effort = os.getenv("LUNA_DEFAULT_REASONING_EFFORT", "medium")
            provider = OpenAIResponsesProvider(api_key, model, reasoning_effort=effort, timeout=float(os.getenv("LUNA_TIMEOUT_SEC", "90")))
            schema = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}
            _, telemetry = provider.call({"prompt_id": "luna-smoke", "prompt_body": 'Return exactly JSON: {"ok":true}.', "output_schema_definition": schema, "attempt": 1})
            results.append({"effort": effort, "ok": True, "telemetry": {key: value for key, value in telemetry.items() if key not in {"endpoint"}}})
        except Exception as exc:
            results.append({"effort": os.getenv("LUNA_DEFAULT_REASONING_EFFORT", "medium"), "ok": False, "error": str(exc)[:240]})
        print(json.dumps({"ok": all(item["ok"] for item in results), "results": results}, ensure_ascii=False, indent=2))
        return 0 if all(item["ok"] for item in results) else 2
    if args.smoke_codex:
        provider = CodexExecProvider(binary=os.getenv("CODEX_EXEC_BIN", "codex"), timeout=float(os.getenv("CODEX_EXEC_TIMEOUT_SEC", "120")))
        try:
            payload, telemetry = provider.call({"prompt_id": "codex-smoke", "prompt_body": 'Return exactly JSON: {"ok":true}.', "output_schema_definition": {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}, "additionalProperties": False}, "reasoning_effort": "high", "attempt": 1})
        except RuntimeError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({"ok": True, "response_keys": sorted(payload), "telemetry": telemetry}, ensure_ascii=False, indent=2))
        return 0
    if args.smoke_toss_accounts:
        provider = TossMarketDataProvider(
            os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET"),
            os.getenv("TOSS_BASE_URL", "https://openapi.tossinvest.com"),
        )
        try:
            artifact = provider.fetch_accounts()
            accounts = [{"accountConfigured": bool(row.get("account_seq")), "accountType": row["account_type"]} for row in artifact.payload.get("accounts", [])]
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)[:240], "diagnostic": provider.last_error_diagnostic}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({"ok": True, "accounts": accounts, "count": len(accounts), "read_only": True}, ensure_ascii=False, indent=2))
        return 0
    if args.smoke_toss_portfolio:
        provider = TossMarketDataProvider(
            os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET"),
            os.getenv("TOSS_BASE_URL", "https://openapi.tossinvest.com"),
        )
        try:
            artifact = TossPortfolioProvider(provider).fetch_snapshot({})
            payload = artifact.payload
            positions = [
                {
                    "symbol": row.get("subject_id"),
                    "shares": row.get("shares"),
                    "average_cost": row.get("average_cost"),
                    "as_of": row.get("as_of"),
                }
                for row in (payload.get("positions") or [])
            ]
            summary = {
                "accountConfigured": payload.get("account_seq") is not None,
                "cash": payload.get("cash"),
                "total_equity": payload.get("total_equity"),
                "positions": positions,
                "read_only": True,
            }
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)[:240], "diagnostic": provider.last_error_diagnostic}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))
        return 0
    if args.smoke_market_context:
        toss = TossMarketDataProvider(
            os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET"),
            os.getenv("TOSS_BASE_URL", "https://openapi.tossinvest.com"),
        )
        provider = CompositeLiveMarketContextProvider(toss)
        try:
            artifact = provider.fetch_market_context({"symbols": ["SPY", "QQQ", "IWM", "SOXX", "VIX", "US10Y", "DXY", "WTI", "BTC", "ETH"], "count": 30})
            gate = MarketContextGate().evaluate(artifact.payload, EffectiveRuleSet())
            assets = artifact.payload.get("assets") or {}
            summary = {
                "ok": gate.decision.value == "PASS",
                "gate": gate.as_dict(),
                "assets": {
                    symbol: {
                        key: details.get(key)
                        for key in ("provider", "source_identifier", "sync_group", "value", "unit", "currency", "observed_at", "fetched_at", "observation_count", "raw_artifact_id", "evidence_id")
                    }
                    for symbol, details in assets.items()
                },
                "read_only": True,
                "broker_writes": 0,
            }
        except Exception as exc:
            print(json.dumps({"ok": False, "error": str(exc)[:240], "diagnostic": toss.last_error_diagnostic, "read_only": True, "broker_writes": 0}, ensure_ascii=False, indent=2))
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if summary["ok"] else 2
    if (args.mode is None and not args.daily_shadow_run and not args.daily_shadow_with_v8) or args.input is None:
        parser.error("--mode and --input are required unless --smoke-deepseek is used")
    input_data = json.loads(args.input.read_text(encoding="utf-8"))
    llm_provider = None
    router = None
    if args.llm_provider == "deepseek":
        llm_provider = DeepSeekProvider(require_secret("DEEPSEEK_API_KEY"), os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com/chat/completions"))
        profiles = {name: ModelProfile(name, "deepseek", llm_provider.model) for name in ("FAST_CHEAP", "BALANCED", "DEEP_REASONING", "CRITICAL_AUDIT", "LUNA_HIGH", "LUNA_EXTRA_HIGH")}
        router = ModelRouter({"deepseek": llm_provider}, profiles)
    elif args.llm_provider == "fake":
        # Explicit fixture mode only.  The orchestrator never treats this as
        # live authority; it is useful for deterministic end-to-end contract
        # acceptance without external mutations.
        llm_provider = FakeProvider()
        profiles = {name: ModelProfile(name, "fake", llm_provider.model, reasoning_effort="medium") for name in ("FAST_CHEAP", "BALANCED", "DEEP_REASONING", "CRITICAL_AUDIT", "LUNA_HIGH", "LUNA_EXTRA_HIGH")}
        router = ModelRouter({"fake": llm_provider}, profiles)
    elif args.llm_provider == "luna":
        luna_model = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
        default_effort = os.getenv("LUNA_DEFAULT_REASONING_EFFORT", "medium")
        deep_effort = os.getenv("LUNA_DEEP_REASONING_EFFORT", "high")
        luna = OpenAIResponsesProvider(require_secret("OPENAI_API_KEY"), luna_model, reasoning_effort=default_effort, timeout=float(os.getenv("LUNA_TIMEOUT_SEC", "90")))
        llm_provider = luna
        retry_count = int(os.getenv("LUNA_MAX_RETRIES", "2"))
        retry_backoff = float(os.getenv("LUNA_RETRY_BACKOFF_SEC", "1"))
        profiles = {
            "FAST_CHEAP": ModelProfile("FAST_CHEAP", "luna", luna.model, retry_count, default_effort, "responses", retry_backoff),
            "BALANCED": ModelProfile("BALANCED", "luna", luna.model, retry_count, default_effort, "responses", retry_backoff),
            "DEEP_REASONING": ModelProfile("DEEP_REASONING", "luna", luna.model, retry_count, deep_effort, "responses", retry_backoff),
            "CRITICAL_AUDIT": ModelProfile("CRITICAL_AUDIT", "luna", luna.model, retry_count, deep_effort, "responses", retry_backoff),
            "LUNA_HIGH": ModelProfile("LUNA_HIGH", "luna", luna.model, retry_count, default_effort, "responses", retry_backoff),
            "LUNA_EXTRA_HIGH": ModelProfile("LUNA_EXTRA_HIGH", "luna", luna.model, retry_count, deep_effort, "responses", retry_backoff),
        }
        router = ModelRouter({"luna": luna}, profiles)
    elif args.llm_provider == "codex":
        codex = CodexExecProvider(binary=os.getenv("CODEX_EXEC_BIN", "codex"), timeout=float(os.getenv("CODEX_EXEC_TIMEOUT_SEC", "120")))
        llm_provider = codex
        profiles = {}
        for name in ("FAST_CHEAP", "BALANCED", "DEEP_REASONING", "CRITICAL_AUDIT", "LUNA_HIGH", "LUNA_EXTRA_HIGH"):
            effort = "xhigh" if name in {"CRITICAL_AUDIT", "LUNA_EXTRA_HIGH"} else "high"
            profiles[name] = ModelProfile(name, "codex", "codex-cli", reasoning_effort=effort)
        router = ModelRouter({"codex": codex}, profiles)
    if args.strict:
        recordings = input_data.get("provider_recordings") or {}
        # The recorded end-to-end acceptance mode must exercise the same
        # execution freshness gates as a daily run.  Refresh only the
        # fixture's transport timestamps in memory; the fixture file and
        # production investment rules remain untouched.  This branch is
        # available only when every provider is explicitly recorded and the
        # LLM provider is ``fake``.
        fixture_mode = (
            args.llm_provider == "fake"
            and args.market_provider == "recorded"
            and args.sec_provider == "recorded"
            and args.portfolio_provider == "recorded"
            and args.research_provider == "recorded"
        )
        if fixture_mode:
            fixture_now = utc_now()
            recordings["_recorded_at"] = fixture_now
            portfolio_recording = recordings.get("portfolio_snapshot")
            if isinstance(portfolio_recording, dict):
                portfolio_recording["_recorded_at"] = fixture_now
                portfolio_recording["as_of"] = fixture_now
            execution_recording = recordings.get("market_execution")
            if isinstance(execution_recording, dict):
                execution_recording["source_observed_at"] = fixture_now
            # The strict execution path requires a same-run, provider-backed
            # economic scenario.  The acceptance fixture carries the public
            # E1 evidence but predates that execution contract, so add the
            # deterministic fixture scenario in memory only.  This remains a
            # recorded contract test and is never promoted to live authority.
            research_recording = recordings.get("research")
            if isinstance(research_recording, dict) and isinstance(research_recording.get("SEC1"), dict):
                scenario = {
                    "security_id": "SEC1",
                    "bull_value": 14.0,
                    "base_value": 10.5,
                    "bear_value": 7.0,
                    "bull_probability": 0.3,
                    "base_probability": 0.5,
                    "bear_probability": 0.2,
                    "opportunity_cost_score": 0.1,
                    "evidence_ids": ["E1"],
                    "source_stage_lineage": ["ADVERSARIAL_AUDIT", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "PORTFOLIO_REVIEW"],
                }
                scenario["scenario_value_hash"] = canonical_hash(scenario)
                research_recording["SEC1"]["economic_scenario"] = scenario
        if args.market_provider in {"toss", "live"}:
            toss_provider = TossMarketDataProvider(os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET"), os.getenv("TOSS_BASE_URL", "https://openapi.tossinvest.com"))
            market_provider = CompositeLiveMarketContextProvider(toss_provider) if args.market_provider == "live" else toss_provider
        else:
            market_provider = RecordedMarketDataProvider(recordings)
        if args.sec_provider == "sec":
            sec_provider = HttpJsonSECProvider(user_agent=os.getenv("SEC_USER_AGENT"))
        else:
            sec_provider = RecordedSECProvider(recordings.get("sec") or {})
        if args.portfolio_provider == "toss":
            market_for_portfolio = market_provider if isinstance(market_provider, TossMarketDataProvider) else getattr(market_provider, "toss", TossMarketDataProvider(os.getenv("TOSS_CLIENT_ID"), os.getenv("TOSS_CLIENT_SECRET"), os.getenv("TOSS_BASE_URL", "https://openapi.tossinvest.com")))
            account_seq = int(os.environ["TOSS_ACCOUNT_SEQ"]) if os.getenv("TOSS_ACCOUNT_SEQ") else None
            portfolio_provider = TossPortfolioProvider(market_for_portfolio, account_seq)
        else:
            portfolio_provider = RecordedPortfolioProvider(recordings.get("portfolio_snapshot") or {})
        if args.research_provider == "recorded":
            research_provider = RecordedResearchEvidenceProvider(recordings.get("research") or {})
        elif args.research_provider == "configured":
            research_base = os.getenv("RESEARCH_BASE_URL", "").strip()
            research_path = os.getenv("RESEARCH_PATH", "/")
            if not research_base:
                raise RuntimeError("RESEARCH_BASE_URL is missing; configure a verified non-SEC evidence endpoint")
            research_provider = ConfiguredResearchEvidenceProvider(research_base, research_path)
        elif args.research_provider == "issuer_ir":
            source_config = os.getenv("RESEARCH_ISSUER_SOURCES_JSON", "").strip()
            source_file = os.getenv("RESEARCH_ISSUER_SOURCES_FILE", "").strip()
            if source_file:
                source_config = Path(source_file).read_text(encoding="utf-8")
            if not source_config:
                raise RuntimeError("RESEARCH_ISSUER_SOURCES_JSON or RESEARCH_ISSUER_SOURCES_FILE is missing")
            try:
                issuer_sources = json.loads(source_config)
            except json.JSONDecodeError as exc:
                raise RuntimeError("issuer IR source configuration must be valid JSON") from exc
            issuer_provider = IssuerIRWebEvidenceProvider(
                issuer_sources,
                timeout=float(os.getenv("RESEARCH_TIMEOUT_SEC", "45")),
                max_bytes=int(os.getenv("RESEARCH_MAX_BYTES", "4000000")),
            )
            # Explicit issuer IR remains the highest-authority source.  The
            # secondary feed is only a real NEWS/MEDIA fallback for newly
            # discovered tickers and can never be treated as COMPANY_IR.
            research_provider = CompositeResearchEvidenceProvider(issuer_provider)
        else:
            research_provider = None
        config = StockAgentConfig(args.library_root, args.database, strict_inputs=True, market_data_provider=market_provider, sec_provider=sec_provider, portfolio_provider=portfolio_provider, research_provider=research_provider)
        agent = ProductionStockAgent(config, provider=llm_provider, router=router)
    else:
        agent = StockAgent(StockAgentConfig(args.library_root, args.database), provider=llm_provider, router=router)
    try:
        if args.daily_shadow_with_v8:
            fixture_mode = args.llm_provider == "fake" and args.market_provider == "recorded" and args.sec_provider == "recorded" and args.portfolio_provider == "recorded" and args.research_provider == "recorded"
            if not args.strict or (args.llm_provider != "luna" and not fixture_mode):
                parser.error("--daily-shadow-with-v8 requires --strict --llm-provider luna (or all-recorded fixture mode with fake provider)")
            if args.v8_bundle is None:
                parser.error("--daily-shadow-with-v8 requires --v8-bundle")
            project_root = Path(__file__).resolve().parents[1]
            metadata = reproducibility_metadata(
                project_root, args.library_root,
                model=getattr(llm_provider, "model", "UNKNOWN"),
                provider="fake" if fixture_mode else "luna",
                reasoning_effort={name: str(profile.reasoning_effort) for name, profile in (router.profiles if router else {}).items()},
                config_values={
                    "market_provider": args.market_provider, "sec_provider": args.sec_provider,
                    "portfolio_provider": args.portfolio_provider, "research_provider": args.research_provider,
                },
            )
            # Preflight is deliberately component-scoped.  The Primary runner
            # still performs the actual full run; a smoke response is never a
            # Daily PASS by itself.
            def preflight() -> dict[str, Any]:
                health: dict[str, Any] = {"luna": {"status": "NOT_RUN"}, "market": {"status": "PASS"}, "sec": {"status": "PASS"}, "research": {"status": "PASS"}, "portfolio": {"status": "PASS"}, "evidence": {"status": "PASS"}, "gate_integrity": {"status": "PASS"}}
                if fixture_mode:
                    health["luna"] = {"status": "PASS", "mode": "FIXTURE_ONLY"}
                else:
                    try:
                        health["luna"] = LunaHealthChecker(llm_provider, agent.prompts).check()
                    except Exception as exc:
                        health["luna"] = {"status": "FAILED", "error": str(exc)[:240]}
                if args.research_provider == "unavailable":
                    health["research"] = {"status": "FAILED", "error": "research provider unavailable"}
                if getattr(agent.config, "research_provider", None) is None:
                    health["research"] = {"status": "FAILED", "error": "research provider not configured"}
                failures = {key: value for key, value in health.items() if key != "status" and value.get("status") != "PASS"}
                health["status"] = "PASS" if not failures else "FAILED"
                if failures:
                    raise RuntimeError("PREFLIGHT_FAILED:" + json.dumps(failures, ensure_ascii=False, sort_keys=True))
                return health
            orchestrator = PrimaryV8DailyOrchestrator(
                agent, args.shadow_output, metadata,
                prompt_bundle=args.v8_bundle,
                preflight=preflight,
                v8_executor=None,
                shadow_version=args.shadow_version,
                reasoning_effort=args.v8_reasoning_effort,
            )
            # Bind the configured Luna transport to V8 without changing the
            # Primary runtime or its authority boundary.
            from .v8_challenger import LunaV8StageExecutor
            if fixture_mode:
                def fixture_v8_executor(stage: str, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
                    if stage == "18":
                        return {"ticker": "SEC1", "score": 65, "score_start": 0, "hard_gate_pass": True, "category": "B_ONLY"}
                    return {"status": "FIXTURE_ONLY", "stage_observed": stage}
                orchestrator.v8_executor = fixture_v8_executor
            else:
                orchestrator.v8_executor = LunaV8StageExecutor(llm_provider, reasoning_effort=args.v8_reasoning_effort)
            result = orchestrator.run(input_data, resume_run_id=args.resume_shadow_run)
            print("DAILY SHADOW + V8 COMPLETE")
            print(json.dumps({"run_id": result.run_id, "status": result.status, "preflight": result.preflight_status, "primary": result.primary_status, "export": result.export_status, "v8": result.v8_status, "comparison": result.comparison_status, "reports": dict(result.paths), "broker_writes": result.broker_write_count}, ensure_ascii=False, indent=2))
            return 0 if result.status == "SUCCEEDED" else 2
        if args.daily_shadow_run:
            if not args.strict or args.llm_provider != "luna":
                parser.error("--daily-shadow-run requires --strict --llm-provider luna")
            project_root = Path(__file__).resolve().parents[1]
            metadata = reproducibility_metadata(
                project_root, args.library_root,
                model=getattr(llm_provider, "model", "UNKNOWN"), provider="luna",
                reasoning_effort={name: str(profile.reasoning_effort) for name, profile in (router.profiles if router else {}).items()},
                config_values={
                    "market_provider": args.market_provider, "sec_provider": args.sec_provider,
                    "portfolio_provider": args.portfolio_provider, "research_provider": args.research_provider,
                },
            )
            runner = DailyShadowRunner(
                agent, args.shadow_output, metadata,
                provider_health=LunaHealthChecker(llm_provider, agent.prompts).check,
                shadow_version=args.shadow_version,
            )
            result = runner.run(input_data, resume_run_id=args.resume_shadow_run)
            print("SHADOW RUN COMPLETE")
            print(json.dumps({
                "run_id": result.shadow_run_id, "version": args.shadow_version, "status": result.status,
                "hunt_run_id": result.hunt_run_id, "execution_run_id": result.execution_run_id,
                "artifacts": result.artifact_paths, "broker_writes": result.broker_write_count,
            }, ensure_ascii=False, indent=2))
            return 0 if result.status in {"SUCCEEDED", "DEGRADED"} else 2
        outcome = agent.run(RunMode(args.mode), input_data)
        report_error = None
        if args.report_output:
            try:
                AuthoritativeHuntReportRenderer(agent.store).write(outcome.run_id, args.report_output)
            except (ReportContractError, OSError, KeyError, ValueError) as exc:
                report_error = str(exc)
                if args.strict:
                    print(json.dumps({"run_id": outcome.run_id, "report_error": report_error}, ensure_ascii=False, indent=2))
                    return 2
        print(json.dumps({"run_id": outcome.run_id, "mode": outcome.mode.value, "outcome": outcome.outcome, "qualified_candidates": list(outcome.qualified_candidates), "authoritative_action": outcome.authoritative_action.value if outcome.authoritative_action else None, "allocation": outcome.allocation, "blocked_reason": outcome.blocked_reason}, ensure_ascii=False, indent=2))
        if report_error:
            print(json.dumps({"report_error": report_error}, ensure_ascii=False))
        return 0 if not outcome.outcome.startswith("BLOCKED") else 2
    finally:
        agent.close()


if __name__ == "__main__":
    raise SystemExit(main())
