from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from stock_agent.adapters import TossMarketDataProvider, TossPortfolioProvider


class TossPersistenceHardeningTests(unittest.TestCase):
    def test_accounts_artifact_never_persists_account_number_or_raw_payload(self):
        provider = TossMarketDataProvider("client-id", "client-secret")
        payload = {
            "result": [
                {
                    "accountSeq": 7,
                    "accountType": "GENERAL",
                    "accountNo": "123-456-789",
                    "accessToken": "sensitive-access-token",
                    "clientSecret": "sensitive-client-secret",
                }
            ],
            "accessToken": "top-level-sensitive-token",
        }
        with patch.object(provider, "_request_json", return_value=payload):
            artifact = provider.fetch_accounts()

        serialized = json.dumps(artifact.payload, sort_keys=True)
        self.assertEqual(artifact.payload["accounts"], [{"account_seq": 7, "account_type": "GENERAL"}])
        self.assertNotIn("account_no", serialized)
        self.assertNotIn("123-456-789", serialized)
        self.assertNotIn("sensitive-access-token", serialized)
        self.assertNotIn("sensitive-client-secret", serialized)
        self.assertNotIn("top-level-sensitive-token", serialized)
        self.assertNotIn("source", artifact.payload)
        self.assertEqual(artifact.payload["source_endpoint"], "/api/v1/accounts")

    def test_portfolio_artifact_persists_only_normalized_economics(self):
        provider = TossMarketDataProvider("client-id", "client-secret")
        holdings = {
            "result": {
                "items": [
                    {
                        "symbol": "HLIT",
                        "quantity": "6",
                        "averagePurchasePrice": "13.50",
                        "asOf": "2026-08-20T08:00:00Z",
                        "accountNo": "123-456-789",
                        "accessToken": "holding-sensitive-token",
                    }
                ],
                "marketValue": {"amount": {"krw": "1000"}},
            },
            "clientSecret": "holding-client-secret",
        }
        buying = {
            "result": {
                "cashBuyingPower": "2000",
                "accountNo": "123-456-789",
            },
            "accessToken": "buying-sensitive-token",
        }

        def fake_request(method, path, query=None, form=None, headers=None, auth=True):
            if path == "/api/v1/holdings":
                return holdings
            if path == "/api/v1/buying-power":
                return buying
            raise AssertionError(path)

        with patch.object(provider, "_request_json", side_effect=fake_request):
            artifact = TossPortfolioProvider(provider, account_seq=7).fetch_snapshot({"currency": "KRW"})

        serialized = json.dumps(artifact.payload, sort_keys=True)
        self.assertEqual(artifact.payload["account_seq"], 7)
        self.assertEqual(artifact.payload["cash"], 2000.0)
        self.assertEqual(artifact.payload["total_equity"], 3000.0)
        self.assertEqual(artifact.payload["holding_count"], 1)
        self.assertEqual(
            artifact.payload["positions"],
            [{"subject_id": "HLIT", "shares": 6, "average_cost": 13.5, "as_of": "2026-08-20T08:00:00Z"}],
        )
        for forbidden in (
            "123-456-789",
            "holding-sensitive-token",
            "holding-client-secret",
            "buying-sensitive-token",
            "accountNo",
            "accessToken",
            "clientSecret",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("source", artifact.payload)
        self.assertEqual(
            artifact.payload["source_endpoints"],
            ["/api/v1/holdings", "/api/v1/buying-power"],
        )


if __name__ == "__main__":
    unittest.main()

