# 04 ROOT CAUSE GRAPH

## Primary root cause: distributed semantic authority

```text
MODEL / PROVIDER FACT
  |
  +-> stage-local fallback strings
  +-> legacy gate decisions
  +-> candidate failure tracker
  +-> conservation helper
  +-> search-stop helper
  +-> `_run_strict` wrapper outcomes
  +-> Shadow guards
  +-> PRE-A projection

Each branch can reinterpret the same fact differently.
```

## Root-cause chains

### RC-1 Failure semantics overload

`engineering exception -> synthetic audit payload -> audit_recommendation=AUDIT_EVIDENCE_INCOMPLETE -> conservation string check -> REJECT`

The semantic domain changes from engineering failure to investment conclusion without an evidence-backed transition.

### RC-2 Search closure overload

`operational probe count -> explicit_ceiling=True -> exhausted=True -> search_stop_allowed -> terminal outcome`

A resource/budget fact is converted into an epistemic/source-completeness fact.

### RC-3 Terminal authority fragmentation

Several layers modify run outcomes after `super()._run_strict()`. Even if every local patch is reasonable, composition order can decide the final meaning. There is no immutable proof object from which the terminal state is derived once.

### RC-4 Firewall fragmentation

Blindness rules are spread across mutable module-level sets and follow-up patches. The present implementation fixes known keys but does not make contamination impossible by construction.

### RC-5 Test-truth inversion

Some tests assert historical behavior instead of canonical semantics. A green test suite therefore does not imply the state machine is correct.

## Architectural response

1. Introduce one canonical semantics module with domain-typed states and derivation functions.
2. Introduce explicit `SourceExhaustionProof` and `RunEvaluationProof` receipts.
3. Make candidate conservation consume validated stage facts, not free-form audit strings.
4. Make final run terminal outcome derive from `RunEvaluationProof` exactly once at the outer sentinel boundary.
5. Make all certification stages use one immutable forbidden-metadata registry.
6. Expand tests from helper assertions to mutation-resistant end-to-end state conservation.
