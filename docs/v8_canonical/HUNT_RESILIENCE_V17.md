# V8 HUNT Resilience V1.7

## Incident basis

RUN-20260831-011 reached broad discovery and a live prescreen survivor, then terminated with a non-retryable Luna HTTP 400 before Deep Research. The run was correctly detected as pipeline starvation, but the human report still rendered `NO_TRADE`.

The code path showed two independent engineering defects:

1. Canonical research Evidence can contain dozens of long source documents. PromptRuntime serialized the full semantic context into the provider message while Runtime also attached `runtime_input`, and OpenAIResponsesProvider appended that raw input as a second user message. Rich Evidence storage and model working context were therefore conflated and duplicated.
2. A blocked/starved HUNT could still render a clean `NO_TRADE` conclusion, contaminating Shadow opportunity labels.

V1.7 is an engineering-resilience layer. It does not modify investment thresholds or certification authority.

## Invariants

- V8 remains canonical investment logic.
- Canonical RawArtifact/Evidence contents remain immutable and untruncated in SQLite.
- Model working context is a non-authoritative bounded projection only.
- Source URL, source timestamp, source class, full-content hash, full-content length, structured catalysts, and receipt identity are preserved in the projection.
- Omitted text may never be inferred by the model.
- `runtime_input` is not transmitted a second time when PromptRuntime already embedded semantic context.
- CatalystGate, SEC gate, Expectation Gap, audits, A/A- certification, Grade Firewall, PRE-A boundaries, risk rules, and broker-write authority are unchanged.
- Initial non-PASS Catalyst evidence is persisted as Evidence Debt before the first capability call.
- OpenAI HTTP failures expose only bounded non-secret type/code/param diagnostics; provider message/body is not persisted.
- A blocked/starved/incomplete HUNT is `NOT_EVALUABLE_PIPELINE_FAILURE`, never a clean `NO_TRADE` opportunity conclusion.
- A successful MARKET_ANALYSIS remains a stage PASS even if a later candidate stage fails.

## Model working context

The Evidence Store and model context have different jobs:

```text
Canonical Evidence Store
  full SEC / IR / media / customer / industry source
  immutable hashes and PIT lineage
        |
        v
Deterministic Wire Projection
  relevant excerpts
  structured catalysts
  source identities / timestamps
  full-content hashes / lengths
        |
        v
LLM capability / research / audit
```

Long bodies use deterministic keyword-centered excerpts. Large source lists and time series are bounded with explicit truncation receipts and full-value hashes. If the first projection remains too large, a second aggressive projection is used. The final provider request has a local byte preflight fence.

This is not evidence deletion: final gates and audit lineage continue to refer to the canonical persisted Evidence, not to an invented summary source.

## RUN-011 semantics

A run with `ENGINEERING_INCIDENT_PIPELINE_STARVATION`, a blocked HUNT result, or unresolved failed WorkItems must report:

```text
FinalAllocation: NO_INVESTMENT_DECISION — HUNT_INCOMPLETE
Today's Conclusion: NOT_EVALUABLE_PIPELINE_FAILURE
```

Such a run must not enter clean `NO_TRADE` performance statistics.

## Validation

The V1.7 regression matrix covers Windows/Linux and Python 3.11/3.12 and verifies:

- giant Evidence is bounded on the wire while source/hash/catalyst identity survives;
- PromptRuntime messages use the bounded projection;
- embedded semantic context is not duplicated through runtime_input;
- safe OpenAI 400 type/code/param diagnostics without body leakage;
- Evidence Debt remains non-authoritative for grade/PRE-A/execution.

Full Stock Agent, V8 PRIMARY, Alpha Discovery, Catalyst Acquisition, and HUNT V1.6 regressions must remain green before merge.
