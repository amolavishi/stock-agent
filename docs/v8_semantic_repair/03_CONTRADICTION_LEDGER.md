# 03 CONTRADICTION LEDGER

| ID | Producer | Invalid Transition | Downstream Damage | Severity | Root Cause |
|---|---|---|---|---|---|
| C-001 | Audit engineering fallback | `AUDIT_EVIDENCE_INCOMPLETE + engineering_failure -> REJECT` | False investment rejection, candidate disappearance from evidence-debt accounting | P0 | Audit recommendation string is overloaded as both failure and thesis conclusion |
| C-002 | Pre-live search exhaustion helper | `ADV_PROBED >= 1000 -> SOURCE_EXHAUSTED` | Premature search stop / false clean NO_TRADE while unresolved securities remain | P0 | Operational budget threshold is incorrectly treated as source-end proof |
| C-003 | Existing test suite | `RAW 3000 / PROBED 1000 / UNRESOLVED 2000 -> exhausted=True` expected | CI certifies the wrong Recall semantics | P0 | Test encodes implementation history instead of canonical meaning |
| C-004 | Multiple `_run_strict` wrappers | outcome strings are repeatedly reinterpreted | Wrapper-order-dependent terminal state and future false NO_TRADE regressions | P1 | No single authoritative run-evaluation proof/terminal owner |
| C-005 | V8 semantic patches | forbidden certification metadata stored in distributed mutable sets | New PRE-A/discovery/quota synonym can leak if one module is missed | P1 | Firewall authority distributed across modules/import order |
| C-006 | Package root vs production module | same `ProductionStockAgent` name can mean base/uncomposed vs composed runtime | library/CLI composition drift | P1 | Production identity not singular at public API boundary |
| C-007 | Scenario matrix | helper-level semantics cover only a subset of requested S01-S24 | State-machine bugs can survive green CI | P1 | Unit assertions do not prove end-to-end conservation |
| C-008 | Candidate conservation | `SOURCE_EXHAUSTED` included as candidate incomplete state | Search/source state can be confused with candidate investment/evaluation state | P1 | Run/search semantics and candidate semantics share one vocabulary without domain typing |
| C-009 | Step20 + stored Step18 | valid grade may coexist with RETURN route | downstream readers can accidentally treat historical grade as qualifiable | P1 | Grade persistence and current qualification state not explicitly separated |
| C-010 | Scanner/coverage wrappers | fallback/data-block results traverse multiple schema generations | execution truth may depend on outer validation layer | P1 | Scanner execution/call/validation/coverage concepts are not represented by one receipt type |

## Root invariant under attack

The common defect is distributed authority: the same fact is interpreted independently by several wrappers. Repair must centralize semantic derivation while preserving existing evidence and grade thresholds.
