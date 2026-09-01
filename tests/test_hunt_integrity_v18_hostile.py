from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from stock_agent.hunt_integrity_v18 import (
    STEP18_SOURCE_SHA256,
    _certification_grade,
    _select_source_indices,
    _v18_dedupe_sources,
    _v18_excerpt,
    _v18_project_value,
)
from stock_agent.v8_next_successor import V8_NEXT_POLICY_HASH, V8_NEXT_POLICY_VERSION


ROOT = Path(__file__).resolve().parents[1]


class HuntIntegrityV18HostileTests(unittest.TestCase):
    def test_10mb_canonical_source_survives_while_wire_projection_is_bounded(self):
        body = "A" * 5_000_000 + " decisive middle fact " + "B" * 5_000_000
        source = {
            "source_class": "COMPANY_IR",
            "source_url": "https://issuer.example/source",
            "source_observed_at": "2026-09-01T00:00:00Z",
            "title": "large source",
            "content": body,
        }
        canonical = _v18_dedupe_sources([source])
        self.assertEqual(len(canonical), 1)
        self.assertEqual(canonical[0]["content"], body)
        self.assertEqual(len(canonical[0]["content"]), len(body))

        projected = _v18_project_value({"evidence_items": canonical}, key="context")
        wire_source = projected["evidence_items"][0]
        self.assertLessEqual(len(wire_source["content"]), 6_000)
        self.assertEqual(wire_source["full_content_char_count"], len(body))
        self.assertTrue(wire_source["wire_projection_only"])

    def test_decisive_negative_source_30_is_not_lost_to_first_n_bias(self):
        sources = [
            {
                "source_class": "MEDIA",
                "source_url": f"https://example.com/{index}",
                "title": f"source {index}",
                "content": "ordinary neutral update",
            }
            for index in range(35)
        ]
        sources[29]["content"] = "The issuer disclosed a material weakness and covenant default risk."
        selected = _select_source_indices(sources, 24)
        self.assertIn(29, selected)
        self.assertIn(34, selected)

    def test_keywordless_middle_disclosure_survives_structural_sampling(self):
        marker = "ZXQ_UNMODELED_FACT_9173"
        text = "x" * 24_000 + marker + "y" * 24_000
        excerpt = _v18_excerpt(text, 6_000)
        self.assertIn(marker, excerpt)
        self.assertLessEqual(len(excerpt), 6_000)

    def test_step18_receipt_fails_closed_on_wrong_source_or_noncanonical_authority(self):
        good = {
            "source_sha256": STEP18_SOURCE_SHA256,
            "grade_authority": "V8_STEP18_CANONICAL",
            "discovery_score_used": False,
            "research_grade": "B+",
        }
        self.assertEqual(_certification_grade(good), "B+")
        bad_source = dict(good, source_sha256="0" * 64)
        self.assertIsNone(_certification_grade(bad_source))
        bad_authority = dict(good, grade_authority="MODEL_GUESS")
        self.assertIsNone(_certification_grade(bad_authority))
        bad_discovery = dict(good, discovery_score_used=True)
        self.assertIsNone(_certification_grade(bad_discovery))

    def test_candidate_one_http_400_does_not_poison_candidate_two(self):
        script = r'''
import json
from contextlib import contextmanager
from types import SimpleNamespace

import stock_agent.production  # installs the explicit production composition
from stock_agent import runtime
from stock_agent.providers import ProviderRequestError

v18 = next(cls for cls in runtime.ProductionStockAgent.__mro__ if cls.__name__ == "V18ProductionStockAgent")
base = v18.__mro__[1]

class DB:
    def __init__(self, owner): self.owner = owner
    def execute(self, sql, params=()):
        self.owner.sql.append((sql, list(params)))
        return None

class Store:
    def __init__(self):
        self.sql = []
        self.stage_results = []
    @contextmanager
    def transaction(self):
        yield DB(self)
    def dependency_hash(self, ids, rule_hash, context_hash): return "dep"
    def record_stage_result(self, run_id, work_item_id, stage, subject_id, result, dependency_ids, dependency_hash, evidence_epoch, status="SUCCEEDED"):
        self.stage_results.append({"stage": stage, "subject_id": subject_id, "status": status, "result": result})
    def current_evidence_epoch_for(self, ids): return 0

agent = object.__new__(v18)
agent._v18_candidate_failures = {}
agent.store = Store()
run = SimpleNamespace(run_id="RUN", rule_set=SimpleNamespace(rule_set_hash="rules"), context_manifest_hash="ctx")
original = base._work_stage

def fake_stage(self, run, stage, prompt_id, payload, subject_id, dependency_ids, context_inputs=None):
    if subject_id == "A":
        raise ProviderRequestError("HTTP 400 invalid_request_error", retryable=False, status_code=400)
    return {"research_status": "COMPLETE", "subject_id": subject_id}

base._work_stage = fake_stage
try:
    first = v18._work_stage(agent, run, "DEEP_RESEARCH", "workflow.stock_researcher", {}, "A", [], {})
    second = v18._work_stage(agent, run, "DEEP_RESEARCH", "workflow.stock_researcher", {}, "B", [], {})
finally:
    base._work_stage = original

print(json.dumps({
    "first": first,
    "second": second,
    "failures": agent._v18_candidate_failures,
    "stage_results": agent.store.stage_results,
}))
'''
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["first"]["research_status"], "INCOMPLETE")
        self.assertEqual(payload["second"]["research_status"], "COMPLETE")
        self.assertIn("A", payload["failures"])
        self.assertNotIn("B", payload["failures"])
        failure_receipt = next(row for row in payload["stage_results"] if row["stage"] == "CANDIDATE_ENGINEERING_FAILURE")
        self.assertEqual(failure_receipt["status"], "FAILED")

    def test_pipeline_failure_conclusion_does_not_render_no_trade(self):
        script = r'''
import json
import stock_agent.production
from stock_agent import shadow

log = {
    "started_at": "2026-09-01T00:00:00Z",
    "run_id": "S1",
    "shadow_version": "SHADOW_V1.3",
    "code_git_sha": "x",
    "git_diff_hash": "x",
    "source_tree_hash": "x",
    "git_dirty": False,
    "strategy_cohort_hash": "x",
    "providers": {},
    "market_context": {"analysis": {}},
    "universe": {},
    "investment_conclusion": "NOT_EVALUABLE_PIPELINE_FAILURE",
}
text = shadow.DailyShadowRunner._report(log, [])
print(json.dumps({"text": text}))
'''
        completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT, text=True, capture_output=True, check=True)
        text = json.loads(completed.stdout.strip().splitlines()[-1])["text"]
        self.assertIn("NOT_EVALUABLE_PIPELINE_FAILURE", text)
        self.assertNotIn("- NO_TRADE", text)

    def test_direct_production_import_and_python_m_use_same_composition(self):
        env = dict(os.environ)
        env["STOCK_AGENT_COMPOSITION_PROBE"] = "1"
        module_run = subprocess.run(
            [sys.executable, "-m", "stock_agent"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        direct_run = subprocess.run(
            [
                sys.executable,
                "-c",
                "import json; from stock_agent.production import production_composition; print(json.dumps(production_composition(), sort_keys=True))",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        module_comp = json.loads(module_run.stdout.strip().splitlines()[-1])
        direct_comp = json.loads(direct_run.stdout.strip().splitlines()[-1])
        self.assertEqual(module_comp, direct_comp)
        self.assertEqual(module_comp["runtime_class"], "V8NextRuntimeProductionStockAgent")
        self.assertEqual(module_comp["v8_policy_version"], V8_NEXT_POLICY_VERSION)
        self.assertEqual(module_comp["v8_ruleset_hash"], V8_NEXT_POLICY_HASH)
        self.assertEqual(module_comp["v8_next_runtime_version"], "V8_NEXT_CERTIFICATION_RUNTIME_V1.0")
        self.assertIn("V181ProductionStockAgent", " ".join(module_comp["mro"]))


if __name__ == "__main__":
    unittest.main()
