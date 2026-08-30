from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
PROMPT_DIRS = {"SYSTEM", "WORKFLOW", "CAPABILITIES", "ADVERSARIAL", "UTILITIES", "INDUSTRY"}
REGISTRY_PATH = ROOT / "SCHEMAS" / "output_schema_registry_v2_2.json"
METADATA_SCHEMA_PATH = ROOT / "SCHEMAS" / "prompt_metadata_schema_v2_2.json"
MANIFEST_PATH = ROOT / "prompt_registry_manifest_v2_2.json"
RUNTIME_PATH = ROOT / "RUNTIME_CONTRACTS" / "architecture_runtime_contract_v1_1.json"
FORMAT_CHECKER = FormatChecker()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_prompt(path: Path) -> dict[str, Any]:
    parts = path.read_text(encoding="utf-8-sig").split("---", 2)
    if len(parts) != 3:
        raise AssertionError(f"frontmatter missing: {path}")
    return yaml.safe_load(parts[1])


def prompt_files() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.md") if p.parent.name in PROMPT_DIRS)


def resolved_schema(registry: dict[str, Any], schema_id: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": registry["$defs"],
        **copy.deepcopy(registry["schemas"][schema_id]),
    }


def validate_schema_instance(registry: dict[str, Any], schema_id: str, instance: Any) -> list[str]:
    validator = Draft202012Validator(resolved_schema(registry, schema_id), format_checker=FORMAT_CHECKER)
    schema_errors = [error.message for error in sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))]
    return schema_errors + semantic_instance_errors(schema_id, instance)


