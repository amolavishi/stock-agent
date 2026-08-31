# PRIMARY Shadow -> PRE-A Daily Chain V1

## Purpose

Run one normal authoritative PRIMARY Shadow cycle first. After PRIMARY finishes and writes its `DAILY_REPORT.md`, derive a separate non-authoritative `PRE_A_REPORT.md` from that completed report.

```text
PRIMARY Daily Shadow
        ↓
shadow_runs/<RUN_ID>/DAILY_REPORT.md
        ↓ read-only
PRE-A Sidecar
        ↓
pre_a_reports/<RUN_ID>/PRE_A_REPORT.md
```

PRE-A is post-processing only. It cannot change PRIMARY SQLite state, Shadow artifacts, Research Grade, Execution Action, Position Size, or broker state.

## Recommended daily command

Use the wrapper instead of manually running two commands:

```cmd
python -m stock_agent.daily_with_pre_a --strict --llm-provider luna --market-provider live --sec-provider sec --research-provider issuer_ir --portfolio-provider toss --input live_primary_input_20260828.json --database shadow_v1.db --shadow-output shadow_runs
```

The wrapper inserts `--daily-shadow-run` automatically, runs PRIMARY exactly once, identifies the `DAILY_REPORT.md` created or updated by that run, and only then calls the PRE-A sidecar.

Default PRE-A output:

```text
pre_a_reports/<RUN_ID>/PRE_A_REPORT.md
```

Optional PRE-A settings:

```cmd
--pre-a-llm-provider luna
--pre-a-reasoning-effort high
--pre-a-output-root pre_a_reports
```

## Failure semantics

- PRIMARY failure -> PRE-A is not run.
- PRIMARY success/DEGRADED with a completed `DAILY_REPORT.md` -> PRE-A may run.
- PRE-A failure -> PRIMARY remains completed and unmodified; the wrapper returns nonzero so the operator notices the missing secondary report.
- More than one newly changed `DAILY_REPORT.md` -> fail closed instead of guessing which report belongs to the run.
- V8 is not accepted by this wrapper. FULL V8 and V8 Challenger remain independent experiments.

## Authority boundary

`PRE_A_REPORT.md` is never an authoritative Stock Agent result. It is a secondary report derived from the official PRIMARY report and must be interpreted as monitoring metadata only.
