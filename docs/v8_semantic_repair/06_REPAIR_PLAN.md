# 06 REPAIR PLAN

## P0-1 Canonical failure semantics

- Move audit/failure interpretation into one canonical semantic derivation function.
- `AUDIT_EVIDENCE_INCOMPLETE + engineering_failure -> ENGINEERING_FAILURE`.
- `AUDIT_EVIDENCE_INCOMPLETE + unresolved evidence -> EVIDENCE_DEBT/NOT_EVALUATED`.
- `REJECT` requires a verified gate/audit/certification investment-failure reason.
- Add regression and mutation tests proving engineering/evidence failures cannot become reject.

## P0-2 Source exhaustion separation

- Replace `ADV_PROBED >= MIN_OPERATIONAL_PROBE -> exhausted` with an explicit proof function.
- Preserve operational thresholds only as coverage/budget metadata.
- `SOURCE_EXHAUSTED` requires zero unresolved denominator debt plus authoritative traversal/reconciliation evidence.
- If the current provider cannot prove source end, return `source_exhausted=False` and `search_debt=True` rather than inventing completeness.
- Reverse the existing wrong-semantics test for 3000/1000/2000.

## P0-3 Candidate conservation

- Ensure every discovered candidate has exactly one terminal/evaluation state.
- Separate search-domain states from candidate-domain states; source exhaustion should be run/search evidence, not a candidate investment state.
- Preserve B+/B/EXCLUDE as completed conclusions when Step20 PASSes.

## P0-4 RunEvaluationProof

- Add a deterministic proof builder over source integrity, scanner receipts, coverage, sentinel, secondary debt, conservation, Step20 debt, and source/universe closure.
- Persist the proof with a canonical hash.
- Derive clean `NO_TRADE` only from a passing proof.

## P1-1 Terminal single ownership

- Do not add a new outer wrapper.
- Patch the final sentinel/semantic owner in place to derive the terminal result once from `RunEvaluationProof`.
- Earlier wrappers may collect evidence/debt but must not authorize clean NO_TRADE independently.

## P1-2 Certification firewall unification

- Create one canonical forbidden-key registry used by `v8_blind_packet` and all Step16-18 packet builders/validators.
- Add prefix/synonym protection for Discovery, PRE-A, quota, prior-grade and execution metadata.
- Add hash-invariance mutation tests.

## P1-3 Step20 semantics

- A RETURN route blocks current qualification regardless of stored Step18 grade.
- Keep the Step18 receipt for audit lineage, but expose current evaluation as NOT_EVALUATED/return debt until resolved.

## P1-4 Production determinism

- Preserve `stock_agent.production.ProductionStockAgent` as canonical composed runtime.
- Add explicit composition fingerprint tests across CLI/module/library import orders.
- Avoid a broad rewrite in this hardening branch unless deterministic installation cannot otherwise be proven.

## P1-5 Shadow/PRE-A

- Project authoritative terminal/conservation state; do not infer from free-form outcome strings.
- PRE-A only accepts valid Step18 B+ + Step20 PASS + no grade conflict.

## P1-6/P1-7 Tests

- Expand S01-S24 into runtime/state-machine scenarios.
- Add hostile mutations for failure->reject, false exhaustion, unconditional NO_TRADE, contamination, scanner fallback execution, duplicate-origin evidence, Step20 RETURN qualification and sentinel incompleteness.
- Classify legacy tests as VALID / WEAK / WRONG-SEMANTICS.

## Completion gate

No PASS until:
- no known P0 semantic contradiction,
- false exhaustion and false clean NO_TRADE are impossible under tests,
- candidate conservation is complete,
- Step18/20/PRE-A authority is preserved,
- full regression + mutation suite passes,
- broker writes remain zero.
