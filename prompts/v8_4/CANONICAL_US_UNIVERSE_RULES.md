# Canonical Strategy Universe Rules — V8.4

The canonical scope is `FULL_STRATEGY_UNIVERSE_SCAN`, not “full US market”. It covers the full strategy-eligible US common-equity universe after the project’s security-type, price, market-cap and liquidity rules.

## Scope codes
- `FULL_STRATEGY_UNIVERSE_SCAN`
- `BOUNDED_STRATEGY_UNIVERSE_SCAN`
- `PARTIAL_STRATEGY_UNIVERSE_SCAN`

`FULL_STRATEGY_UNIVERSE_SCAN` requires: authoritative listing-source coverage, identity reconciliation, security-type classification, price/market-cap/20D median-dollar-volume filters, explicit unresolved eligibility, and count reconciliation. Russell/IWM/ETF/top-N lists are breadth sources, never the canonical universe.

Every canonical eligible ticker receives a universal cheap screen and scanner eligibility routing. Each scanner then scans only its reproducible eligible subset. Full scope does not require every ticker to receive every scanner’s deep logic.

The universe artifact must validate against `SCHEMAS/UNIVERSE_MANIFEST_SCHEMA.json`. `eligibility_status=UNRESOLVED` is neither PASS nor FAIL. A material unresolved identity/eligibility population prevents the FULL scope claim.
