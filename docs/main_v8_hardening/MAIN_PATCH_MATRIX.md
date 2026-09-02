# MAIN_PATCH_MATRIX

| Problem | Files Changed | Fix | Regression Risk | Tests | Status |
|---|---|---|---|---|---|
| Pseudo 02~14 execution | `v8_main_discovery_coach.py`, `v8_main_discovery_integrity.py`, `v8_main_scanner_contract_v12.py`, `v8_main_discovery_post_v11.py` | Real per-scanner/per-round model stages; schema and strategy dimensions; authoritative receipts; final scout only aggregates | LLM cost/latency; dynamic schema incompatibility | discovery integrity + T1~T16 | IMPLEMENTED |
| Canonical source absent/mismatched | `v8_main_source_fidelity.py`, `v8_main_source_gate.py` | Exact bytes + SHA required; no paraphrase fallback | Fresh clone cannot run live until source package available | source fidelity hostile tests | IMPLEMENTED / LIVE SOURCE REQUIRED |
| Step18 authority gap | `v8_next_certification*.py`, `v8_next_runtime.py`, `v8_next_successor.py` | Step15→16→17→17.5→18→20; Python final grade arithmetic; Step20 pure validator | Legacy fixtures can lose inflated A/A- | NEXT certification tests | IMPLEMENTED |
| Grade/quota anchoring | `v8_primary.py`, `v8_grade_quota_firewall.py`, `discovery_recall_firewall_v15.py` | Intrinsic blind scrub of Discovery metadata/quota/prior grade/action; remove active five-name grade target | Downstream code expecting legacy telemetry | T8/T16 + quota firewall | IMPLEMENTED |
| Candidate failure poisoning | `hunt_integrity_v18.py` | Candidate-scoped engineering failure sentinel/conservation; run-global failures remain global | Exception taxonomy omissions | hostile provider/model/schema/SEC | IMPLEMENTED |
| Evidence first-N bias | `hunt_integrity_v18.py`, `hunt_integrity_v181.py` | Authority/adverse selection; late-source sampling; omitted manifests; canonical evidence preserved | Wire budget pressure | late bullish/bearish hostile cases | IMPLEMENTED |
| Evidence independence by URL count | `v8_evidence_origin_v19.py` | Python materializes evidence-source children and origin IDs; Step16 validates declarations | Conservative collapse of ambiguous sources | Step16 source-backed runtime test | IMPLEMENTED |
| Technical UNKNOWN disappears | `v8_main_recall_conservation.py` | Evidence debt + Secondary persistence; no Stage/Execution waiver | More unresolved debt can prevent clean stop | recall conservation | IMPLEMENTED |
| Top-down partial context kills bottom-up | `v8_market_discovery_admission.py` | Discovery admission separated from strict execution; canonical strict receipt preserved | Core/non-core classification must remain conservative | market admission tests | IMPLEMENTED |
| Search-stop premature | `v8_main_discovery_integrity.py`, `v8_main_discovery_post_v11.py` | 75-name system rounds, marginal yield, 13-scanner family completion, Secondary/Near-Miss debt, sentinel/exhaustion | More live model calls | T4/T5/T12/T13 + low-yield family tests | IMPLEMENTED |
| Secondary ephemeral | `v8_main_discovery_integrity.py`, `v8_main_discovery_post_v11.py` | Persistent SQLite queue, recheck trigger, expiry→WATCH, no PRE-A authority | Schema migration/queue growth | T15 | IMPLEMENTED |
| PRE-A authority contamination | `pre_a_source_v2.py`, `pre_a_sidecar.py`, `daily_with_pre_a.py` | Authoritative valid B+ cert only; read-only; no grade/action/broker authority | Legacy reports may be rejected | PRE-A tests | IMPLEMENTED |
| Engineering failure => clean NO_TRADE | `v8_next_terminal_lineage.py`, `shadow_non_evaluable_guard.py`, `shadow_health_v19.py` | Preserve root failure; classify non-evaluable; transport health decoupled from research schema | Historical Shadow expectations change | failure semantics + Shadow tests | IMPLEMENTED |
| Import-order runtime drift | `bootstrap.py`, `production.py`, `__main__.py`, `__init__.py` | One production composition root; side-effect-light root import; production entrypoints use bootstrap | MRO remains incremental/complex | composition assertions | IMPLEMENTED |
| Allocation poisoning by unselected candidate | `hunt_integrity_v182.py` | Selected-candidate-only execution veto | Existing broad failure assumptions change | allocation isolation | IMPLEMENTED |

## No-relaxation statement

No patch lowers A/A- thresholds, turns UNKNOWN into PASS, promotes B+, waives Stage/SEC/liquidity rules, enables broker writes, or installs the Python heuristic Discovery Recall runtime as a replacement for MAIN scanners.
