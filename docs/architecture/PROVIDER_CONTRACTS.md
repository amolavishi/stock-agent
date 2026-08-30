# Provider Contracts

The repository currently has no Toss, SEC, Obsidian or live DeepSeek adapter.
The following contracts are design boundaries, not claims about unverified
external capabilities.

## MarketDataProvider

```python
class MarketDataProvider(Protocol):
    def quote(self, request: QuoteRequest) -> ProviderResult[RawQuote]: ...
    def ohlcv(self, request: OHLCVRequest) -> ProviderResult[RawOHLCV]: ...
    def market_snapshot(self, request: MarketSnapshotRequest) -> ProviderResult[RawMarketSnapshot]: ...
```

The Toss implementation may implement only methods confirmed by its official
contract. Unsupported data returns an explicit capability error; it is not
filled from web search or LLM memory. Raw request/response hashes, provider,
retrieved time, as-of, rate-limit and retry lineage are stored.

## SECProvider

```python
resolve_identity(ticker_or_cik) -> SecurityIdentity
submissions(cik) -> RawSubmissions
company_facts(cik) -> RawCompanyFacts
filing(accession_or_type) -> RawFiling
```

Normalization must preserve CIK, accession number, filing type, filed/report
dates, source URL/reference, raw hash, extractor version and freshness. Missing
or ambiguous identity blocks capital/forensic conclusions; it never means
“no dilution”. SEC rate limits use bounded backoff and explicit unknown outcome.

## LLMProvider / DeepSeek

`providers.py:12-98` is the current seed interface. vNext expands it with typed
`ProviderRequest`, `ProviderResponse`, timeout, model/profile, request ID,
retry class, token/cost accounting and redacted error. `DeepSeekProvider` is an
adapter selected by ModelRouter and receives secrets only through typed config.
Vendor transport details must remain inside that adapter.

## ObsidianProjectionProvider

```python
project(snapshot: VerifiedProjection) -> ProjectionReceipt
```

It writes only to a configured vault root through temp-file + atomic rename,
records content hash, and can fail/retry independently of SQLite.

## Recorded providers

Fake/Recorded providers are test adapters only. They may return raw market/SEC
artifacts or model payloads, but cannot supply `GateDecision`, `FinalAction`,
authoritative stage eligibility, shares or capital commitment as a trusted
conclusion.
