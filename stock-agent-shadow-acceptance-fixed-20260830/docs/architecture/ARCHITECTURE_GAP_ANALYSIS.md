# Architecture Gap Analysis

Severity: P0 = implementation/authority blocker, P1 = production path blocker,
P2 = required hardening, P3 = operational improvement.

| Component | Required | Current / evidence | Gap | Severity | Action |
|---|---|---|---|---|---|
| Orchestrator | Run lifecycle, pause/resume/cancel, terminal state | `runtime.py:144-167` only | no explicit state machine/recovery manager | P1 | PATCH then split coordinator |
| Work Queue | durable prerequisite DAG, lease/CAS/recovery/generation | `store.py:163-305` | payload prerequisites, no generation/pause/cancel/watchdog | P1 | REWRITE repository queue contract |
| EffectiveRuleSet | registry/resolver/frozen source hashes | `models.py:63-75`, `store.py:330-344` | free-form catalog/source mapping and non-waivable rules absent | P1 | REWRITE rule registry |
| Toss Adapter | MarketDataProvider raw quote/OHLCV/scope | no implementation | no authoritative market source | P0 | ADD adapter boundary; implement only verified endpoints |
| Market normalizer | freshness/provenance/technical inputs | no implementation | no MarketSnapshot/TechnicalSnapshot | P0 | ADD |
| SEC Adapter | CIK, submissions, facts, filings, rate limit | no implementation | capital/forensic source absent | P0 | ADD |
| Evidence Store | append-mostly provenance, source rank, conflict | `store.py:170-219` basic evidence only | no URI/raw artifact/source hierarchy/conflict graph | P1 | REWRITE schema/repository |
| Claim Store | typed claim/evidence graph | `models.py:118-131`, `store.py:227-234` | no values/period/grade/conflict resolution | P1 | PATCH/extend |
| SQLite repository | all authoritative entities/transactions/migrations | `store.py:44-122` | many required entities missing; legacy unique final table remains | P0 | REWRITE schema via migrations |
| Obsidian projection | SQLite→human-readable vault, retryable | no implementation | knowledge/prompt vault conflated outside runtime | P1 | ADD projection worker |
| Knowledge loader | FIRST_TOUCH/DELTA baseline | no implementation | no durable knowledge read path | P1 | ADD |
| Prompt Registry | manifest/frontmatter/schema/composition | `prompt_runtime.py:31-119` | no prompt version DB lineage/registry validation at Run level | P2 | PATCH |
| Prompt Compiler | ContextManifest + one leaf + input bundle | `prompt_runtime.py:61-119` | context placeholders, no persisted PromptExecution | P1 | PATCH/ADD persistence |
| LLMProvider | provider-neutral retry/error/cost | `providers.py:12-98` | DeepSeek transport and reservation FSM absent | P1 | REWRITE provider layer |
| DeepSeekProvider | actual configured adapter | `providers.py:62-74` stub | no HTTP/error/rate-limit/structured response | P0 for live use | ADD behind interface |
| Schema Validator | canonical JSON Schema/repair | `prompt_runtime.py:87-119` | present, but semantic output lineage incomplete | P2 | KEEP/PATCH |
| Semantic Validator | Python hard/typed validations | `gates.py:20-163` | missing technical/portfolio/evidence conflict semantics | P1 | EXTEND gate package |
| ContextManifest | exact required/included/content hashes | `prompt_runtime.py:81-86` | IDs/content only, no token budget persistence | P1 | REWRITE contract |
| Freshness Engine | subject/domain epochs + invalidation graph | `dependencies.py`, `store.py:170-219` | global/simple hashes, no replacement work graph | P0 | REWRITE |
| TechnicalFeatureCalculator | price/volume/RS/base/stage features | no implementation | StageGate cannot be authoritative from market data | P0 | ADD |
| StageGate | Python classifier + gate | `gates.py:24-27` consumes caller stage/boolean | no technical snapshot/classifier | P0 | REWRITE |
| CapitalPrescreenGate | SEC quick facts typed tri-state | `gates.py:30-45` | raw fake extraction; no SEC identity/source | P1 | PATCH after SEC adapter |
| Research Pipeline | stage ordering and candidate isolation | `runtime.py:97-139` | direct synchronous loop; no sector/industry stored entities | P1 | REWRITE workflow DAG |
| Full SEC Forensic | mandatory after deep research | prompt call only at `runtime.py:135` | no filing input/CapitalStructureSnapshot | P0 | ADD |
| Audit Pipeline | independent audit/debate/issues | prompt call at `runtime.py:138` | no independent provider/evidence/debate resolution | P1 | REWRITE |
| QualifiedCandidatePool | DB-derived terminal projection | in-memory list at `runtime.py:139-167` | no pool table/member rows/fresh prerequisite query | P0 | ADD repository entity |
| MarketExecutionGate | fresh execution-only data | `gates.py:70-82` | input DTO/provider freshness absent | P1 | PATCH after MarketData |
| RiskEngine | stop/gap/event/budget arithmetic | `gates.py:86-107` | no portfolio/risk assessment persistence; defaults in runtime | P1 | PATCH + persist |
| PositionSizer | distinct sizing policy | no class | RiskEngine currently returns shares directly | P1 | ADD |
| PortfolioComparison | candidate vs positions/cash | prompt only in `runtime.py:173-178` | no PortfolioSnapshot/read-only importer/semantic gate | P0 | ADD |
| FinalAllocationGate | live all-prerequisite transaction | `store.py:328-377` partial | no full live gate query, legacy table, context proxy | P0 | REWRITE |
| Reporting | DB snapshot→atomic JSON/Markdown | no code | only static reports | P2 | ADD |
| Configuration/secrets | typed config, secret allowlist | `env.example` only | no provider selection/secret loading/rotation | P1 | ADD |
| CI/migrations | repeatable validation/recovery | no CI/migration files | regression cannot be enforced | P2 | ADD |