def semantic_instance_errors(schema_id: str, instance: Any) -> list[str]:
    """Cross-field rules that JSON Schema cannot express without duplicating runtime logic."""
    if not isinstance(instance, dict):
        return []
    errors: list[str] = []

    failure_paths = instance.get("failure_paths")
    if isinstance(failure_paths, list):
        categories = [path.get("category") for path in failure_paths if isinstance(path, dict)]
        causal_keys = [
            (str(path.get("scenario", "")).strip().casefold(), str(path.get("causal_path", "")).strip().casefold())
            for path in failure_paths if isinstance(path, dict)
        ]
        if len(set(categories)) < 3:
            errors.append("failure_paths must contain at least three distinct categories")
        if len(set(causal_keys)) != len(causal_keys):
            errors.append("failure_paths scenario+causal_path pairs must be unique")

    if schema_id == "FinalSynthesisRecommendationV2":
        action = instance.get("recommended_action")
        if action == "STARTER" and isinstance(instance.get("starter_plan"), dict):
            plan = instance["starter_plan"]
            holding = plan.get("maximum_holding_period", {})
            if holding.get("minimum_days", 0) > holding.get("maximum_days", 0):
                errors.append("StarterPlanV2 minimum_days exceeds maximum_days")
            maximum = plan.get("maximum_position", {})
            resulting = plan.get("planned_add", {}).get("resulting_position_cap", {})
            starter_shares = plan.get("starter_shares", 0)
            starter_pct = plan.get("starter_capital_pct", 0)
            if starter_shares > maximum.get("shares", 0):
                errors.append("starter shares exceed maximum position")
            if starter_pct > maximum.get("capital_pct", 0):
                errors.append("starter capital percentage exceeds maximum position")
            if resulting.get("shares", 0) < starter_shares:
                errors.append("resulting post-add shares are below starter shares")
            if resulting.get("capital_pct", 0) < starter_pct:
                errors.append("resulting post-add capital percentage is below starter percentage")
            planned_add = plan.get("planned_add", {})
            planned_shares = planned_add.get("planned_add_shares")
            planned_pct = planned_add.get("planned_add_capital_pct")
            if isinstance(planned_shares, (int, float)) and starter_shares + planned_shares > resulting.get("shares", 0):
                errors.append("starter plus planned add shares exceed resulting position cap")
            if isinstance(planned_pct, (int, float)) and starter_pct + planned_pct > resulting.get("capital_pct", 0):
                errors.append("starter plus planned add percentage exceeds resulting position cap")
            if resulting.get("shares", 0) > maximum.get("shares", 0):
                errors.append("planned add resulting shares exceed maximum position")
            if resulting.get("capital_pct", 0) > maximum.get("capital_pct", 0):
                errors.append("planned add resulting capital percentage exceeds maximum position")
        if action in {"ADD", "FULL", "TRIM", "EXIT"}:
            target = instance.get("target_security_id")
            position_receipt = instance.get("position_snapshot_receipt")
            if isinstance(position_receipt, dict):
                if position_receipt.get("subject_id") != target:
                    errors.append("position_snapshot_receipt.subject_id does not match target_security_id")
                if position_receipt.get("position_exists") is not True:
                    errors.append("position_snapshot_receipt must prove position_exists=true")
        if action == "ADD":
            target = instance.get("target_security_id")
            for key in ["prior_add_trigger_receipt", "fresh_evidence_delta_receipt", "strengthening_evidence_receipt"]:
                receipt = instance.get(key)
                if isinstance(receipt, dict) and receipt.get("subject_id") != target:
                    errors.append(f"{key}.subject_id does not match target_security_id")
            prior = instance.get("prior_add_trigger_receipt") or {}
            add_plan = instance.get("add_plan") or {}
            if prior.get("trigger_id") != add_plan.get("trigger_id"):
                errors.append("prior ADD trigger id does not match AddPlanV2")
            if prior.get("trigger_type") != add_plan.get("trigger_type"):
                errors.append("prior ADD trigger type does not match AddPlanV2")
            planned_evidence = set(add_plan.get("strengthening_evidence_ids") or [])
            delta_evidence = set((instance.get("fresh_evidence_delta_receipt") or {}).get("strengthening_evidence_ids") or [])
            receipt_evidence = set((instance.get("strengthening_evidence_receipt") or {}).get("strengthening_evidence_ids") or [])
            if not planned_evidence or not planned_evidence.issubset(delta_evidence) or not planned_evidence.issubset(receipt_evidence):
                errors.append("AddPlanV2 evidence must be a non-empty subset of both strengthening receipts")

    if schema_id == "PortfolioComparisonResultV2":
        root_snapshot = instance.get("capital_snapshot_id")
        alternatives = instance.get("alternatives") or []
        ranks = [alt.get("relative_rank") for alt in alternatives if isinstance(alt, dict)]
        rows = [(alt.get("asset_id"), alt.get("capital_path")) for alt in alternatives if isinstance(alt, dict)]
        if len(ranks) != len(set(ranks)):
            errors.append("portfolio relative_rank values must be unique")
        if len(rows) != len(set(rows)):
            errors.append("portfolio asset_id+capital_path rows must be unique")
        for alt in alternatives:
            if not isinstance(alt, dict):
                continue
            if alt.get("capital_snapshot_id") != root_snapshot:
                errors.append("alternative capital_snapshot_id does not match root snapshot")
            receipt = alt.get("position_snapshot_receipt")
            if isinstance(receipt, dict) and receipt.get("subject_id") != alt.get("asset_id"):
                errors.append("portfolio position receipt subject does not match alternative asset")
        preferred = instance.get("preferred_recommendation") or {}
        preferred_key = (preferred.get("asset_id"), preferred.get("capital_path"))
        if rows.count(preferred_key) != 1:
            errors.append("preferred recommendation must resolve to exactly one alternative row")
    return errors


def walk_objects(schema: Any, location: str = "root") -> list[str]:
    failures: list[str] = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            if not schema.get("properties"):
                failures.append(f"{location}: object has no properties")
            if schema.get("additionalProperties") is not False:
                failures.append(f"{location}: additionalProperties is not false")
        for key, value in schema.items():
            failures.extend(walk_objects(value, f"{location}/{key}"))
    elif isinstance(schema, list):
        for index, value in enumerate(schema):
            failures.extend(walk_objects(value, f"{location}/{index}"))
    return failures


