# MAIN_REGRESSION_RESULTS

## Validation target

- Base MAIN: `2c4620e3babbd550b2c137436af6e6dcb9071c78`
- Branch: `audit/main-v8-adversarial-hardening-20260902`
- PR: #27
- Last code-changing validation target before documentation-only commits: `2de7f53f3b64d00d973ebfe5239a0bb85df91276`

## CI commands / workflows

### Full matrix

`.github/workflows/stock-agent-ci.yml`

```text
Ubuntu latest / Python 3.11
Ubuntu latest / Python 3.12
Windows latest / Python 3.11
Windows latest / Python 3.12

python -m compileall -q stock_agent tests
python -m unittest discover -s tests -q
python STOCK_AGENT_OBSIDIAN_PROMPT_LIBRARY_V2_2/VALIDATION/validate_contracts.py
git diff --exit-code
```

### MAIN V8 hostile matrix

`.github/workflows/main-v8-adversarial-regression.yml`

The hostile suite includes source fidelity, real scanner/round contracts, T1~T16 false-negative injections, quota/blind firewall, Market Context admission, technical Evidence Debt, candidate failure isolation, Step18/Step20, PRE-A and Shadow semantics.

### Existing regression workflows

- `alpha-discovery-regression`
- `catalyst-acquisition-regression`
- `hunt-pipeline-v16-regression`
- `hunt-resilience-v17-regression`
- `v8-primary-regression`
- `shadow-sentinel-regression`

## Failure-driven validation history

### Earlier hostile/full CI failure

The new tests deliberately exposed:

1. T8/T16: Discovery/scanner/quota metadata was not intrinsically scrubbed by `v8_blind_packet`; behavior depended on bootstrap install order.
2. A synthetic Step18 runtime fixture expected Grade A while lacking Python-owned independent evidence-origin lineage.

Fixes:

- moved Discovery-only and grade-quota scrub keys into core `v8_primary.py` so blindness is import-order independent;
- removed active five-name A/A- supply target from Discovery contract;
- kept A/A- evidence-origin restriction intact and upgraded the fixture to materialize/cite a Python evidence origin rather than weakening the grade cap.

The first source-backed fixture revision itself failed because a nested fake agent method attempted `self.assertTrue`; candidate-failure isolation correctly converted that test bug into a candidate-scoped failure. The fixture was corrected without changing production thresholds.

## Current automated-validation state

At the time this document was created, the latest code SHA `2de7f53f3b64d00d973ebfe5239a0bb85df91276` had a fresh CI set queued/running. Previous code SHA `e008ad4de627a032a6cff8c590c305a0e908d8fc` had already reduced the dedicated hostile matrix to one fixture-only failure; T8/T16 were no longer failing.

**Do not interpret this section as final CI PASS.** Final acceptance requires the latest code-equivalent branch state to report green on both the full four-platform/python matrix and the dedicated MAIN V8 hostile matrix. This file must not turn PENDING into PASS by declaration.

## Live validation

Status: `LIVE_VALIDATION_REQUIRED`.

Reason: canonical V8 02~14 source files are represented by exact SHA/byte metadata in `SOURCE_MANIFEST.json`, and matching named files have been located in the user's File Library, but they are not safely available as exact bytes in the GitHub checkout through the current connector path. The runtime therefore correctly fails closed unless an exact source root/archive is supplied.

A valid live Shadow acceptance run must prove all of the following:

```text
broad U.S. universe nonzero
strategy-eligible unique >= 150
02..14 real provider/model calls
all scanner receipts validated
Secondary / Near-Miss ledger
research progression
Full SEC
Step15
Step16
Step17
Step17.5
Step18
Step20
Shadow non-evaluable semantics correct
broker_writes = 0
```

Fixture/CI PASS must never be labeled Live PASS.

## Acceptance state

- Static architecture hardening: IMPLEMENTED
- Hostile failure injection: IMPLEMENTED; final matrix confirmation pending at document creation
- Full cross-platform CI: final confirmation pending at document creation
- Canonical source-byte live execution: `LIVE_VALIDATION_REQUIRED`
- PR #27 merge: **BLOCKED / DO NOT MERGE** until final CI and live source-backed Shadow validation are satisfied.
