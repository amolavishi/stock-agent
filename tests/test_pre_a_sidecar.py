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
            {"gate": "customer acceptance", "severity": "MAJOR", "reason": "one structured gate remains"}
        ],
        "promotion_triggers": ["customer acceptance confirmed"],
        "demotion_triggers": ["acceptance delayed beyond the stated window"],
        "expiry_or_recheck": "recheck within 4 weeks",
        "why": "Structured PRIMARY state records B+ and one remaining verification gate.",
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


def _bundle(**candidate_overrides):
    source_candidate = {
        "ticker": "ABC",
        "source_grade": "B+",
        "grade_conflict": False,
        "decision": {"ticker": "ABC", "grade": "B+", "not_evaluated": False},
        "decision_hash": "d" * 64,
        "stages": {
            "CAPITAL_PRESCREEN_GATE": {
                "status": "SUCCEEDED",
                "result": {"decision": "PASS"},
                "dependency_hash": "c" * 64,
                "evidence_epoch": 1,
                "created_at": "2026-09-01T00:00:00Z",
            }
        },
    }
    source_candidate.update(candidate_overrides)
    return {
        "source_version": "PRE_A_STRUCTURED_SOURCE_V2",
        "authority": "NON_AUTHORITATIVE_READ_ONLY_PROJECTION",
        "primary_mutation": False,
        "broker_write_count": 0,
        "shadow_run": {"shadow_run_id": "RUN-1", "hunt_run_id": "HUNT-1"},
        "candidate_count": 1,
        "candidates": [source_candidate],
        "source_hash": "s" * 64,
    }


class PreASidecarTests(unittest.TestCase):
    def test_sidecar_writes_separate_report_without_mutating_primary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "shadow_runs" / "RUN-1" / "DAILY_REPORT.md"
            source.parent.mkdir(parents=True)
            original = "# Human report wording is provenance only\n\nABC can be described however the renderer wants.\n"
            source.write_text(original, encoding="utf-8")
            output = root / "pre_a_reports" / "RUN-1" / "PRE_A_REPORT.md"
            provider = FakeProvider(responder=lambda request: _payload())

            result = generate_pre_a_report(
                source,
                output,
                source_bundle=_bundle(),
                provider=provider,
                prompt_text="Use only STRUCTURED_PRIMARY_SOURCE.",
                reasoning_effort="high",
            )

            self.assertEqual(result, output.resolve())
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            rendered = output.read_text(encoding="utf-8")
            self.assertIn("NON-AUTHORITATIVE SIDECAR", rendered)
            self.assertIn("primary_mutation: `NO`", rendered)
            self.assertIn("broker_write_count: `0`", rendered)
            self.assertIn("markdown_semantic_authority: `NO`", rendered)
            self.assertIn("ABC", rendered)

    def test_markdown_rewording_cannot_change_model_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "a" / "DAILY_REPORT.md"
            second = root / "b" / "DAILY_REPORT.md"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("ABC Grade B+ and promotional prose", encoding="utf-8")
            second.write_text("Completely different human layout; no grade text at all", encoding="utf-8")
            bundle = _bundle()
            provider_a = FakeProvider(responder=lambda request: _payload())
            provider_b = FakeProvider(responder=lambda request: _payload())
            generate_pre_a_report(first, root / "out-a.md", source_bundle=bundle, provider=provider_a, prompt_text="policy", reasoning_effort="high")
            generate_pre_a_report(second, root / "out-b.md", source_bundle=bundle, provider=provider_b, prompt_text="policy", reasoning_effort="high")
            user_a = next(message["content"] for message in provider_a.calls[0]["request"]["messages"] if message["role"] == "user")
            user_b = next(message["content"] for message in provider_b.calls[0]["request"]["messages"] if message["role"] == "user")
            self.assertEqual(user_a, user_b)
            self.assertNotIn("promotional prose", user_a)
            self.assertNotIn("different human layout", user_a)

    def test_sidecar_refuses_to_overwrite_primary_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "DAILY_REPORT.md"
            source.write_text("provenance", encoding="utf-8")
            provider = FakeProvider(responder=lambda request: _payload())
            with self.assertRaisesRegex(PreASidecarError, "must not overwrite"):
                generate_pre_a_report(
                    source,
                    source,
                    source_bundle=_bundle(),
                    provider=provider,
                    prompt_text="policy",
                    reasoning_effort="high",
                )

    def test_sidecar_rejects_hallucinated_ticker(self):
        payload = _payload(_candidate(ticker="XYZ"))
        with self.assertRaisesRegex(PreASidecarError, "hallucinated ticker"):
            validate_sidecar_payload(payload, _bundle())

    def test_model_cannot_claim_b_plus_when_structured_grade_is_a_minus(self):
        payload = _payload(_candidate(source_grade="B+"))
        with self.assertRaisesRegex(PreASidecarError, "does not match structured PRIMARY grade"):
            validate_sidecar_payload(payload, _bundle(source_grade="A-", decision={"ticker": "ABC", "grade": "A-"}))

    def test_pre_a_readiness_requires_structured_b_plus(self):
        payload = _payload(_candidate(source_grade="A-", promotion_readiness="PRE_A"))
        with self.assertRaisesRegex(PreASidecarError, r"requires structured B\+"):
            validate_sidecar_payload(payload, _bundle(source_grade="A-", decision={"ticker": "ABC", "grade": "A-"}))

    def test_pre_a_readiness_rejects_engineering_failure(self):
        bundle = _bundle(stages={
            "CAPITAL_PRESCREEN_GATE": {"status": "SUCCEEDED", "result": {"decision": "PASS"}},
            "CANDIDATE_ENGINEERING_FAILURE": {"status": "FAILED", "result": {"status": "ENGINEERING_FAILURE"}},
        })
        with self.assertRaisesRegex(PreASidecarError, "incomplete PRIMARY evaluation"):
            validate_sidecar_payload(_payload(), bundle)

    def test_pre_a_high_rejects_critical_gate(self):
        payload = _payload(
            _candidate(
                missing_gates=[
                    {"gate": "financing viability", "severity": "CRITICAL", "reason": "unresolved"}
                ]
            )
        )
        with self.assertRaisesRegex(PreASidecarError, "CRITICAL"):
            validate_sidecar_payload(payload, _bundle())

    def test_pre_a_high_rejects_more_than_two_open_gates(self):
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
            validate_sidecar_payload(payload, _bundle())


if __name__ == "__main__":
    unittest.main()
