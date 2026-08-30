import unittest

from stock_agent.adapters import TossMarketDataProvider


class TossNestedCandleRegressionTests(unittest.TestCase):
    def test_verified_candle_route_handles_nested_result_candles(self):
        provider = TossMarketDataProvider(client_id="id", client_secret="secret")

        def fake_request(method, path, query=None, **kwargs):
            self.assertEqual(path, "/api/v1/candles")
            return {
                "result": {
                    "candles": [
                        {"close": "100", "timestamp": "2026-08-23T00:00:00Z"},
                        {"close": "101", "timestamp": "2026-08-23T00:01:00Z"},
                    ]
                }
            }

        provider._request_json = fake_request
        artifact = provider.fetch_market_context({"symbols": ["SPY", "QQQ"]})
        self.assertEqual(artifact.payload["normalization_status"], "COMPLETE")
        self.assertEqual(artifact.payload["breadth"], "BROAD")


if __name__ == "__main__":
    unittest.main()
