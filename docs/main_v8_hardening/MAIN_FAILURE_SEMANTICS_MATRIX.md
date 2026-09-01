# MAIN_FAILURE_SEMANTICS_MATRIX

## Governing invariant

```text
Investment Failure != Engineering Failure
Evidence Failure != Investment Failure
Pre-Discovery Failure != No Opportunity
```

A clean `NO_TRADE` / `NO_QUALIFIED_CANDIDATE` conclusion is permitted only when the relevant opportunity set was actually evaluated through the required Discovery/research/certification path and the negative conclusion is investment-economic rather than technical.

## Taxonomy

| Class | Typical examples | Scope | Investment meaning | Allowed terminal / routing semantics | Clean NO_TRADE allowed? |
|---|---|---|---|---|---:|
| `INVESTMENT_REJECT` | verified toxic dilution, Stage 3 extreme chase, liquidity hard fail, failed catalyst/economics gate | candidate | Negative investment fact | candidate `REJECT` / `EXCLUDE`; run may still continue | Yes, but only after all surviving candidates are evaluated |
| `EVIDENCE_INSUFFICIENT` | unresolved consensus, missing exact catalyst economics, incomplete nonfatal SEC field | candidate | Unknown, not bearish | Evidence Debt / Secondary / return-to-research | No |
| `SOURCE_EXHAUSTED` | all permitted sources searched and unresolved fact cannot currently be verified | candidate or search family | Unknown after proven exhaustion | `SOURCE_EXHAUSTED` / B+ cap / non-executable depending rule | No, unless every opportunity is otherwise fully evaluated and policy explicitly permits conclusion |
| `PROVIDER_FAILURE` | HTTP/provider unavailable, bad upstream response | candidate or run-global | No investment information | candidate failure isolation or `NOT_EVALUABLE_PROVIDER_FAILURE` | No |
| `MODEL_FAILURE` | model 400/timeout/nonresponse | candidate or run-global | No investment information | candidate engineering failure or `NOT_EVALUABLE_MODEL_FAILURE` | No |
| `SCHEMA_FAILURE` | malformed structured output, contract violation | candidate or run-global | No investment information | retry/repair; then candidate failure or `NOT_EVALUABLE_SCHEMA_FAILURE` | No |
| `PIPELINE_FAILURE` | required stage not invoked, missing receipt, invalid state transition | candidate or run-global | No investment information | `NOT_EVALUABLE_PIPELINE_FAILURE` | No |
| `STALE_DATA` | market/SEC/research observation exceeds policy age | candidate or run-global | No current investment conclusion | refresh; otherwise `NOT_EVALUABLE_STALE_DATA` / evidence debt | No |
| `PRE_DISCOVERY_FAILURE` | Market core unavailable, universe acquisition failed, canonical V8 source absent | run-global | Search did not occur sufficiently | `NOT_EVALUABLE_PRE_DISCOVERY` / `NOT_EVALUABLE_INPUT_INTEGRITY` | No |
| `CANDIDATE_SCOPED_FAILURE` | one issuer SEC parser/model/research call fails | candidate | Candidate not evaluated | persist failure; continue all independent candidates | No for that candidate; must not poison others |
| `RUN_GLOBAL_FAILURE` | common provider outage, DB corruption, rule/source contract unavailable | run-global | Run invalid | `NOT_EVALUABLE_*` / `BLOCKED_*` | No |
| `NOT_EVALUABLE` | umbrella state for incomplete technical/research/discovery evaluation | candidate or run | No valid investment verdict | explicit reason + lineage | No |

## Scope rules

### Candidate-scoped

One candidate's model/provider/schema/SEC error is recorded as `CANDIDATE_ENGINEERING_FAILURE` or the corresponding provider/evidence state. Other candidates continue. Candidate conservation must show every Discovery candidate ending in one of: PASS, REJECT, NEXT_STAGE, EVIDENCE_DEBT, SOURCE_EXHAUSTED, PROVIDER_FAILURE, ENGINEERING_FAILURE, or NOT_EVALUATED.

### Run-global

A dependency shared by the whole run may stop the run, but it must preserve the technical root cause. Examples include missing canonical scanner sources, unusable broad universe provider, corrupt database, or market core data required to determine current context. These states cannot be rewritten downstream as `NO_TRADE`.

## Shadow semantics

Shadow reporting must expose separately:

- pipeline health;
- investment conclusion;
- whether the conclusion is a clean evaluated no-trade state;
- root engineering/provider/evidence failures;
- broker write count.

Any `NOT_EVALUABLE_*` or `BLOCKED_*` root cause implies `investment_conclusion_is_clean_no_trade = false`.

## Examples

```text
Market core data missing
 -> PRE_DISCOVERY_FAILURE
 -> NOT_EVALUABLE_PRE_DISCOVERY
 -> clean_no_trade = false
```

```text
Candidate A model 400
Candidate B full evidence + valid Step18 A-
 -> A = CANDIDATE_SCOPED_FAILURE
 -> B continues
 -> run is not poisoned by A
```

```text
Candidate fails verified toxic convertible hard gate
 -> INVESTMENT_REJECT
 -> candidate EXCLUDE
 -> this is an investment conclusion, not an engineering failure
```

```text
Canonical V8 scanner 08 file missing/hash mismatch
 -> RUN_GLOBAL INPUT INTEGRITY FAILURE
 -> NOT_EVALUABLE_INPUT_INTEGRITY
 -> SCANNER_EXECUTED = false
```

## Forbidden coercions

The following mappings are protocol violations:

- `PROVIDER_FAILURE -> NO_TRADE`
- `MODEL_FAILURE -> EXCLUDE`
- `SCHEMA_FAILURE -> bearish fact`
- `STALE_DATA -> FAIL`
- `UNKNOWN -> FALSE`
- `PRE_DISCOVERY_FAILURE -> NO_QUALIFIED_CANDIDATE`
- `candidate A failure -> candidate B failure`
- `missing scanner receipt -> search exhausted`