def resolve_composition(root_id: str, prompts: dict[str, dict[str, Any]]) -> list[str]:
    ordered: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(prompt_id: str) -> None:
        if prompt_id in visited:
            return
        if prompt_id in visiting:
            raise AssertionError(f"composition cycle at {prompt_id}")
        visiting.add(prompt_id)
        for dependency in sorted(prompts[prompt_id]["compose_with"]):
            visit(dependency)
        visiting.remove(prompt_id)
        visited.add(prompt_id)
        ordered.append(prompt_id)

    visit(root_id)
    mixins = [x for x in ordered if prompts[x]["prompt_kind"] == "MIXIN"]
    leaves = [x for x in ordered if prompts[x]["prompt_kind"] == "LEAF"]
    return mixins + leaves


def graph_is_acyclic(prompts: dict[str, dict[str, Any]]) -> tuple[bool, str | None]:
    visiting: set[str] = set()
    visited: set[str] = set()

    def dependencies(metadata: dict[str, Any]) -> list[str]:
        conditional = [item for values in metadata["conditional_dependencies"].values() for item in values]
        return metadata["requires_results"] + metadata["requires_capabilities"] + conditional

    def visit(prompt_id: str) -> None:
        if prompt_id in visited:
            return
        if prompt_id in visiting:
            raise AssertionError(prompt_id)
        visiting.add(prompt_id)
        for dependency in dependencies(prompts[prompt_id]):
            visit(dependency)
        visiting.remove(prompt_id)
        visited.add(prompt_id)

    try:
        for prompt_id in prompts:
            visit(prompt_id)
    except AssertionError as exc:
        return False, str(exc)
    return True, None


def semantic_dependency_failures(prompts: dict[str, dict[str, Any]], runtime: dict[str, Any]) -> list[str]:
    stage_index = {name: index for index, name in enumerate(runtime["stage_order"])}
    failures: list[str] = []
    for prompt_id, metadata in prompts.items():
        if metadata["prompt_kind"] == "MIXIN":
            continue
        consumer_stage = metadata["stage"]
        conditional = [item for values in metadata["conditional_dependencies"].values() for item in values]
        for dependency in metadata["requires_results"] + metadata["requires_capabilities"] + conditional:
            producer_stage = prompts[dependency]["stage"]
            if producer_stage == "MIXIN":
                continue
            if stage_index[producer_stage] > stage_index[consumer_stage]:
                failures.append(f"{prompt_id}({consumer_stage}) requires future {dependency}({producer_stage})")
        for input_name in metadata["required_inputs"]:
            product = runtime["data_products"].get(input_name)
            if product is None:
                failures.append(f"{prompt_id}: no producer for required input {input_name}")
                continue
            producer_stage = product["produced_at_stage"]
            if stage_index[producer_stage] > stage_index[consumer_stage]:
                failures.append(f"{prompt_id}({consumer_stage}) requires future input {input_name}({producer_stage})")
    return failures


def hunt_only_failures(prompts: dict[str, dict[str, Any]], runtime: dict[str, Any]) -> list[str]:
    execution_stages = {"EXECUTION_RISK", "FINAL_SYNTHESIS"}
    failures: list[str] = []

    def dependencies(metadata: dict[str, Any]) -> list[str]:
        conditional = [item for values in metadata["conditional_dependencies"].values() for item in values]
        return metadata["compose_with"] + metadata["requires_results"] + metadata["requires_capabilities"] + conditional

    for root_id, root in prompts.items():
        if "HUNT_ONLY" not in root["allowed_run_modes"]:
            continue
        stack = [root_id]
        seen: set[str] = set()
        while stack:
            prompt_id = stack.pop()
            if prompt_id in seen:
                continue
            seen.add(prompt_id)
            metadata = prompts[prompt_id]
            if "HUNT_ONLY" not in metadata["allowed_run_modes"]:
                failures.append(f"{root_id} reaches execution-only prompt {prompt_id}")
            if metadata["stage"] in execution_stages:
                failures.append(f"{root_id} reaches execution stage {prompt_id}")
            stack.extend(dependencies(metadata))
        for input_name in root["required_inputs"]:
            if runtime["data_products"][input_name]["produced_at_stage"] in execution_stages:
                failures.append(f"{root_id} requires execution-only input {input_name}")
    return failures


