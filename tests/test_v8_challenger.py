from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from stock_agent.v8_challenger import (
    ChallengerInputManifest,
    V8ArtifactStore,
    V8AuthorityViolation,
    V8BundleError,
    V8ChallengerRunner,
    LunaV8StageExecutor,
    V8PITViolation,
    V8PromptBundle,
    V8ScoreContamination,
    validate_certification_output,
)


STAGES = ["00A", "01"] + [f"{number:02d}" for number in range(2, 19)]


class V8ChallengerContractTests(unittest.TestCase):
    def _bundle(self, directory: Path) -> V8PromptBundle:
        source = directory / "STOCK_SCANNING_PROMPTS_V8_A_GRADE_PIPELINE.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("README_V8_PIPELINE.md", "bundle")
            for stage in STAGES:
                archive.writestr(f"{stage}_stage_V8.md", f"# {stage}")
            archive.writestr("RUN_ALL_V8_MASTER_PROMPT.md", "run")
        return V8PromptBundle.load(source)

    def _manifest(self, bundle: V8PromptBundle) -> ChallengerInputManifest:
        return ChallengerInputManifest.from_mapping({
            "primary_run_id": "RUN-1",
            "comparison_as_of": "2026-08-26T16:00:00Z",
            "market_snapshot_id": "MKT-1",
            "market_snapshot_hash": "mkt-hash",
            "evidence_manifest_hash": "ev-hash",
            "primary_ruleset_hash": "rules-hash",
            "v8_prompt_bundle_hash": bundle.bundle_hash,
            "primary_shadow_version": "SHADOW_V1.1",
        }, bundle)

    @staticmethod
    def _evidence(published: str = "2026-08-26T15:00:00Z") -> dict:
        return {
            "evidence_id": "E-1",
            "raw_artifact_id": "RA-1",
            "content_hash": "hash-1",
            "source_url": "https://issuer.example/evidence",
            "published_at": published,
            "retrieved_at": "2026-08-26T18:00:00Z",
            "security_id": "SEC-1",
        }

    @staticmethod
    def _market(observed: str = "2026-08-26T15:30:00Z") -> dict:
        return {
            "snapshot_id": "MKT-1",
            "snapshot_hash": "mkt-hash",
            "observed_at": observed,
            "price_as_of": observed,
        }

    def test_bundle_requires_all_phase1_prompts_and_hashes_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            self.assertEqual(set(bundle.stage_files), set(STAGES))
            self.assertTrue(bundle.bundle_hash)
            missing = Path(tmp) / "missing.zip"
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("00A_only.md", "x")
            with self.assertRaises(V8BundleError):
                V8PromptBundle.load(missing)

    def test_pit_rejects_source_published_after_comparison_as_of(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            runner = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=lambda *_: {})
            result = runner.run(self._manifest(bundle), [{"ticker": "XYZ"}], [self._evidence("2026-08-26T16:01:00Z")], market_snapshot=self._market())
            self.assertEqual(result.status, "FAILED")
            self.assertIn("published after comparison_as_of", result.errors[0])

    def test_later_retrieval_before_cutoff_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            seen: list[dict] = []

            def execute(stage, _prompt, context):
                seen.append(context)
                return {"candidates": [{"ticker": "XYZ"}]} if stage == "00A" else ({"score": 90, "hard_gate_pass": True} if stage == "18" else {"status": "OK"})

            result = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=execute).run(
                self._manifest(bundle), [{"ticker": "XYZ"}], [self._evidence()], market_snapshot=self._market(),
                primary_results=[{"ticker": "XYZ", "qualified": True}],
            )
            self.assertEqual(result.status, "SUCCEEDED")
            self.assertTrue(result.certified_a)
            self.assertEqual(result.broker_write_count, 0)
            self.assertTrue((Path(tmp) / "challenger_v8" / "PRIMARY_VS_V8_COMPARISON.json").exists())
            comparison = json.loads((Path(tmp) / "challenger_v8" / "PRIMARY_VS_V8_COMPARISON.json").read_text(encoding="utf-8"))
            self.assertEqual(comparison["challenger_run_id"], result.challenger_run_id)
            self.assertIn("EVIDENCE_MANIFEST", result.artifacts)
            self.assertIn("OUTCOMES", result.artifacts)

    def test_market_price_newer_than_cutoff_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            runner = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=lambda *_: {})
            result = runner.run(self._manifest(bundle), [], [self._evidence()], market_snapshot=self._market("2026-08-26T16:01:00Z"))
            self.assertEqual(result.status, "FAILED")
            self.assertIn("market snapshot is newer", result.errors[0])

    def test_discovery_score_and_primary_action_are_blind(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            contexts: list[dict] = []

            def execute(stage, _prompt, context):
                contexts.append(context)
                return {"score": 90, "hard_gate_pass": True} if stage == "18" else ({"candidates": [{"ticker": "XYZ"}]} if stage == "00A" else {"status": "OK"})

            result = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=execute).run(
                self._manifest(bundle), [{"ticker": "XYZ", "discovery_score": 100, "grade": "A", "final_allocation": {"shares": 9}}], [self._evidence()], market_snapshot=self._market()
            )
            self.assertEqual(result.status, "SUCCEEDED")
            for context in contexts:
                self.assertNotIn("discovery_score", json.dumps(context))
                self.assertNotIn("final_allocation", json.dumps(context))
                self.assertNotIn("grade", json.dumps(context))

    def test_step18_starts_from_zero_and_grade_cap_is_python_enforced(self):
        result = validate_certification_output({"score": 75, "category": "A_CERTIFIED", "hard_gate_pass": True})
        self.assertEqual(result["score_start"], 0)
        self.assertEqual(result["category"], "B_PLUS_ONLY")
        self.assertEqual(result["sizing_authority"], "PYTHON_ONLY")
        self.assertIsNone(result["authoritative_action"])

    def test_step18_normalizes_each_candidate_and_requests_search_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))

            def execute(stage, _prompt, _context):
                if stage == "18":
                    return {"certifications": [
                        {"ticker": "A", "score": 90, "category": "B_ONLY", "hard_gate_pass": True},
                        {"ticker": "B", "score": 75, "category": "A_CERTIFIED", "hard_gate_pass": True},
                    ]}
                return {"status": "OK"}

            result = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=execute).run(
                self._manifest(bundle), [{"ticker": "A"}, {"ticker": "B"}], [self._evidence()], market_snapshot=self._market()
            )
            self.assertEqual(result.status, "SUCCEEDED")
            self.assertEqual(result.certified_a, 1)
            self.assertEqual(result.certified_a_minus, 0)
            expansion = json.loads((Path(tmp) / "challenger_v8" / "SEARCH_EXPANSION_REQUEST.json").read_text(encoding="utf-8"))
            self.assertEqual(expansion["status"], "SEARCH_EXPANSION_REQUEST")
            self.assertEqual(expansion["certified_a_or_a_minus"], 1)
            certifications = [json.loads(line) for line in (Path(tmp) / "challenger_v8" / "V8_CERTIFICATION.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row["category"] for row in certifications], ["A_CERTIFIED", "B_PLUS_ONLY"])

    def test_step18_nonzero_start_is_rejected(self):
        with self.assertRaises(V8ScoreContamination):
            validate_certification_output({"score_start": 15, "score": 90, "hard_gate_pass": True})

    def test_step18_authoritative_action_is_rejected(self):
        with self.assertRaises(V8AuthorityViolation):
            validate_certification_output({"score": 90, "hard_gate_pass": True, "authoritative_action": "BUY"})

    def test_sizing_authority_cannot_be_user_controlled(self):
        with self.assertRaises(V8AuthorityViolation):
            validate_certification_output({"score": 90, "hard_gate_pass": True, "sizing_authority": "PYTHON_OR_USER"})

    def test_luna_executor_separates_context_from_system_policy_and_records_usage(self):
        class Provider:
            def __init__(self):
                self.request = None

            def call(self, request):
                self.request = request
                return {"score": 90, "hard_gate_pass": True}, {"input_tokens": 4, "output_tokens": 3, "reasoning_effort": "medium"}

        provider = Provider()
        executor = LunaV8StageExecutor(provider)
        payload = executor("18", "CERTIFICATION_INSTRUCTIONS", {"discovery_score": "MUST_NOT_BE_SENT"})
        self.assertEqual(payload["score"], 90)
        self.assertIn("UNTRUSTED_CONTEXT_DATA", provider.request["messages"][1]["content"])
        self.assertIn("CERTIFICATION_INSTRUCTIONS", provider.request["messages"][0]["content"])
        self.assertEqual(executor.telemetry[0]["output_tokens"], 3)
        self.assertNotIn("Authorization", json.dumps(provider.request))

    def test_secret_like_input_and_output_are_redacted_before_challenger_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            seen: list[dict] = []

            def execute(stage, _prompt, context):
                seen.append(context)
                if stage == "18":
                    return {"score": 90, "hard_gate_pass": True, "notes": "Bearer sk-test-secret-value-1234567890"}
                return {"status": "OK", "access_token": "sk-test-secret-value-1234567890"}

            result = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=execute).run(
                self._manifest(bundle), [{"ticker": "XYZ", "api_key": "sk-test-secret-value-1234567890"}],
                [{**self._evidence(), "content": "Authorization: Bearer sk-test-secret-value-1234567890"}],
                market_snapshot=self._market(),
            )
            self.assertEqual(result.status, "SUCCEEDED")
            self.assertNotIn("sk-test-secret-value-1234567890", json.dumps(seen))
            artifacts = "".join(path.read_text(encoding="utf-8") for path in (Path(tmp) / "challenger_v8").glob("*.json*"))
            self.assertNotIn("sk-test-secret-value-1234567890", artifacts)

    def test_discovery_stage_cannot_emit_research_grade(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))

            def execute(stage, _prompt, _context):
                return {"grade": "A"} if stage == "02" else ({"score": 90, "hard_gate_pass": True} if stage == "18" else {"status": "OK"})

            result = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=execute).run(
                self._manifest(bundle), [], [self._evidence()], market_snapshot=self._market()
            )
            self.assertEqual(result.status, "FAILED")
            self.assertIn("research grade", result.errors[0])

    def test_missing_evidence_lineage_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            runner = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=lambda *_: {})
            result = runner.run(self._manifest(bundle), [], [{"published_at": "2026-08-26T15:00:00Z"}], market_snapshot=self._market())
            self.assertEqual(result.status, "FAILED")
            self.assertIn("RawArtifact lineage", result.errors[0])

    def test_unknown_evidence_namespace_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(Path(tmp))
            row = self._evidence()
            row["namespace"] = "PRIMARY_MUTATION"
            result = V8ChallengerRunner(bundle, V8ArtifactStore(Path(tmp)), executor=lambda *_: {}).run(
                self._manifest(bundle), [], [row], market_snapshot=self._market()
            )
            self.assertEqual(result.status, "FAILED")
            self.assertIn("namespace", result.errors[0])

    def test_challenger_failure_isolated_and_primary_artifacts_are_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = self._bundle(root)
            result = V8ChallengerRunner(bundle, V8ArtifactStore(root), executor=lambda *_: (_ for _ in ()).throw(RuntimeError("provider timeout"))).run(
                self._manifest(bundle), [{"ticker": "XYZ"}], [self._evidence()], market_snapshot=self._market()
            )
            self.assertEqual(result.status, "FAILED")
            self.assertEqual(result.broker_write_count, 0)
            self.assertTrue((root / "challenger_v8" / "CHALLENGER_RUN_LOG.json").exists())
            self.assertFalse((root / "final_actions").exists())


if __name__ == "__main__":
    unittest.main()
