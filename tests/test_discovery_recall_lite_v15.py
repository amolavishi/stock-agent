from __future__ import annotations

import json
import subprocess
import sys
import unittest

from stock_agent.discovery_recall_firewall_v15 import (
    DISCOVERY_RECALL_FIREWALL_VERSION,
    install_discovery_recall_firewall_v15,
)
from stock_agent.discovery_recall_lite_v15 import (
    DEFAULT_ADV_PROBE_TARGET,
    FORENSIC_AUDIT_SHA256,
    MIN_SIGNAL_COVERAGE,
    PREFERRED_SIGNAL_COVERAGE,
    SCANNERS,
    _default_sentinel,
    _evaluate,
    _scanner_receipt,
    _sentinel_sample,
    _sentinel_schema,
)
from stock_agent.discovery_recall_stop_bridge_v15 import DISCOVERY_RECALL_STOP_BRIDGE_VERSION
from stock_agent import v8_primary


class DiscoveryRecallLiteV15Tests(unittest.TestCase):
    def _rows(self, count: int = 207):
        rows = []
        technical = {}
        for index in range(count):
            sid = f"T{index:03d}"
            rows.append({
                "security_id": sid,
                "ticker": sid,
                "issuer_name": f"Generic Software Defense Company {index}",
                "market": "NASDAQ",
                "sector": "Technology",
                "industry": "Software",
                "price": 10.0,
                "market_cap": 1_000_000_000,
                "ipoyear": "2025" if index % 11 == 0 else None,
            })
            technical[sid] = {
                "last_price": 10.0,
                "sma_window": 9.8,
                "return_window": 0.04 if index % 3 else -0.03,
                "return_1": 0.03 if index % 5 == 0 else 0.005,
                "volatility_window": 0.22,
                "volume_ratio": 1.30 if index % 7 == 0 else 1.05,
            }
        return rows, technical

    def test_exact_02_14_scanner_set(self):
        self.assertEqual(sorted(SCANNERS), [f"{n:02d}" for n in range(2, 15)])

    def test_207_ticker_fixture_produces_complete_receipt_for_every_scanner(self):
        rows, technical = self._rows()
        for scanner_id in sorted(SCANNERS):
            receipt, evaluations, rounds = _scanner_receipt(scanner_id, rows, technical)
            self.assertEqual(receipt["status"], "SIGNAL_SCAN_COMPLETE", scanner_id)
            self.assertTrue(receipt["output_contract_complete"], scanner_id)
            self.assertEqual(receipt["evaluated_count"], 207, scanner_id)
            self.assertEqual(len({item["security_id"] for item in evaluations}), 207, scanner_id)
            self.assertEqual(len(rounds), 5, scanner_id)
            self.assertFalse(receipt["grade_authority"])
            self.assertEqual(receipt["execution_depth"], "LITE_SIGNAL_ROUTING_WITH_FULL_SECONDARY_VERIFICATION")

    def test_round_telemetry_contains_forensic_stop_inputs(self):
        rows, technical = self._rows(101)
        _, _, rounds = _scanner_receipt("07", rows, technical)
        self.assertEqual([item["new_unique_tickers"] for item in rounds], [50, 50, 1])
        required = {
            "signal_detected", "partial_signal", "secondary", "high_research_value",
            "deep_dive_now", "duplicate_saturation", "signal_detection_rate",
            "partial_signal_rate", "secondary_queue_rate", "high_research_value_rate",
            "independent_evidence_yield", "source_exhaustion",
        }
        self.assertTrue(required.issubset(rounds[0]))

    def test_medium_signal_high_research_value_reaches_secondary(self):
        row = {"security_id": "SMID", "ticker": "SMID", "issuer_name": "Generic", "market_cap": 1_000_000_000, "price": 10.0}
        tech = {"last_price": 10.0, "sma_window": 9.5, "return_window": 0.05, "return_1": 0.03, "volatility_window": 0.20, "volume_ratio": 1.30}
        item = _evaluate("07", row, tech)
        self.assertEqual(item["signal_strength"], "MODERATE")
        self.assertEqual(item["research_value"], "HIGH")
        self.assertEqual(item["disposition"], "DEEP_DIVE_SECONDARY")
        self.assertFalse(item["grade_authority"])

    def test_unknown_is_discovery_insufficient_not_hard_fail(self):
        row = {"security_id": "UNK", "ticker": "UNK", "issuer_name": "Generic", "market_cap": 1_000_000_000, "price": 10.0}
        item = _evaluate("12", row, {"last_price": 10.0, "sma_window": 10.0})
        self.assertEqual(item["disposition"], "DISCOVERY_INSUFFICIENT")
        self.assertNotIn(item["disposition"], {"STRUCTURAL_HARD_FAIL", "THESIS_HARD_FAIL"})

    def test_price_stage_mismatch_is_not_structural_rejection(self):
        row = {"security_id": "RUN", "ticker": "RUN", "issuer_name": "Generic", "market_cap": 1_000_000_000, "price": 20.0}
        tech = {"last_price": 20.0, "sma_window": 12.0, "return_window": 0.70, "return_1": 0.01, "volatility_window": 0.20, "volume_ratio": 1.4}
        item = _evaluate("07", row, tech)
        self.assertEqual(item["disposition"], "PRICE_STAGE_MISMATCH")

    def test_rejection_sentinel_is_grade_blind_and_covers_sample(self):
        rows, technical = self._rows(50)
        evaluations = []
        for scanner_id in sorted(SCANNERS):
            _, items, _ = _scanner_receipt(scanner_id, rows, technical)
            evaluations.extend(items)
        sample = _sentinel_sample(evaluations)
        sentinel = _default_sentinel(sample)
        self.assertLessEqual(len(sample), 30)
        self.assertEqual(len(sentinel["audits"]), len(sample))
        self.assertFalse(sentinel["grade_authority"])
        schema_text = json.dumps(_sentinel_schema(), sort_keys=True).lower()
        for forbidden in ("research_grade", "target_price", "position_size", "pre_a_status"):
            self.assertNotIn(forbidden, schema_text)

    def test_discovery_metadata_is_scrubbed_before_blind_certification(self):
        install_discovery_recall_firewall_v15()
        packet = {
            "ticker": "ABC",
            "facts": {"revenue": 100},
            "research_value": "HIGH",
            "signal_strength": "MODERATE",
            "scanner_id": "07",
            "secondary_queue": {"status": "OPEN"},
            "near_miss": True,
            "rejection_sentinel": {"finding": "OK"},
            "recommended_discovery_action": "DEEP_DIVE_SECONDARY",
            "research_grade": "A",
        }
        blinded = v8_primary.v8_blind_packet(packet)
        self.assertEqual(blinded, {"ticker": "ABC", "facts": {"revenue": 100}})

    def test_forensic_constants_are_not_weak_150_only_guard(self):
        self.assertEqual(MIN_SIGNAL_COVERAGE, 150)
        self.assertGreaterEqual(PREFERRED_SIGNAL_COVERAGE, 200)
        self.assertGreaterEqual(DEFAULT_ADV_PROBE_TARGET, 1000)
        self.assertEqual(FORENSIC_AUDIT_SHA256, "47494df8fd0464c3fb63c6f2a5facd7dd6296616bec635b6faebe15e4ddab616")
        self.assertEqual(DISCOVERY_RECALL_FIREWALL_VERSION, "V8_DISCOVERY_RECALL_FIREWALL_V1.5")
        self.assertEqual(DISCOVERY_RECALL_STOP_BRIDGE_VERSION, "V8_DISCOVERY_RECALL_STOP_BRIDGE_V1.5")

    def test_production_composition_installs_recall_runtime_in_subprocess(self):
        code = "import json; from stock_agent.bootstrap import production_composition; print(json.dumps(production_composition(), sort_keys=True))"
        completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        value = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(value["discovery_recall_lite_version"], "V8_DISCOVERY_RECALL_LITE_V1.5")
        self.assertEqual(value["discovery_recall_firewall_version"], "V8_DISCOVERY_RECALL_FIREWALL_V1.5")
        self.assertEqual(value["discovery_recall_stop_bridge_version"], "V8_DISCOVERY_RECALL_STOP_BRIDGE_V1.5")
        self.assertEqual(value["discovery_recall_forensic_audit_sha256"], FORENSIC_AUDIT_SHA256)
        mro = " ".join(value["mro"])
        self.assertIn("DiscoveryRecallLiteProductionStockAgent", mro)
        self.assertIn("DiscoveryRecallStopBridgeProductionStockAgent", mro)


if __name__ == "__main__":
    unittest.main()