def canonical_registry_hash(registry: dict[str, Any]) -> str:
    payload = json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def run(*, write_reports: bool = False) -> dict[str, Any]:
    registry = load_json(REGISTRY_PATH)
    metadata_schema = load_json(METADATA_SCHEMA_PATH)
    manifest = load_json(MANIFEST_PATH)
    runtime = load_json(RUNTIME_PATH)
    prompt_paths = prompt_files()
    prompt_items = [(path, read_prompt(path)) for path in prompt_paths]
    prompts = {metadata["prompt_id"]: metadata for _, metadata in prompt_items}

    results: dict[str, Any] = {}
    failures: list[str] = []

    # JSON Schema formal validity and strict object coverage.
    try:
        Draft202012Validator.check_schema(metadata_schema)
        for schema_id in registry["schemas"]:
            Draft202012Validator.check_schema(resolved_schema(registry, schema_id))
        strict_failures = walk_objects({"$defs": registry["$defs"], "schemas": registry["schemas"]})
        failures.extend(strict_failures)
        results["typed_schema_registry"] = {"status": "PASS" if not strict_failures else "FAIL", "schema_count": len(registry["schemas"]), "strict_object_failures": strict_failures}
    except Exception as exc:
        failures.append(f"schema definition invalid: {exc}")
        results["typed_schema_registry"] = {"status": "FAIL", "error": str(exc)}

    # Positive and actual negative contract tests.
    positive_cases = load_json(ROOT / "VALIDATION" / "schema_positive_cases.json")["cases"]
    positive_case_index = {case["case_id"]: case for case in positive_cases}
    positive_failures = []
    for case in positive_cases:
        errors = validate_schema_instance(registry, case["schema_id"], case["instance"])
        if errors:
            positive_failures.append({"case_id": case["case_id"], "errors": errors})
    failures.extend(f"positive case failed: {x['case_id']}" for x in positive_failures)
    results["schema_positive_cases"] = {"status": "PASS" if not positive_failures else "FAIL", "passed": len(positive_cases) - len(positive_failures), "total": len(positive_cases), "failures": positive_failures}

    negative_cases = load_json(ROOT / "VALIDATION" / "schema_negative_cases.json")["cases"]
    unexpected_passes = []
    for case in negative_cases:
        errors = validate_schema_instance(registry, case["schema_id"], case["instance"])
        if not errors:
            unexpected_passes.append(case["case_id"])
    failures.extend(f"negative case unexpectedly passed: {x}" for x in unexpected_passes)
    results["schema_negative_cases"] = {"status": "PASS" if not unexpected_passes else "FAIL", "rejected": len(negative_cases) - len(unexpected_passes), "total": len(negative_cases), "unexpected_passes": unexpected_passes}

    # Metadata and manifest projection/hash consistency.
    metadata_validator = Draft202012Validator(metadata_schema, format_checker=FORMAT_CHECKER)
    metadata_failures = []
    for path, metadata in prompt_items:
        errors = [x.message for x in metadata_validator.iter_errors(metadata)]
        if metadata.get("prompt_kind") == "LEAF":
            linked = positive_case_index.get(metadata.get("output_example_case"))
            if linked is None:
                errors.append("linked positive example case does not exist")
            elif linked["schema_id"] != metadata.get("output_schema"):
                errors.append("linked positive example schema does not match output_schema")
            body = path.read_text(encoding="utf-8-sig")
            if metadata.get("output_example_case") not in body:
                errors.append("prompt body does not reference its machine-validated example")
        if errors:
            metadata_failures.append({"file": path.relative_to(ROOT).as_posix(), "errors": errors})
    if len(prompts) != len(prompt_items):
        metadata_failures.append({"file": "*", "errors": ["duplicate prompt_id"]})
    failures.extend(f"metadata failed: {x['file']}" for x in metadata_failures)
    results["prompt_metadata"] = {"status": "PASS" if not metadata_failures else "FAIL", "prompt_count": len(prompt_items), "unique_prompt_ids": len(prompts), "failures": metadata_failures}

    # Prompt-body contract lint: formal schema is the sole contract source.
    body_contract_failures = []
    for path, metadata in prompt_items:
        if metadata["prompt_kind"] != "LEAF":
            continue
        body = path.read_text(encoding="utf-8-sig").split("---", 2)[2]
        output_sections = re.findall(r"(?ms)^## (?:Formal )?Output Contract[^\n]*\n.*?(?=^## |\Z)", body)
        if len(output_sections) != 1:
            body_contract_failures.append(f"{metadata['prompt_id']}: formal output section count {len(output_sections)}")
            continue
        if "```json" in output_sections[0]:
            body_contract_failures.append(f"{metadata['prompt_id']}: duplicated inline JSON Output Contract")
        canonical_ref = f"SCHEMAS/output_schema_registry_v2_2.json#{metadata['output_schema']}"
        if canonical_ref not in output_sections[0]:
            body_contract_failures.append(f"{metadata['prompt_id']}: canonical schema reference missing")
        if re.search(r"(?m)^# .*v2\.1\b", body):
            body_contract_failures.append(f"{metadata['prompt_id']}: stale v2.1 heading")
        if metadata["prompt_id"] == "adversarial.consensus_revalidation" and re.search(r"consensus_recommendation=(?:READY|NOT_READY|DEADLOCK)", body):
            body_contract_failures.append(f"{metadata['prompt_id']}: prose requests enum outside formal schema")
    failures.extend(body_contract_failures)
    results["prompt_body_contract_lint"] = {"status": "PASS" if not body_contract_failures else "FAIL", "leaf_prompts_checked": sum(1 for x in prompts.values() if x["prompt_kind"] == "LEAF"), "inline_json_contract_count": 0 if not body_contract_failures else None, "failures": body_contract_failures}

    manifest_entries = {entry["prompt_id"]: entry for entry in manifest["prompts"]}
    manifest_diffs = []
    hash_passed = 0
    for path, metadata in prompt_items:
        entry = manifest_entries.get(metadata["prompt_id"])
        if entry is None:
            manifest_diffs.append(f"missing manifest entry {metadata['prompt_id']}")
            continue
        # Prompt hashes are defined over canonical LF bytes so the manifest is
        # stable across Windows CRLF checkouts and POSIX LF checkouts.
        canonical_bytes = path.read_bytes().replace(b"\r\n", b"\n")
        expected = {"file": path.relative_to(ROOT).as_posix(), "content_hash": hashlib.sha256(canonical_bytes).hexdigest(), **metadata}
        if entry != expected:
            manifest_diffs.append(f"semantic diff {metadata['prompt_id']}")
        if entry.get("content_hash") == expected["content_hash"]:
            hash_passed += 1
    if set(manifest_entries) != set(prompts):
        manifest_diffs.append("manifest/prompt id set mismatch")
    if manifest["output_schema_registry_hash"] != canonical_registry_hash(registry):
        manifest_diffs.append("output schema registry hash mismatch")
    failures.extend(manifest_diffs)
    results["manifest_consistency"] = {"status": "PASS" if not manifest_diffs else "FAIL", "semantic_diff_count": len(manifest_diffs), "content_hash_passed": hash_passed, "content_hash_total": len(prompt_items), "diffs": manifest_diffs}

    # References and one-call/one-output-owner composition.
    reference_failures = []
    composition_failures = []
    for prompt_id, metadata in prompts.items():
        conditional = [item for values in metadata["conditional_dependencies"].values() for item in values]
        for dependency in metadata["compose_with"] + metadata["requires_results"] + metadata["requires_capabilities"] + conditional:
            if dependency not in prompts:
                reference_failures.append(f"{prompt_id} -> missing {dependency}")
        if metadata["prompt_kind"] == "LEAF" and metadata["output_schema"] not in registry["schemas"]:
            reference_failures.append(f"{prompt_id} -> missing schema {metadata['output_schema']}")
        if metadata["prompt_kind"] == "MIXIN" and metadata["output_schema"] is not None:
            reference_failures.append(f"mixin {prompt_id} owns output schema")
    if not reference_failures:
        for prompt_id, metadata in prompts.items():
            if metadata["prompt_kind"] != "LEAF":
                continue
            resolved = resolve_composition(prompt_id, prompts)
            if len(resolved) != len(set(resolved)):
                composition_failures.append(f"{prompt_id}: duplicate prompt_id after flatten")
            owner_count = sum(1 for item in resolved if prompts[item]["prompt_kind"] == "LEAF" and prompts[item]["output_schema"])
            grounding_count = resolved.count("system.analysis_grounding")
            if owner_count != 1:
                composition_failures.append(f"{prompt_id}: output owner count {owner_count}")
            if grounding_count != 1:
                composition_failures.append(f"{prompt_id}: grounding count {grounding_count}")
        composition_cases = load_json(ROOT / "VALIDATION" / "prompt_composition_cases.json")["cases"]
        for case in composition_cases:
            resolved = resolve_composition(case["root_prompt_id"], prompts)
            actual_owner_count = sum(1 for item in resolved if prompts[item]["prompt_kind"] == "LEAF" and prompts[item]["output_schema"])
            actual_grounding_count = resolved.count("system.analysis_grounding")
            if actual_owner_count != case["expect_leaf_output_owner_count"]:
                composition_failures.append(f"{case['case_id']}: expected owner {case['expect_leaf_output_owner_count']}, got {actual_owner_count}")
            if actual_grounding_count != case["expect_system_grounding_count"]:
                composition_failures.append(f"{case['case_id']}: expected grounding {case['expect_system_grounding_count']}, got {actual_grounding_count}")
    failures.extend(reference_failures + composition_failures)
    results["dependency_references"] = {"status": "PASS" if not reference_failures else "FAIL", "failures": reference_failures}
    results["composition_contract"] = {"status": "PASS" if not composition_failures else "FAIL", "explicit_case_count": len(load_json(ROOT / "VALIDATION" / "prompt_composition_cases.json")["cases"]), "all_leaf_prompts_checked": sum(1 for item in prompts.values() if item["prompt_kind"] == "LEAF"), "failures": composition_failures}

    acyclic, cycle_at = graph_is_acyclic(prompts)
    semantic_failures = semantic_dependency_failures(prompts, runtime)
    hunt_failures = hunt_only_failures(prompts, runtime)
    if not acyclic:
        failures.append(f"semantic dependency cycle at {cycle_at}")
    failures.extend(semantic_failures + hunt_failures)
    results["semantic_dependency"] = {"status": "PASS" if acyclic and not semantic_failures else "FAIL", "cycle_count": 0 if acyclic else 1, "future_dependency_failures": semantic_failures}
    results["hunt_only_dependency"] = {"status": "PASS" if not hunt_failures else "FAIL", "execution_dependency_count": len(hunt_failures), "failures": hunt_failures}

    sequencing_failures = []
    expected_products = {
        "market_context_gate_receipt": ("Python MarketContextGate", "DISCOVERY"),
        "sector_gate_receipt": ("Python SectorGate", "DISCOVERY"),
        "stage_gate_receipt": ("Python StageGate", "DISCOVERY"),
        "capital_prescreen_gate_receipt": ("Python CapitalPrescreenGate", "PRESCREEN"),
        "market_execution_gate_receipt": ("Python MarketExecutionGate", "EXECUTION_RISK"),
    }
    for product_name, (owner, stage) in expected_products.items():
        actual = runtime["data_products"].get(product_name)
        if actual != {"producer": owner, "produced_at_stage": stage}:
            sequencing_failures.append(f"{product_name}: expected {owner}@{stage}, got {actual}")
    required_prerequisites = {
        "workflow.sector_analyst": "market_context_gate_receipt",
        "workflow.stock_scout": "sector_gate_receipt",
        "utility.capital_structure_prescreen": "stage_gate_receipt",
        "workflow.stock_researcher": "stage_gate_receipt",
    }
    for prompt_id, required_input in required_prerequisites.items():
        if required_input not in prompts[prompt_id]["required_inputs"]:
            sequencing_failures.append(f"{prompt_id}: missing {required_input}")
    funnel = runtime["capital_funnel"]
    for left, right in [("STOCK_DISCOVERY", "STAGE_GATE"), ("STAGE_GATE", "CAPITAL_STRUCTURE_PRESCREEN"), ("CAPITAL_STRUCTURE_PRESCREEN", "CAPITAL_PRESCREEN_GATE"), ("CAPITAL_PRESCREEN_GATE", "STOCK_DEEP_RESEARCH")]:
        if funnel.index(left) >= funnel.index(right):
            sequencing_failures.append(f"funnel order invalid: {left} before {right}")
    schema_text = json.dumps(registry["schemas"], sort_keys=True)
    for receipt_type in ["StageGateReceipt", "MarketContextGateReceipt", "SectorGateReceipt", "MarketExecutionGateReceipt", "FreshnessDeltaReceiptV2", "PositionSnapshotReceiptV2", "PriorAddTriggerReceiptV2", "StrengtheningEvidenceReceiptV2"]:
        if f"#/$defs/{receipt_type}" not in schema_text:
            sequencing_failures.append(f"dedicated schema receipt unused: {receipt_type}")
    failures.extend(sequencing_failures)
    results["gate_provenance_and_sequencing"] = {"status": "PASS" if not sequencing_failures else "FAIL", "failures": sequencing_failures}

    # Synthetic negative dependency logic proves the checker is fail-closed.
    dependency_cases = load_json(ROOT / "VALIDATION" / "dependency_stage_cases.json")["synthetic_negative_cases"]
    stage_index = {name: index for index, name in enumerate(runtime["stage_order"])}
    synthetic_passed = 0
    for case in dependency_cases:
        if "producer_stage" in case:
            detected = stage_index[case["producer_stage"]] > stage_index[case["consumer_stage"]]
        else:
            detected = case["forbidden_dependency"] in runtime["run_modes"][case["run_mode"]]["forbidden_dependencies"]
        synthetic_passed += int(detected)
    if synthetic_passed != len(dependency_cases):
        failures.append("synthetic negative dependency detector failed")
    results["semantic_dependency_negative_cases"] = {"status": "PASS" if synthetic_passed == len(dependency_cases) else "FAIL", "rejected": synthetic_passed, "total": len(dependency_cases)}

    # Holding horizon is bound to the active EffectiveRuleSet by Python, not frozen in prompt schema.
    horizon_policy = runtime.get("holding_horizon_policy", {})
    horizon_cases = load_json(ROOT / "VALIDATION" / "holding_horizon_policy_cases.json")["cases"]
    horizon_failures = []
    default_maximum = horizon_policy.get("default_maximum_days")
    for case in horizon_cases:
        effective_maximum = case["active_rule_maximum_days"] if case["active_rule_override"] else default_maximum
        actual = "PASS" if isinstance(effective_maximum, int) and case["maximum_holding_days"] <= effective_maximum else "FAIL"
        if actual != case["expected"]:
            horizon_failures.append(f"{case['case_id']}: expected {case['expected']}, got {actual}")
    if default_maximum != 56 or horizon_policy.get("non_default_horizon_requires_active_rule_override") is not True:
        horizon_failures.append("runtime holding horizon policy is not bound to v2.0 default plus active override")
    failures.extend(horizon_failures)
    results["holding_horizon_runtime_policy"] = {
        "status": "PASS" if not horizon_failures else "FAIL",
        "passed": len(horizon_cases) - len(horizon_failures),
        "total": len(horizon_cases),
        "default_maximum_days": default_maximum,
        "failures": horizon_failures,
    }

    # Namespace separation and explicit authority checks.
    decision_contract = load_json(ROOT / "RUNTIME_CONTRACTS" / "decision_contract_v2.json")
    python_gate = set(decision_contract["python_gate_decisions"])
    audit = set(decision_contract["audit_recommendations"])
    final_status = set(decision_contract["final_recommendation_statuses"])
    namespace_overlap = sorted((audit | final_status) & python_gate)
    if namespace_overlap:
        failures.append(f"LLM/Python enum overlap: {namespace_overlap}")
    results["namespace_separation"] = {"status": "PASS" if not namespace_overlap else "FAIL", "overlap": namespace_overlap}
    results["authority_boundary"] = {
        "status": "PASS" if runtime["final_action_owner"] == "Python FinalAllocationGate only" and runtime["fresh_money_positive_commitment_cardinality"] == "0..1 per Run" else "FAIL",
        "final_action_owner": runtime["final_action_owner"],
        "fresh_money_cardinality": runtime["fresh_money_positive_commitment_cardinality"],
    }
    if results["authority_boundary"]["status"] != "PASS":
        failures.append("authority boundary mismatch")

    # Freeze checks required by the final remediation acceptance criteria.
    legacy_actions = {"BUY", "CONDITIONAL_BUY", "WAIT", "HOLD", "SELL"}
    decision_enum_values: set[str] = set()
    for namespace in ["discovery_decisions", "execution_recommendation_actions", "audit_recommendations", "final_recommendation_statuses"]:
        decision_enum_values.update(decision_contract[namespace])
    legacy_hits = sorted(legacy_actions & decision_enum_values)
    if legacy_hits:
        failures.append(f"legacy action namespace values remain: {legacy_hits}")
    results["legacy_action_scan"] = {"status": "PASS" if not legacy_hits else "FAIL", "hit_count": len(legacy_hits), "hits": legacy_hits}

    authority_intrusions = sorted(
        metadata["prompt_id"] for metadata in prompts.values()
        if metadata.get("authoritative_decision") is not False or metadata.get("side_effects") != "none"
    )
    if authority_intrusions:
        failures.append(f"prompt authority intrusion: {authority_intrusions}")
    results["python_authority_preservation"] = {
        "status": "PASS" if not authority_intrusions else "FAIL",
        "intrusion_count": len(authority_intrusions),
        "intrusions": authority_intrusions,
    }

    preservation_path = ROOT / "VALIDATION" / "industry_overlay_preservation_report.json"
    preservation = load_json(preservation_path) if preservation_path.exists() else {}
    industry_preserved = preservation.get("industry_overlay_count") == 12 and preservation.get("all_analytical_bodies_preserved") is True
    if not industry_preserved:
        failures.append("industry overlay semantic body preservation check failed")
    results["industry_overlay_preservation"] = {
        "status": "PASS" if industry_preserved else "FAIL",
        "overlay_count": preservation.get("industry_overlay_count", 0),
        "all_analytical_bodies_preserved": preservation.get("all_analytical_bodies_preserved", False),
    }

    overall = "PASS" if not failures else "FAIL"
    results["overall"] = overall
    results["failure_count"] = len(failures)
    results["failures"] = failures

    if write_reports:
        dump_json(ROOT / "VALIDATION" / "manifest_consistency_report.json", results["manifest_consistency"])
        dump_json(ROOT / "VALIDATION" / "contract_test_results.json", results)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Prompt Library contracts without mutating tracked files by default.")
    parser.add_argument("--write-report", action="store_true", help="write validation JSON reports into VALIDATION/")
    args = parser.parse_args()
    result = run(write_reports=args.write_report)
    print(json.dumps({"overall": result["overall"], "failure_count": result["failure_count"], "failures": result["failures"]}, ensure_ascii=False, indent=2))
    sys.exit(0 if result["overall"] == "PASS" else 1)

