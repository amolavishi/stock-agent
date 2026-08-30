from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

from stock_agent.adapters import FilesystemObsidianProjector, TossMarketDataProvider, TossPortfolioProvider


class _Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps(self.payload).encode()


class ProviderContractTests(unittest.TestCase):
    def test_toss_oauth_and_verified_paths_are_read_only_and_cached(self):
        calls = []
        def fake_open(request, timeout=0):
            calls.append(request)
            if request.full_url.endswith("/oauth2/token"):
                return _Response({"access_token": "token", "token_type": "Bearer", "expires_in": 3600})
            if "/prices?" in request.full_url:
                return _Response({"result": [{"symbol": "AAPL", "lastPrice": "1", "timestamp": "2026-01-01T00:00:00Z", "currency": "USD"}]})
            raise AssertionError(request.full_url)
        with patch("urllib.request.urlopen", side_effect=fake_open):
            provider = TossMarketDataProvider("id", "secret", min_interval=0)
            first = provider.fetch_prices(["AAPL"]); second = provider.fetch_prices(["AAPL"])
        self.assertEqual(first.artifact_type, "TOSS_PRICES"); self.assertEqual(second.artifact_type, "TOSS_PRICES")
        self.assertEqual(sum(1 for request in calls if request.full_url.endswith("/oauth2/token")), 1)
        self.assertTrue(all(request.get_method() == "GET" for request in calls if "/api/v1/" in request.full_url))

    def test_toss_candle_without_source_timestamp_is_not_freshened_at_ingest(self):
        provider = TossMarketDataProvider("id", "secret", min_interval=0)
        with patch.object(provider, "_request_json", return_value={"result": [{"close": 1.0}, {"close": 1.1}]}):
            artifact = provider.fetch_candles("AAPL")
        self.assertIsNone(artifact.source_observed_at)

    def test_toss_rejects_private_base_url_before_network(self):
        with self.assertRaises(Exception):
            TossMarketDataProvider("id", "secret", base_url="http://127.0.0.1:8080")

    def test_toss_portfolio_requires_account_and_is_read_only(self):
        provider = TossMarketDataProvider("id", "secret", min_interval=0)
        with self.assertRaises(Exception): TossPortfolioProvider(provider).fetch_snapshot({})

    def test_toss_account_seq_is_discovered_from_documented_accounts_endpoint(self):
        calls = []

        def fake_open(request, timeout=0):
            calls.append(request)
            if request.full_url.endswith("/oauth2/token"):
                return _Response({"access_token": "token", "token_type": "Bearer", "expires_in": 3600})
            if request.full_url.endswith("/api/v1/accounts"):
                return _Response({"result": [{"accountNo": "12345678901", "accountSeq": 7, "accountType": "BROKERAGE"}]})
            if "/api/v1/holdings" in request.full_url:
                self.assertEqual(request.headers.get("X-tossinvest-account"), "7")
                return _Response({"result": {"items": [], "marketValue": {"amount": {"krw": 0}}}})
            if "/api/v1/buying-power" in request.full_url:
                self.assertEqual(request.headers.get("X-tossinvest-account"), "7")
                return _Response({"result": {"cashBuyingPower": 1000}})
            raise AssertionError(request.full_url)

        with patch("urllib.request.urlopen", side_effect=fake_open):
            provider = TossMarketDataProvider("id", "secret", min_interval=0)
            portfolio = TossPortfolioProvider(provider)
            accounts = portfolio.discover_accounts()
            snapshot = portfolio.fetch_snapshot({})
        self.assertEqual(accounts, [{"account_seq": 7, "account_type": "BROKERAGE"}])
        self.assertNotIn("12345678901", json.dumps(accounts, sort_keys=True))
        self.assertEqual(snapshot.payload["account_seq"], 7)
        self.assertTrue(all("/api/v1/orders" not in request.full_url for request in calls))

    def test_toss_403_diagnostic_is_structured_and_secret_free(self):
        provider = TossMarketDataProvider("client-id", "client-secret", min_interval=0, max_retries=0)
        error = urllib.error.HTTPError(
            "https://openapi.tossinvest.com/api/v1/accounts", 403, "forbidden", {}, None
        )
        error.read = lambda: json.dumps({
            "code": "ACCOUNT_IP_DENIED",
            "message": "Bearer super-secret-token client-secret",
        }).encode("utf-8")
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(Exception) as raised:
                provider._request_json("GET", "/api/v1/accounts", auth=False,
                                       headers={"X-Tossinvest-Account": "7"})
        self.assertIn("403", str(raised.exception))
        diagnostic = provider.last_error_diagnostic
        self.assertEqual(diagnostic["status"], 403)
        self.assertEqual(diagnostic["error_code"], "ACCOUNT_IP_DENIED")
        self.assertFalse(diagnostic["authorization_attached"])
        self.assertTrue(diagnostic["account_header_attached"])
        self.assertNotIn("super-secret-token", json.dumps(diagnostic))
        self.assertNotIn("client-secret", json.dumps(diagnostic))

    def test_obsidian_projection_is_idempotent_and_read_back_verifiable(self):
        with tempfile.TemporaryDirectory() as root:
            projector = FilesystemObsidianProjector(root); document = {"outcome": "QUALIFIED_CANDIDATE_POOL"}
            path1 = projector.project("run-1", "Run Summary", document)
            path2 = projector.project("run-1", "Run Summary", document)
            self.assertEqual(path1, path2); self.assertTrue(projector.verify("run-1", "Run Summary", document))
            self.assertEqual(projector.read("run-1", "Run Summary"), path1.read_text(encoding="utf-8"))


if __name__ == "__main__": unittest.main()
