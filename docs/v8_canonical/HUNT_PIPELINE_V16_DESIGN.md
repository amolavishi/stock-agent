# V8 HUNT Pipeline V1.6 — Evidence-First Research Flow

## Root causes from RUN-009 / RUN-010

1. `CatalystGate` is executed before `DEEP_RESEARCH`, and a non-PASS decision immediately terminates the candidate. This makes catalyst evidence a research admission requirement instead of an output of research.
2. The v1.5 research bundle improved RSS breadth but still treats issuer IR + media snippets as the practical source boundary. It does not guarantee full-article depth or SEC fallback before declaring source exhaustion.
3. A malformed secondary-media URL can abort the whole secondary lane instead of being skipped item-by-item.
4. Evidence debt is not a persisted lifecycle. UNKNOWN/MISSING can remain terminal rather than moving through `EVIDENCE_DEBT -> REFRESH -> RETRY -> SOURCE_EXHAUSTED`.
5. Funnel telemetry does not distinguish stage entered/pass/fail/not-evaluated/refreshed at candidate level.
6. `prescreen_passed > 0 && deep_research == 0` can still look like a normal no-candidate result instead of an engineering starvation incident.

## V1.6 invariants

- V8 remains canonical. Discovery priority never grants Research Grade, PRE-A readiness, action, or size.
- Existing A/A-, Catalyst, SEC, expectation-gap, audit and Grade Firewall requirements remain fail-closed for qualification.
- Catalyst is no longer a pre-research admission veto. An initial missing/insufficient catalyst is explicit evidence debt; the existing strict `CatalystGate` is rerun after Deep Research and Full SEC evidence have been acquired. Only the latest strict post-research receipt can satisfy qualification.
- Research acquisition attempts issuer IR, secondary media, full article text where safely fetchable, and SEC 8-K/10-Q/10-K fallback. Optional configured GOVERNMENT/REGULATOR/CUSTOMER/INDUSTRY URLs are supported through the research query.
- If no grounded catalyst is found on the first pass, acquisition performs a broader retry before source exhaustion.
- Invalid individual media links are skipped and attributed; they do not crash the whole secondary lane.
- Candidate-level evidence lifecycle and stage telemetry are persisted.
- Pipeline starvation is an engineering incident and cannot be presented as a clean opportunity conclusion.
- Broker writes remain zero in HUNT. Human final decision and existing execution authority boundaries remain unchanged.
