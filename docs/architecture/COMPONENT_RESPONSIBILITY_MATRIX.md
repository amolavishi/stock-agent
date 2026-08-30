# Component Responsibility Matrix

| Target module | Responsibility / public interface | Persistence | Failure behavior | Forbidden |
|---|---|---|---|---|
| `application/orchestrator.py` | `start/resume/pause/cancel/status` Run lifecycle | Run, CommandIntent | transactionally terminal/retry/manual | market inference |
| `runtime/workflow.py` | create/lease/heartbeat/CAS complete WorkItem DAG | WorkItem, Checkpoint, ErrorEvent | bounded retry, stale replacement, recovery | LLM authority |
| `persistence/sqlite/repository.py` | typed repositories and short transactions | all authoritative entities | rollback, conflict, migration error | prose/business guessing |
| `rules/registry.py` | immutable RuleDefinition catalog/source hash | RuleDefinition | startup fail on hash/conflict | runtime markdown interpretation |
| `rules/resolver.py` | scope→EffectiveRuleSet/RulePack | rule set joins | conflict blocks Run | free-form override |
| `providers/market/base.py` | `fetch_quote/fetch_ohlcv/fetch_market_snapshot` | ProviderCall/raw artifact | retry/rate-limit/unknown outcome | gate/action |
| `providers/market/toss.py` | verified Toss capability implementation | raw provider artifacts | unsupported capability=MISSING/explicit error | invented endpoint |
| `providers/sec/base.py` | CIK/submissions/facts/filing fetch contract | raw filing/artifact | retry/backoff; identity/freshness block | dilution conclusion |
| `providers/sec/edgar.py` | SEC implementation after endpoint verification | filing/evidence | preserve accession/provenance | replace SEC with LLM |
| `evidence/normalizer.py` | normalize raw source, source rank, freshness | Evidence/Snapshot/Conflict | invalidated append-only | investment decision |
| `evidence/graph.py` | Claim links, dependency edges, invalidation | Claim/edges/fences | transaction invalidation/replacement | manual stale bypass |
| `technical/features.py` | deterministic price/volume/RS/base features | TechnicalSnapshot | unavailable/unknown, no guess | LLM calculation |
| `research/discovery.py` | market→sector→industry→universe/candidate | Candidate, StageAssessment | hard fail/isolated candidate | ExecutionAction |
| `research/deep_research.py` | prompt bundle and advisory ResearchResult | PromptExecution/ResearchResult | schema repair then final fail | Gate write |
| `research/sec_forensic.py` | Full SEC after Deep Research | CapitalStructureSnapshot | missing filing blocks | “no dilution” default |
| `audit/debate.py` | independent audit/issues/evidence requests | AuditResult/DebateIssue | unresolved CRITICAL block | resolve own issue authoritatively |
| `gates/stage.py` | feature + assessment→GateResult | GateResult | reject/insufficient/retry | accept caller boolean |
| `gates/capital.py` | tri-state capital hard rules | GateResult | toxic reject, unknown block/forensic | size-down toxic risk |
| `gates/execution.py` | fresh execution market suitability | GateResult | fail closed on core stale | discovery blocking partial |
| `risk/engine.py` | stop/gap/event/portfolio risk budgets | RiskAssessment | no size on incomplete inputs | provider price |
| `risk/position_sizer.py` | risk→max/actual shares and plan arithmetic | SizeAssessment/StarterPlan | reject invalid/zero | LLM shares |
| `portfolio/comparison.py` | read-only portfolio/cash alternative comparison | OpportunityCost/CashBias | missing snapshot blocks action | mutate portfolio |
| `allocation/final_gate.py` | live prerequisite query and final single writer | FinalAction/guard | atomic rollback, 0..1 fresh money | recommendation prose |
| `prompts/compiler.py` | manifest/frontmatter/one leaf/context bundle | PromptExecution/ContextManifest | missing input/repair path | workflow state |
| `providers/llm/router.py` | role/profile/provider policy and cost | ModelCall/CostReservation | bounded fallback/circuit breaker | gate bypass |
| `knowledge/obsidian_projection.py` | SQLite verified state→vault files | ProjectionJob/ReportArtifact | retry independently; no DB rollback | operational truth |
| `reporting/renderer.py` | consistent DB read→JSON/Markdown atomic artifact | ReportArtifact/Outbox | temp/fsync/rename | state mutation |
