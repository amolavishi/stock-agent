# V8.4 MAIN Structural Contradiction Audit — 2026-09-03

## Scope
Adversarial review of the production MAIN chain after RUN-002 exposed missing canonical scanner sources. This audit treats the V8.4 Drive package as the active Discovery source authority while preserving V8 NEXT certification authority and the existing MAIN orchestration.

## Confirmed structural contradictions and repairs

### C1 — Source semantics vs runtime schema
V8.4 defines cross-scanner `SIGNAL_STRENGTH=HIGH|MEDIUM|LOW` and permits `EARLY_TRAJECTORY`. The existing MAIN scanner schema still used `STRONG|MODERATE|WEAK|NONE|UNKNOWN` and omitted `EARLY_TRAJECTORY`.

Repair: production scanner and coverage-ledger schemas are patched after all legacy/v20 schema extensions to use `HIGH|MEDIUM|LOW|UNKNOWN`, with scanner-level `EARLY_TRAJECTORY` preserved.

### C2 — Schema repair alone would still corrupt search-stop metrics
Existing round metrics counted only `STRONG|MODERATE|WEAK`, so valid V8.4 HIGH/MEDIUM/LOW signals could be recorded as zero new signal and incorrectly support low-yield/search-stop conclusions.

Repair: production round metrics recompute signal IDs/counts using V8.4 vocabulary. Search-stop consumes those corrected metrics.

### C3 — One scanner engineering failure could abort later scanners
The round executor called the underlying provider/runtime without per-round engineering isolation. A schema/provider/transport exception could therefore terminate the remaining 02~14 work.

Repair: scanner-round engineering failures become explicit `DATA_BLOCKED` non-assessments with `screened_count=0`, per-ticker DATA_BLOCK coverage rows and `source_exhaustion=false`. Remaining rounds/scanners continue. Global V8 source-integrity failures are deliberately not isolated and remain run-global fail-closed failures.

### C4 — Two source-identity authorities
Legacy integrity preparation carried a hard-coded Scanner-08 SHA repair while the active V8.4 package has a different exact raw-byte source identity.

Repair: bootstrap applies the V8.4 source lock after legacy preparation and before runtime schema/prompt registration. Production composition/tests assert every scanner identity equals the V8.4 lock. The old value is only a frozen pre-lock compatibility detail and has no production authority.

### C5 — FULL universe claim could exceed proof
V8.4 allows `FULL_STRATEGY_UNIVERSE_SCAN` only with authoritative listing coverage, identity reconciliation, security-type classification, price/market-cap/20D-MDV reconciliation, unresolved-eligibility accounting and count reconciliation.

Repair: production records FULL only when an explicit Python-validated universe manifest proves every required condition and material unresolved eligibility is zero. Otherwise the runtime records BOUNDED or PARTIAL scope; broad count alone cannot create FULL status.

### C6 — EARLY_TRAJECTORY vs legacy final aggregator
Scanner-level V8.4 can produce `EARLY_TRAJECTORY`, while the legacy final `workflow.stock_scout` output schema is narrower.

Repair: `EARLY_TRAJECTORY` remains authoritative in scanner receipts. At the final legacy aggregation boundary it may map to WATCH_STAGE0, never silently to EXCLUDE. A HIGH-research-value EARLY_TRAJECTORY omitted from final output becomes explicit unresolved search debt and blocks a clean search-stop/NO_TRADE conclusion.

### C7 — Fresh-checkout source integrity
RUN-002 reached a large eligible/discovered universe but the checkout lacked canonical 02~14 files.

Repair: the repository now vendors exact V8.4 raw-byte sources under `prompts/v8_4/`, plus common contract and canonical universe rules. `V8_4_DISCOVERY_SOURCE_MANIFEST.json` locks SHA-256 and byte count. `.gitattributes` disables EOL conversion for these files so Windows cannot silently mutate canonical bytes.

## Authority invariants retained
- MAIN remains the sole final Discovery owner.
- No Python heuristic scanner routing authority is introduced.
- Discovery never writes Research Grade, PRE-A status or execution action.
- V8 NEXT Step18 remains the only Research Grade writer; Step20 remains validator-only.
- Engineering failure is not investment rejection and cannot become clean NO_TRADE.
- UNKNOWN remains UNKNOWN.
- `broker_write=0` and human final decision remain unchanged.

## Acceptance equation
`V8.4 SOURCE LOCKED = SOURCE LOADED = SCANNER EXECUTED/RECEIPTED = COVERAGE PROVED = SEARCH STOP VALIDATED`, while `Discovery != Certification` remains enforced.
