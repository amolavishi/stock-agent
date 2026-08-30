# Migration Matrix

| Existing path | Decision | Migration action | Risk | Required tests |
|---|---|---|---|---|
| `stock_agent/models.py` | PATCH/MERGE | retain enums/hash, add typed snapshots/provider requests and version fields | medium | model/schema round trips |
| `stock_agent/store.py` | REWRITE incrementally | introduce migration-managed repositories and typed tables; retain old data read-only during cutover | high | migration, WAL crash, CAS, backup |
| `stock_agent/runtime.py` | REWRITE incrementally | extract Orchestrator/Workflow/Research/Execution services; keep StockAgent facade during migration | high | all legacy + T1–T20 E2E |
| `stock_agent/gates.py` | PATCH then SPLIT | preserve hard validators; split stage/capital/execution/risk/final modules | medium | negative hard-rule suite |
| `stock_agent/dependencies.py` | REWRITE | subject/domain fences and graph traversal | high | refresh/invalidation/late commit |
| `stock_agent/prompt_runtime.py` | KEEP/PATCH | retain registry/schema composition; add persisted compiler/execution lineage | low | prompt validator/repair/mode |
| `stock_agent/providers.py` | REWRITE | retain Fake/Recorded; add provider-neutral error/cost/router and DeepSeek transport | high | timeout/429/malformed output/cost |
| `stock_agent/cli.py` | PATCH | typed config, provider selection, command idempotency, secret allowlist | medium | CLI config/secret redaction |
| `tests/test_stock_agent.py` | KEEP then REBASE | preserve contract intent; replace authoritative fixture fields with provider recordings | medium | all existing negative tests |
| `tests/test_production_runtime.py` | KEEP/EXPAND | add adapter, crash/recovery, concurrency and recorded provider cases | low | T1–T20 |
| `outputs/STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2` | KEEP as fixture/reference | use package ZIP as immutable Prompt Vault input; no redesign | low | manifest/schema validator |
| Toss integration | ADD | implement only verified capability under `providers/market/toss.py` | high | recorded/live contract tests |
| SEC/EDGAR integration | ADD | implement CIK/submissions/facts/filings + normalizer | high | accession/freshness/rate limit |
| Obsidian integration | ADD | SQLite projection/outbox; no operational writes | medium | projection failure/retry |
| static `PRODUCTION_IMPLEMENTATION_REPORT.md` | MERGE/REPLACE | reports must be generated from DB verification, not asserted completion | low | report hash/regeneration |
