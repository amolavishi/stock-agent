# Stock Agent Primary Shadow Acceptance Review — 2026-08-30

## Scope

This review targets the latest 2026-08-28 Primary Shadow source delivered outside GitHub, not the stale GitHub `main` branch. V8, investment thresholds, stop/target logic, sizing philosophy, and broker-write behavior are out of scope.

## Repository state discovered

- GitHub repository: `amolavishi/stock-agent`
- GitHub default branch: `main`
- GitHub `main` HEAD observed: `8aae312628aa4aaaa78034739d173e381df14dbc` (2026-08-10)
- The GitHub `main` tree is the older Hybrid PAPER/Discord/Hermes generation and is not the latest 2026-08-28 Primary Shadow runtime.
- Therefore the verified fixes must not be merged directly onto current `main` until the latest 2026-08-28 source tree is synchronized to GitHub.

## Confirmed problems in latest delivered Primary Shadow source

### 1. Liquidity coverage repeats a small bounded subset

The system no longer uses the original market-cap-top-200-only probe, but the expensive full-candle ADV budget remains small. Without rotation, the same deterministic subset can be repeatedly evaluated while most strategy-eligible securities remain `NOT_EVALUATED`.

### 2. Provider/data failures are misclassified as investment rejections

`RESEARCH_PROVIDER_FAILURE`, `SEC_PROVIDER_FAILURE`, and `SEC_STALE_DATA` were projected into `REJECTED_*` decisions. That corrupts Shadow attribution because the company was not actually rejected on investment merit; the system failed to evaluate it.

### 3. Provider/data failures were not consistently emitted as incidents

This makes 30-day operational failure-rate analysis unreliable and mixes software/data quality failures with investment-gate performance.

## Implemented minimal fixes

### Liquidity rotation

Changed `stock_agent/adapters.py` so the bounded full-candle probe budget is split between current quote-volume-priority candidates and a deterministic daily rotation across the remaining strategy universe. The same official date is reproducible, while subsequent dates advance cumulative coverage. Rotation metadata is persisted through `stock_agent/runtime.py`.

No investment thresholds were relaxed and the expensive probe budget was not expanded without evidence.

### NOT_EVALUATED attribution

Changed `stock_agent/shadow.py` and `stock_agent/runtime.py` so:

- `SEC_STALE_DATA` -> `NOT_EVALUATED_SEC_DATA`
- `SEC_PROVIDER_FAILURE` -> `NOT_EVALUATED_SEC_PROVIDER`
- `RESEARCH_PROVIDER_FAILURE` -> `NOT_EVALUATED_RESEARCH_PROVIDER`

These states use `rejected=false` and `watch=false` and preserve explicit stage/reason fields. Provider/data failures emit idempotent Shadow incidents with stage, failure code, provider component, retryability, and candidate impact. Catalyst evidence gaps remain explicitly `NOT_EVALUATED_CATALYST`, not investment rejections.

## Files changed in the verified patch

- `stock_agent/adapters.py`
- `stock_agent/runtime.py`
- `stock_agent/shadow.py`
- `tests/test_production_adapters.py`
- `tests/test_shadow_v1_20260825.py`

## Verification on the corrected latest source

- `python -m compileall -q stock_agent tests` — PASS
- Full unittest discovery — **305 / 305 PASS**
- Prompt Library contract validator — **PASS**, `failure_count=0`
- Broker-write behavior — unchanged
- V8 — untouched/off for this acceptance work

New regression coverage includes liquidity exploration rotation across official dates, same-day deterministic rotation metadata, stale SEC evidence remaining an evidence gap rather than investment rejection, and research-provider failure projecting to `NOT_EVALUATED` plus an incident.

## Artifact hashes

- Verified patch SHA-256: `a389091a9db60ea8b5e381600ae2487d64baa66158f0caa609eb38228cd25611`
- Corrected source ZIP SHA-256: `79d3e07d969507fafc5b495939117ace0f81a4e7f7072030258b8348ed326093`

## Live verification status

Post-fix real-provider live acceptance has **not** been claimed from this review environment because the required live credentials/network are not available here. The corrected code is regression-green, but final Shadow-start acceptance still requires one real read-only Primary run after the latest source is synchronized to GitHub/local runtime.

Required checks:

1. Broad universe loads successfully.
2. Liquidity rotation/coverage metadata appears in the run output.
3. Provider/data failures, if any, appear as `NOT_EVALUATED` plus incidents rather than `REJECTED`.
4. Daily artifacts are generated normally.
5. Provenance matches the executed source.
6. `broker_write_count = 0`.

## Merge warning

**DO NOT MERGE THIS REVIEW DIRECTLY INTO THE CURRENT GITHUB MAIN AS IF MAIN WERE THE LATEST SHADOW SOURCE.**

First synchronize the 2026-08-28 latest Shadow source tree to a GitHub branch. Then apply the verified patch, run CI, and perform one real read-only live acceptance run.
