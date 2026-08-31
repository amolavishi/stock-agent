import tempfile
import unittest
from pathlib import Path

from stock_agent.pre_a_sidecar import (
    PreASidecarError,
    generate_pre_a_report,
    validate_sidecar_payload,
)
from stock_agent.providers import FakeProvider


def _candidate(**overrides):
    row = {
        "ticker": "ABC",
        "source_grade": "B+",
        "promotion_readiness": "PRE_A_HIGH",
        "a_trajectory": "HIGH",
        "fundamental_direction": "VERIFIED",
        "expectation_gap": "PARTIAL_VERIFIED",
        "price_lag": "PRESENT",
        "catalyst_window": "NEAR",
        "missing_gates": [
            {"gate": "customer acceptance", "severity": "MAJOR", "reason": "one source-reported gate remains"}
        ],
        "promotion_triggers": ["customer acceptance confirmed"],
        "demotion_triggers": ["acceptance delayed beyond the stated window"],
        "expiry_or_recheck": "recheck within 4 weeks",
        "why": "The source report describes B+ and one remaining verification gate.",
        "source_limitations": [],
    }
    row.update(overrides)
    return row


def _payload(candidate=None):
    return {
        "analysis_status": "COMPLETE",
        "candidates": [_candidate() if candidate is None else candidate],
        "global_notes": ["sidecar only"],
    }


class PreASidecarTests(unittest.TestCase):
    def test_sidecar_writes_separate_report_without_mutating_primary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "shadow_runs" / "RUN-1" / "DAILY_REPORT.md"
            source.parent.mkdir(parents=True)
            original = "# Daily Report\n\nCandidate `ABC`\nCurrent Research Grade: B+\nFundamental improvement verified.\n"
            source.write_text(original, encoding="utf-8")
            output = root / "pre_a_reports" / "RUN-1" / "PRE_A_REPORT.md"
            provider = FakeProvider(responder=lambda request: _payload())

            result = generate_pre_a_report(
                source,
                output,
                provider=provider,
                prompt_text="Use only the supplied source report.",
                reasoning_effort="high",
            )

            self.assertEqual(result, output.resolve())
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("NON-AUTHORITATIVE SIDECAR", rendered)
            self.assertIn("primary_mutation: `NO`", rendered)
            self.assertIn("broker_write_count: `0`", rendered)
            self.assertIn("ABC", rendered)

    def test_sidecar_refuses_to_overwrite_primary_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "DAILY_REPORT.md"
            source.write_text("Candidate `ABC`\nGrade B+", encoding="utf-8")
            provider = FakeProvider(responder=lambda request: _payload())

            with self.assertRaisesRegex(PreASidecarError, "must not overwrite"):
                generate_pre_a_report(
                    source,
                    source,
                    provider=provider,
                    prompt_text="policy",
                    reasoning_effort="high",
                )

    def test_sidecar_rejects_hallucinated_ticker(self):
        report = "Candidate `ABC`\nCurrent Research Grade: B+"
        payload = _payload(_candidate(ticker="XYZ"))

        with self.assertRaisesRegex(PreASidecarError, "hallucinated ticker"):
            validate_sidecar_payload(payload, report)

    def test_pre_a_readiness_requires_locally_supported_b_plus(self):
        report = "Candidate `ABC`\nCurrent Research Grade: A-"
        payload = _payload(_candidate(source_grade="B+"))

        with self.assertRaisesRegex(PreASidecarError, r"does not locally support B\+"):
            validate_sidecar_payload(payload, report)

    def test_pre_a_high_rejects_critical_gate(self):
        report = "Candidate `ABC`\nCurrent Research Grade: B+"
        payload = _payload(
            _candidate(
                missing_gates=[
                    {"gate": "financing viability", "severity": "CRITICAL", "reason": "unresolved"}
                ]
            )
        )

        with self.assertRaisesRegex(PreASidecarError, "CRITICAL"):
            validate_sidecar_payload(payload, report)

    def test_pre_a_high_rejects_more_than_two_open_gates(self):
        report = "Candidate `ABC`\nCurrent Research Grade: B+"
        payload = _payload(
            _candidate(
                missing_gates=[
                    {"gate": "g1", "severity": "MINOR", "reason": "r1"},
                    {"gate": "g2", "severity": "MODERATE", "reason": "r2"},
                    {"gate": "g3", "severity": "MINOR", "reason": "r3"},
                ]
            )
        )

        with self.assertRaisesRegex(PreASidecarError, "missing-gate cap"):
            validate_sidecar_payload(payload, report)


if __name__ == "__main__":
    unittest.main()
