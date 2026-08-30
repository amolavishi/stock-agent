from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .gates import (CapitalPrescreenGate, ContractViolation, MarketContextGate,
                    MarketExecutionGate, RiskEngine, SectorGate, StageGate,
                    validate_add_lineage, validate_failure_paths,
                    validate_recommendation_identity, validate_starter_plan,
                    FinalAllocationGate, QualifiedCandidateGate, require_fresh, require_artifact_fresh,
                    validate_sec_artifacts, make_economic_assessment_receipt)
from .adapters import (MarketDataProvider, PortfolioProvider, SECProvider, ProviderError,
                        ResearchEvidenceProvider, UnavailableResearchEvidenceProvider,
                        RecordedMarketDataProvider, RecordedPortfolioProvider,
                        RecordedResearchEvidenceProvider, RecordedSECProvider,
                        deterministic_market_context_from_payload)
from .normalizers import MarketNormalizer, PortfolioNormalizer, SecurityNormalizer, TechnicalFeatureCalculator, deterministic_stage_from_features
from .portfolio_receipts import make_position_snapshot_receipt
from .execution_quantity import ExecutionQuantityError, transaction_shares
from .discovery import deterministic_universe_prefilter
from .catalyst import CatalystGate, extract_catalyst_packet
from .valuation import ExpectationGapGate, build_reverse_valuation_receipt, extract_valuation_inputs, observed_market_price
from .paths import canonical_prompt_library_root
from .models import (ActionRecommendation, EffectiveRuleSet, Evidence, RawArtifact,
                     ExecutionAction, GateDecision, RunMode, RunOutcome,
                     canonical_hash, utc_now)
from .prompt_runtime import PromptRuntime, PromptContractError
from .providers import CostTracker, FakeProvider, ModelRouter, RecordedProvider
from .store import SQLiteStore


def _merge_deterministic_market_context(context_payload: dict[str, Any], derived_context: dict[str, Any]) -> dict[str, Any]:
    """Merge deterministic summary fields without erasing live asset receipts.

    Providers attach the authoritative per-asset contract (units, currency,
    timestamps, observation counts, and RawArtifact/Evidence hashes).  The
    normalizer also emits a compact ``assets`` map used only for deriving
    regime labels.  Keeping that compact map out of the merge prevents a
    summary calculation from silently deleting the provenance required by
    ``MarketContextGate``.
    """
    for key, value in derived_context.items():
        if key in {"complete", "assets"}:
            continue
        context_payload[key] = value
    return context_payload


def _inject_python_receipts(result: Any, supplied_context: dict[str, Any], schema: dict[str, Any]) -> Any:
    """Bind Python-owned receipt fields before the final schema check.

    Gate and context receipts contain hashes that are authoritative outputs of
    Python/SQLite, not values a language model should be asked to recreate.
    When a canonical schema exposes one of these fields, copy the exact value
    (or its upstream receipt) from the already-bound typed context.  This is a
    one-way authority binding; no model value can override the receipt.
    """
    if not isinstance(result, dict):
        return result
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return result
    definitions = schema.get("$defs") if isinstance(schema.get("$defs"), dict) else {}
    for field in properties:
        if not str(field).endswith("_receipt"):
            continue
        typed = supplied_context.get(field)
        if typed is None:
            stem = str(field)[:-len("_receipt")]
            typed = supplied_context.get(f"{stem}_results") or supplied_context.get(stem)
        if not isinstance(typed, dict):
            continue
        if "gate_receipt" in str(field):
            value = typed.get("value")
        else:
            value = typed.get("upstream_receipt") or typed.get("value")
        if value is not None:
            field_schema = properties.get(field)
            if isinstance(field_schema, dict) and isinstance(field_schema.get("$ref"), str):
                definition_name = field_schema["$ref"].rsplit("/", 1)[-1]
                field_schema = definitions.get(definition_name)
            allowed = field_schema.get("properties") if isinstance(field_schema, dict) else None
            if isinstance(value, dict) and isinstance(allowed, dict):
                # GateReceipt.as_dict() carries an optional
                # ``core_input_complete`` flag that is intentionally not part
                # of the nested prompt receipt schema.  Emit only the fields
                # declared by that schema while preserving the exact hashes.
                value = {key: value[key] for key in allowed if key in value}
            result[field] = copy.deepcopy(value)
    return result


def _compact_model_universe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build a bounded, non-authoritative universe view for model prompts.

    The full market rows remain available to Python for deterministic filters,
    technical calculations, and provenance.  They may contain long candle and
    volume histories, however, and sending those histories once per security
    can exceed the reasoning provider request limit after broad liquidity
    scanning.  The model only needs identity and current summary fields for
    sector/discovery narration; omitting time-series payloads keeps the live
    path bounded without changing any investment decision or gate input.
    """
    keep = (
        "security_id", "ticker", "issuer_name", "name", "venue", "market",
        "sector", "industry", "price", "last_price", "market_cap",
        "average_volume", "average_dollar_volume", "approximate_dollar_volume",
        "currency", "liquidity_status", "liquidity_observed", "source",
    )
    compact: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        compact.append({key: row[key] for key in keep if key in row})
    return compact


@dataclass
class StockAgentConfig:
    library_root: Path
    database_path: Path
    worker_id: str = "stock-agent-worker"
    strict_inputs: bool = False
    market_data_provider: MarketDataProvider | None = None
    sec_provider: SECProvider | None = None
    portfolio_provider: PortfolioProvider | None = None
    research_provider: ResearchEvidenceProvider | None = None


class StockAgent:
    """Production orchestration path: every model stage is a leased WorkItem."""

    STAGE_PREREQUISITES = {
        "MARKET_ANALYSIS": (),
        "SECTOR_ANALYSIS": ("MARKET_ANALYSIS",),
        "STOCK_DISCOVERY": ("SECTOR_ANALYSIS",),
        "CAPITAL_PRESCREEN": ("STOCK_DISCOVERY",),
        "CAP_FUNDAMENTAL_CHANGE": ("CAPITAL_PRESCREEN",),
        "CAP_CATALYST_EXPECTATION_RESEARCH": ("CAP_FUNDAMENTAL_CHANGE",),
        "CAP_DIRECTIONAL_PROBABILITY": ("CAP_CATALYST_EXPECTATION_RESEARCH",),
        "DEEP_RESEARCH": ("CAP_DIRECTIONAL_PROBABILITY",),
        "FULL_SEC_FORENSIC": ("DEEP_RESEARCH",),
        "STANDARD_AUDIT": ("FULL_SEC_FORENSIC",),
        "ADVERSARIAL_AUDIT": ("STANDARD_AUDIT",),
        "PORTFOLIO_REVIEW": ("ADVERSARIAL_AUDIT",),
        "CAP_PROBABILITY_EDGE": ("PORTFOLIO_REVIEW",),
        "CAP_CATALYST_EXPECTATION_EXEC": ("CAP_PROBABILITY_EDGE",),
        "CAP_CAPITAL_FORENSICS": ("CAP_CATALYST_EXPECTATION_EXEC",),
        "CAP_ENTRY_READINESS": ("CAP_CAPITAL_FORENSICS",),
        "CAP_FAILURE_INVALIDATION": ("CAP_ENTRY_READINESS",),
        "FINAL_SYNTHESIS": ("CAP_FAILURE_INVALIDATION",),
    }

    LEGACY_STAGE_PREREQUISITES = {
        "MARKET_ANALYSIS": (),
        "SECTOR_ANALYSIS": ("MARKET_ANALYSIS",),
        "STOCK_DISCOVERY": ("SECTOR_ANALYSIS",),
        "CAPITAL_PRESCREEN": ("STOCK_DISCOVERY",),
        "DEEP_RESEARCH": ("CAPITAL_PRESCREEN",),
        "FULL_SEC_FORENSIC": ("DEEP_RESEARCH",),
        "ADVERSARIAL_AUDIT": ("FULL_SEC_FORENSIC",),
        "PORTFOLIO_REVIEW": ("ADVERSARIAL_AUDIT",),
        "FINAL_SYNTHESIS": ("PORTFOLIO_REVIEW",),
    }

    _RECORDED_PROVIDER_TYPES = (
        RecordedMarketDataProvider,
        RecordedPortfolioProvider,
        RecordedResearchEvidenceProvider,
        RecordedSECProvider,
    )

    @classmethod
    def _provider_evaluation_time(cls, artifact: Any, provider: Any) -> datetime | None:
        if isinstance(provider, cls._RECORDED_PROVIDER_TYPES):
            source_time = getattr(provider, "recorded_at", None)
            if source_time:
                try:
                    replay_clock = datetime.fromisoformat(str(source_time).replace("Z", "+00:00"))
                    if replay_clock.tzinfo is None:
                        replay_clock = replay_clock.replace(tzinfo=timezone.utc)
                    return replay_clock.astimezone(timezone.utc)
                except (TypeError, ValueError):
                    return None
        return None

    @classmethod
    def _require_provider_artifact_fresh(cls, artifact: Any, provider: Any, max_age_seconds: float, label: str, max_future_skew_seconds: float) -> None:
        require_artifact_fresh(
            artifact,
            max_age_seconds,
            label,
            max_future_skew_seconds,
            now=cls._provider_evaluation_time(artifact, provider),
        )

    PROMPT_FOR_STAGE = {
        "CAP_FUNDAMENTAL_CHANGE": ("capability.fundamental_change_quality", "FundamentalChangeQualityAssessmentV2"),
        "CAP_CATALYST_EXPECTATION_RESEARCH": ("capability.catalyst_expectation_gap", "CatalystExpectationGapAssessmentV2"),
        "CAP_DIRECTIONAL_PROBABILITY": ("capability.directional_probability_hypothesis", "DirectionalProbabilityHypothesisV2"),
        "STANDARD_AUDIT": ("adversarial.standard_audit", "AdversarialAuditResult"),
        "CAP_PROBABILITY_EDGE": ("capability.probability_edge_risk_asymmetry", "ProbabilityEdgeRiskAsymmetryAssessmentV2"),
        "CAP_CATALYST_EXPECTATION_EXEC": ("capability.catalyst_expectation_gap", "CatalystExpectationGapAssessmentV2"),
        "CAP_CAPITAL_FORENSICS": ("capability.capital_structure_forensics", "CapitalStructureForensicAssessmentV2"),
        "CAP_ENTRY_READINESS": ("capability.entry_readiness_execution_structure", "EntryReadinessExecutionAssessmentV2"),
        "CAP_FAILURE_INVALIDATION": ("capability.failure_scenarios_invalidation", "FailureScenarioInvalidationAssessmentV2"),
    }

    PROMPT_RESULT_STAGE = {
        "adversarial.standard_audit": "STANDARD_AUDIT",
        "workflow.portfolio_reviewer": "PORTFOLIO_REVIEW",
        "workflow.adversarial_reviewer": "ADVERSARIAL_AUDIT",
        "workflow.market_analyst": "MARKET_ANALYSIS",
        "workflow.sector_analyst": "SECTOR_ANALYSIS",
        "workflow.stock_scout": "STOCK_DISCOVERY",
        "workflow.stock_researcher": "DEEP_RESEARCH",
        "utility.sec_extraction": "FULL_SEC_FORENSIC",
    }

    PROMPT_CAPABILITY_STAGE = {
        "capability.fundamental_change_quality": "CAP_FUNDAMENTAL_CHANGE",
        "capability.catalyst_expectation_gap": "CAP_CATALYST_EXPECTATION_RESEARCH",
        "capability.directional_probability_hypothesis": "CAP_DIRECTIONAL_PROBABILITY",
        "capability.probability_edge_risk_asymmetry": "CAP_PROBABILITY_EDGE",
        "capability.capital_structure_forensics": "CAP_CAPITAL_FORENSICS",
        "capability.entry_readiness_execution_structure": "CAP_ENTRY_READINESS",
        "capability.failure_scenarios_invalidation": "CAP_FAILURE_INVALIDATION",
    }

    def __init__(self, config: StockAgentConfig | None = None, store: SQLiteStore | None = None, provider: Any | None = None, router: ModelRouter | None = None) -> None:
        root = Path(config.library_root) if config else canonical_prompt_library_root()
        db_path = Path(config.database_path) if config else Path(":memory:")
        self.config = config or StockAgentConfig(root, db_path)
        self.store = store or SQLiteStore(db_path)
        self.prompts = PromptRuntime(root)
        self.provider = provider or FakeProvider()
        self.router = router or ModelRouter({"fake": self.provider, "recorded": self.provider})
        self.cost_tracker = CostTracker(self.store)
        self.market_context_gate = MarketContextGate(); self.sector_gate = SectorGate(); self.stage_gate = StageGate(); self.prescreen_gate = CapitalPrescreenGate(); self.catalyst_gate = CatalystGate(); self.expectation_gap_gate = ExpectationGapGate(); self.market_execution_gate = MarketExecutionGate(); self.risk_engine = RiskEngine()
        self.market_normalizer = MarketNormalizer(); self.security_normalizer = SecurityNormalizer(); self.portfolio_normalizer = PortfolioNormalizer(); self.technical_calculator = TechnicalFeatureCalculator()
        self.qualified_candidate_gate = QualifiedCandidateGate(self.store); self.final_allocation_gate = FinalAllocationGate(self.store)

    def close(self) -> None: self.store.close()

    def _rules(self, data: dict[str, Any]) -> EffectiveRuleSet:
        if "rule_override" in data: raise ContractViolation("free-form rule_override is not authoritative")
        return self.store.resolve_rule_set(data.get("rule_override_id"))

    @staticmethod
    def _atom(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
        if "$ref" in schema: return StockAgent._atom(defs[schema["$ref"].split("/")[-1]], defs)
        if "const" in schema: return schema["const"]
        if "anyOf" in schema:
            return next((StockAgent._atom(x, defs) for x in schema["anyOf"] if x.get("type") != "null"), None)
        if "enum" in schema: return schema["enum"][0]
        typ = schema.get("type")
        if typ == "object": return {k: StockAgent._atom(schema.get("properties", {}).get(k, {}), defs) for k in schema.get("required", [])}
        if typ == "array": return [StockAgent._atom(schema.get("items", {}), defs) for _ in range(int(schema.get("minItems", 0)))]
        if typ == "integer": return 1
        if typ == "number": return 1.0
        if typ == "boolean": return False
        if schema.get("format") == "date-time": return "2026-08-17T00:00:00Z"
        if schema.get("pattern") == "^E[A-Za-z0-9._:-]+$": return "E-RECORDED"
        if schema.get("pattern") == "^[a-f0-9]{64}$": return "0" * 64
        return "recorded"

    def _valid_payload(self, schema_id: str, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        schema = copy.deepcopy(self.prompts.registry["schemas"][schema_id]); schema["$defs"] = self.prompts.registry["$defs"]
        payload = self._atom(schema, self.prompts.registry["$defs"])
        if patch: payload.update(patch)
        return payload

    def _profile_for_stage(self, stage: str) -> str:
        # Stage depth chooses a configured profile; provider names never
        # determine authority.  This keeps ordinary daily calls at the
        # configured baseline effort while audits may use the deeper profile.
        if stage in {"STANDARD_AUDIT", "ADVERSARIAL_AUDIT", "FINAL_SYNTHESIS"} and "CRITICAL_AUDIT" in self.router.profiles:
            return "CRITICAL_AUDIT"
        if stage in {"DEEP_RESEARCH", "FULL_SEC_FORENSIC"} and "DEEP_REASONING" in self.router.profiles:
            return "DEEP_REASONING"
        if stage in {"MARKET_ANALYSIS", "SECTOR_ANALYSIS", "STOCK_DISCOVERY", "CAPITAL_PRESCREEN", "PORTFOLIO_REVIEW"} and "BALANCED" in self.router.profiles:
            return "BALANCED"
        return "BALANCED"

    @staticmethod
    def _typed_context(source_stage: str, content_type: str, value: Any, receipt_id: str | None = None, receipt_type: str = "ContextReceiptV2") -> dict[str, Any]:
        content_hash = canonical_hash({"source_stage": source_stage, "content_type": content_type, "value": value})
        rid = receipt_id or f"input-receipt:{source_stage}:{content_hash}"
        receipt = {"receipt_type": receipt_type, "receipt_id": rid, "source_stage": source_stage, "content_type": content_type, "content_hash": content_hash}
        receipt["receipt_hash"] = canonical_hash(receipt)
        return {"source_stage": source_stage, "content_type": content_type, "value": value, "content_hash": content_hash, "upstream_receipt": receipt}

    def _bind_upstream_receipts(self, run, subject_id: str | None, supplied_context: dict[str, Any]) -> dict[str, Any]:
        """Bind semantic context to the latest repository-owned stage result.

        Raw provider inputs retain deterministic input receipts.  Whenever a
        named stage result exists, the receipt is upgraded to a concrete
        `stage-result:<id>` reference so the Prompt call cannot invent an
        upstream object outside the SQLite ledger.
        """
        stage_map = {"MARKET_ANALYSIS": "MARKET_ANALYSIS", "SECTOR_ANALYSIS": "SECTOR_ANALYSIS", "STOCK_DISCOVERY": "STOCK_DISCOVERY", "STAGE_GATE": "STAGE_GATE", "CAPITAL_PRESCREEN": "CAPITAL_PRESCREEN", "CAPITAL_GATE": "CAPITAL_PRESCREEN_GATE", "DEEP_RESEARCH": "DEEP_RESEARCH", "FULL_SEC_FORENSIC": "FULL_SEC_FORENSIC", "ADVERSARIAL_AUDIT": "ADVERSARIAL_AUDIT", "PORTFOLIO_REVIEW": "PORTFOLIO_REVIEW"}
        bound: dict[str, Any] = {}
        for key, value in supplied_context.items():
            if not isinstance(value, dict) or "source_stage" not in value:
                bound[key] = value
                continue
            typed = copy.deepcopy(value)
            source_stage = str(typed.get("source_stage"))
            lookup_stage = stage_map.get(source_stage)
            stage_row = self.store.get_stage_result(run.run_id, lookup_stage, subject_id) if lookup_stage else None
            if stage_row is None and lookup_stage and subject_id is not None:
                stage_row = self.store.get_stage_result(run.run_id, lookup_stage, None)
            if stage_row and stage_row.get("result_id"):
                try:
                    persisted_value = __import__("json").loads(stage_row.get("result_json") or "null")
                except (TypeError, ValueError):
                    persisted_value = None
                if persisted_value != typed.get("value"):
                    raise PromptContractError(f"{source_stage} receipt/value mismatch for {key}")
                receipt = {"receipt_type": "StageResultReceiptV2", "receipt_id": f"stage-result:{stage_row['result_id']}", "source_stage": source_stage, "content_type": typed.get("content_type"), "content_hash": typed.get("content_hash")}
                receipt["receipt_hash"] = canonical_hash(receipt)
                typed["upstream_receipt"] = receipt
            bound[key] = typed
        return bound

    def _capability_context(self, prompt_id: str, stage: str, candidate: dict[str, Any] | None, data: dict[str, Any], prior: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build typed inputs for a capability leaf without creating a second output owner."""
        candidate = candidate or {}
        prior = prior or {}
        research = candidate.get("research_result") or prior.get("research_result") or {}
        sec = candidate.get("sec_result") or prior.get("sec_result") or {}
        capital = candidate.get("capital_prescreen_result") or prior.get("capital_prescreen_result") or {}
        evidence = candidate.get("evidence_ids") or []
        economic = data.get("economic_assessment") or (data.get("economic_assessments") or {}).get(candidate.get("security_id"), {})
        values: dict[str, tuple[str, str, Any]] = {
            "target_analysis": ("DEEP_RESEARCH", "ResearchResult", research),
            "evidence_packet": ("EVIDENCE_STORE", "EvidencePacket", evidence),
            "issue_ledger": ("DEBATE_LEDGER", "IssueLedger", prior.get("issue_ledger") or []),
            "company_facts": ("SEC_CHEAP_PRESCREEN", "CompanyFacts", candidate.get("company_facts") or sec),
            "financial_statements": ("SEC_PROVIDER", "FinancialStatements", candidate.get("financial_statements") or sec),
            "filing_notes": ("SEC_PROVIDER", "FilingNotes", sec),
            "capital_structure_snapshot": ("SEC_CHEAP_PRESCREEN", "CapitalStructureSnapshot", capital),
            "sec_evidence": ("SEC_PROVIDER", "SECEvidence", sec),
            "research_context": ("DEEP_RESEARCH", "ResearchContext", research),
            "catalyst_evidence": ("RESEARCH_EVIDENCE", "CatalystEvidence", candidate.get("research_evidence") or research),
            "scenario_evidence": ("DEEP_RESEARCH", "ScenarioEvidence", research),
            # Economic scenarios are Python-owned execution inputs, not a
            # replay of the Deep Research leaf result.  Keeping a distinct
            # source stage prevents the repository receipt binder from
            # confusing the scenario value with the research narrative.
            "valuation_scenarios": ("PYTHON_ECONOMIC_RECEIPT", "ValuationScenarios", economic),
            "risk_engine_results": ("PYTHON_RISK_ENGINE", "RiskAssessment", prior.get("risk") or data.get("risk_inputs") or {}),
            "industry_overlay": ("INDUSTRY_DATA", "IndustryOverlay", data.get("industry_driver_snapshot") or {}),
            "fresh_price_snapshot": ("MARKET_EXECUTION", "PriceSnapshot", prior.get("market") or data.get("market_execution") or {}),
            "stage_assessment": ("STAGE_GATE", "StageAssessment", prior.get("stage_gate") or {}),
            "stage_gate_receipt": ("STAGE_GATE", "GateReceipt", prior.get("stage_gate") or {}),
            "research_result": ("DEEP_RESEARCH", "ResearchResult", research),
            "market_execution_gate_receipt": ("MARKET_EXECUTION_GATE", "GateReceipt", prior.get("market_gate") or {}),
            "risk_metrics": ("PYTHON_RISK_ENGINE", "RiskMetrics", prior.get("risk") or data.get("risk_inputs") or {}),
            "event_terms": ("DEEP_RESEARCH", "EventTerms", research),
            "event_evidence": ("RESEARCH_EVIDENCE", "EventEvidence", candidate.get("research_evidence") or research),
            "python_ev_calculation": ("PYTHON_ECONOMIC_RECEIPT", "EconomicAssessment", economic),
            "valuation_inputs": ("DEEP_RESEARCH", "ValuationInputs", economic),
            "price_snapshot": ("MARKET_EXECUTION", "PriceSnapshot", prior.get("market") or data.get("market_execution") or {}),
            "earnings_evidence": ("RESEARCH_EVIDENCE", "EarningsEvidence", candidate.get("research_evidence") or research),
            "contract_evidence": ("RESEARCH_EVIDENCE", "ContractEvidence", candidate.get("research_evidence") or research),
        }
        result: dict[str, Any] = {}
        for required in self.prompts.prompts[prompt_id].get("required_inputs", []):
            if required in {"effective_rule_pack", "run_mode"}:
                continue
            source_stage, content_type, value = values.get(required, (stage, required, {}))
            result[required] = self._typed_context(source_stage, content_type, value)
        return result

    def _run_capability(self, run, stage: str, candidate: dict[str, Any], data: dict[str, Any], prior: dict[str, Any] | None = None) -> dict[str, Any]:
        prompt_id, schema_id = self.PROMPT_FOR_STAGE[stage]
        context_inputs = self._capability_context(prompt_id, stage, candidate, data, prior)
        raw_input = {"candidate": candidate, "prior_results": prior or {}, "stage": stage}
        default = self._valid_payload(schema_id)
        if schema_id == "AdversarialAuditResult":
            default.update({"audit_recommendation": "SUPPORTS_CONTINUATION", "unresolved_critical": []})
        return self._work_stage(run, stage, prompt_id, {"raw_input": raw_input, "default_payload": default}, candidate.get("security_id"), list(candidate.get("evidence_ids") or []), context_inputs)

    def _declared_dependency_contexts(self, run, prompt_id: str, subject_id: str | None) -> dict[str, dict[str, Any]]:
        """Materialize canonical prior Prompt results into the parent context."""
        metadata = self.prompts.prompts.get(prompt_id, {})
        declared = list(metadata.get("requires_results", []) or []) + list(metadata.get("requires_capabilities", []) or [])
        contexts: dict[str, dict[str, Any]] = {}
        for dependency_prompt_id in sorted(set(str(item) for item in declared)):
            stage = self.PROMPT_RESULT_STAGE.get(dependency_prompt_id) or self.PROMPT_CAPABILITY_STAGE.get(dependency_prompt_id)
            if dependency_prompt_id == "capability.catalyst_expectation_gap" and prompt_id == "workflow.final_synthesis_agent":
                stage = "CAP_CATALYST_EXPECTATION_EXEC"
            if not stage:
                raise ContractViolation(f"declared dependency has no runtime stage: {dependency_prompt_id}")
            row = self.store.get_stage_result(run.run_id, stage, subject_id)
            if row is None and subject_id is not None:
                row = self.store.get_stage_result(run.run_id, stage, None)
            if not row or row.get("status") != "SUCCEEDED":
                raise ContractViolation(f"declared dependency result missing: {dependency_prompt_id}")
            try:
                value = json.loads(row.get("result_json") or "null")
            except (TypeError, ValueError) as exc:
                raise ContractViolation(f"declared dependency result malformed: {dependency_prompt_id}") from exc
            schema_id = str(metadata_for_dependency := self.prompts.prompts.get(dependency_prompt_id, {}).get("output_schema") or "")
            if not schema_id or not isinstance(value, dict):
                raise ContractViolation(f"declared dependency schema missing: {dependency_prompt_id}")
            schema_errors = self.prompts.validate(schema_id, value)
            if schema_errors:
                raise ContractViolation(f"declared dependency schema invalid: {dependency_prompt_id}")
            typed = self._typed_context(f"PROMPT:{dependency_prompt_id}", schema_id, value, receipt_id=f"stage-result:{row['result_id']}", receipt_type="StageResultReceiptV2")
            contexts[f"PROMPT:{dependency_prompt_id}"] = typed
        return contexts

    def _work_stage(self, run, stage: str, prompt_id: str, payload: dict[str, Any], subject_id: str | None, dependency_ids: list[str], context_inputs: dict[str, Any] | None = None) -> dict[str, Any]:
        dep_hash = self.store.dependency_hash(dependency_ids, run.rule_set.rule_set_hash, run.context_manifest_hash)
        # Zero-dependency stages use the deterministic zero epoch at commit;
        # unrelated provider Evidence must not make MARKET_ANALYSIS stale.
        run.evidence_epoch = self.store.current_evidence_epoch_for(dependency_ids) if dependency_ids else 0
        required_stages = list((self.STAGE_PREREQUISITES if self.config.strict_inputs else self.LEGACY_STAGE_PREREQUISITES).get(stage, ()))
        metadata = self.prompts.prompts.get(prompt_id, {})
        for result_id in (metadata.get("requires_results", []) or []) if self.config.strict_inputs else ():
            dependency_stage = self.PROMPT_RESULT_STAGE.get(str(result_id))
            if dependency_stage and dependency_stage not in required_stages:
                required_stages.append(dependency_stage)
        for capability_id in (metadata.get("requires_capabilities", []) or []) if self.config.strict_inputs else ():
            dependency_stage = self.PROMPT_CAPABILITY_STAGE.get(str(capability_id))
            if str(capability_id) == "capability.catalyst_expectation_gap" and stage == "FINAL_SYNTHESIS":
                dependency_stage = "CAP_CATALYST_EXPECTATION_EXEC"
            if dependency_stage and dependency_stage not in required_stages:
                required_stages.append(dependency_stage)
        prerequisite_ids = [self.store.work_item_id_for_stage(run.run_id, prerequisite) for prerequisite in required_stages]
        if any(identifier is None for identifier in prerequisite_ids):
            raise ContractViolation(f"semantic prerequisite missing for {stage}: {required_stages}")
        prerequisites = {"work_item_ids": [identifier for identifier in prerequisite_ids if identifier]}
        payload = {**payload, "dependency_ids": dependency_ids, "prerequisites": prerequisites}; item = self.store.enqueue(run, stage, payload, dep_hash); leased = self.store.lease_next(self.config.worker_id)
        if leased is None or leased.work_item_id != item.work_item_id: raise ContractViolation(f"unable to lease {stage}")
        if not self.store.heartbeat(leased.work_item_id, leased.lease_token or ""): raise ContractViolation(f"heartbeat failed {stage}")
        profile_name = self._profile_for_stage(stage); profile = self.router.profiles[profile_name]; reservation = self.cost_tracker.reserve(run.run_id, leased.work_item_id, prompt_id, profile)
        context_data = {"run_id": run.run_id, "stage": stage, "run_mode": run.mode.value, "subject_id": subject_id or "RUN", "effective_rule_pack": run.rule_set.rule_set_hash, "evidence_receipts": dependency_ids}
        supplied_context = self._bind_upstream_receipts(run, subject_id, dict(context_inputs or payload.get("context_inputs") or {}))
        if self.config.strict_inputs:
            supplied_context.update(self._declared_dependency_contexts(run, prompt_id, subject_id))
        for required_input in self.prompts.prompts[prompt_id].get("required_inputs", []):
            if required_input in {"effective_rule_pack", "run_mode"}:
                continue
            if required_input not in supplied_context:
                raise PromptContractError(f"typed upstream context missing for {prompt_id}: {required_input}")
            value = supplied_context[required_input]
            if not isinstance(value, dict) or "source_stage" not in value or "content_type" not in value or "value" not in value:
                raise PromptContractError(f"untyped upstream context for {prompt_id}: {required_input}")
            context_data[required_input] = value
        context_data["semantic_context"] = True
        context_data["enforce_declared_dependencies"] = bool(self.config.strict_inputs)
        for dependency_key, dependency_value in supplied_context.items():
            if dependency_key.startswith("PROMPT:"):
                context_data[dependency_key] = dependency_value
        context_data["upstream_receipt_ids"] = [str((value.get("upstream_receipt") or {}).get("receipt_id")) for value in supplied_context.values() if isinstance(value, dict) and isinstance(value.get("upstream_receipt"), dict)]
        context = self.prompts.context_manifest(context_data, ["run_id", "stage", "run_mode", "effective_rule_pack"])
        call_telemetry: dict[str, Any] = {}
        def call(request: dict[str, Any]) -> Any:
            nonlocal call_telemetry
            provider = self.router.providers[profile.provider]
            if isinstance(provider, (FakeProvider, RecordedProvider)) or getattr(provider, "provider", "") in {"fake", "recorded"}:
                request["default_payload"] = payload["default_payload"]
            runtime_input = payload.get("raw_input", {})
            self.prompts.validate_untrusted_data(runtime_input)
            request["runtime_input"] = runtime_input
            # Responses may return a syntactically valid object whose
            # model-generated receipt hashes are not the exact Python-owned
            # values.  Defer the provider's duplicate schema check so we can
            # bind those deterministic receipts, then PromptRuntime performs
            # the canonical validation before persistence.
            request["defer_provider_schema_validation"] = True
            result, telemetry = self.router.call(profile_name, request)
            if profile.provider == "luna":
                result = _inject_python_receipts(result, supplied_context, request.get("output_schema_definition") or {})
            call_telemetry = dict(telemetry or {})
            return result
        try:
            result = self.prompts.strict_call(prompt_id, call, context=context, run_mode=run.mode.value)
            # Do not let later evidence-list mutations alter the value that
            # was validated and persisted for this WorkItem receipt.
            result = copy.deepcopy(result)
            status = self.store.complete(leased, result, dep_hash, run.evidence_epoch, run.rule_set.rule_set_hash, run.context_manifest_hash)
            if status.value != "SUCCEEDED": raise ContractViolation(f"{stage} STALE_ON_ARRIVAL")
            self.store.record_stage_result(run.run_id, leased.work_item_id, stage, subject_id, result, dependency_ids, dep_hash, self.store.current_evidence_epoch_for(dependency_ids))
            telemetry = call_telemetry
            retry_count = int(telemetry.get("retry_count", 0))
            self.cost_tracker.settle(reservation, telemetry, retry_count)
            self.store.record_model_call(run.run_id, leased.work_item_id, prompt_id, telemetry, retry_count)
            return result
        except PromptContractError as exc:
            self.store.retry(leased.work_item_id, "structured_call_or_contract_failure"); raise ContractViolation(f"{stage}: {exc}") from exc
        except Exception:
            self.store.retry(leased.work_item_id, "structured_call_or_contract_failure"); raise

    def _seed_evidence(self, candidate: dict[str, Any], observed_at: str | None = None, source_class: str = "RECORDED_PROVIDER") -> list[str]:
        ids = list(candidate.get("evidence_ids") or [f"E-{candidate['security_id']}-RAW-1"])
        source_time = observed_at or candidate.get("source_observed_at") or utc_now()
        for eid in ids:
            # Every seed receipt is backed by a persisted raw observation.  A
            # bare fixture Evidence row is not sufficient for authoritative
            # reporting or for a live provider path.
            payload = {
                "evidence_id": str(eid),
                "security_id": candidate["security_id"],
                "source_class": source_class,
                "source_url": candidate.get("source_url"),
                "source_observed_at": source_time,
            }
            artifact = RawArtifact(
                f"artifact-seed-{canonical_hash(payload)}",
                source_class,
                "SEED_EVIDENCE",
                candidate["security_id"],
                source_time,
                payload,
                canonical_hash(payload),
                source_time,
                utc_now(),
            )
            self.store.save_raw_artifact(artifact)
            self.store.upsert_evidence(Evidence(str(eid), candidate["security_id"], source_class, source_time, 0, artifact.payload_hash, "RECORDED", raw_artifact_id=artifact.artifact_id))
        return ids

    def _qualified_candidates(self, data: dict[str, Any], run) -> tuple[list[dict[str, Any]], str | None]:
        raw_candidates = data.get("candidates") or data.get("raw_universe") or []
        market_raw = data.get("market_context", {})
        market = self._work_stage(run, "MARKET_ANALYSIS", "workflow.market_analyst", {"raw_input": market_raw, "default_payload": self._valid_payload("MarketContextExecutionAssessmentV2", {"run_mode": run.mode.value})}, None, [], {"market_snapshot": self._typed_context("MARKET_DATA", "MarketContext", market_raw), "market_breadth": self._typed_context("MARKET_DATA", "Breadth", market_raw.get("breadth")), "sector_relative_strength": self._typed_context("MARKET_DATA", "SectorRelativeStrength", market_raw.get("sector_relative_strength", {}))})
        market_gate = self.market_context_gate.evaluate(market_raw, run.rule_set)
        if market_gate.decision != GateDecision.PASS: return [], "MARKET_CONTEXT_REJECTED"
        sector_raw = data.get("sector", {})
        sector = self._work_stage(run, "SECTOR_ANALYSIS", "workflow.sector_analyst", {"raw_input": sector_raw, "default_payload": self._valid_payload("SectorOpportunityAssessmentV2")}, None, [], {"market_context_result": self._typed_context("MARKET_ANALYSIS", "MarketAnalysisResult", market), "sector_data_packet": self._typed_context("SECTOR_DATA", "SectorData", sector_raw), "industry_driver_snapshot": self._typed_context("INDUSTRY_DATA", "IndustryDriverSnapshot", sector_raw.get("industry_driver_snapshot", {})), "market_context_gate_receipt": self._typed_context("MARKET_CONTEXT_GATE", "GateReceipt", market_gate.as_dict())})
        if self.sector_gate.evaluate({"eligible": True}, run.rule_set).decision != GateDecision.PASS: return [], "SECTOR_REJECTED"
        sector_receipt = self.sector_gate.evaluate({"eligible": True}, run.rule_set).as_dict(); candidates_payload = []
        for raw in raw_candidates:
            requested_discovery = raw.get("recommended_discovery_action", "EXCLUDE")
            if requested_discovery not in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY", "WATCH_STAGE0", "WATCH_RESET", "EXCLUDE"}: requested_discovery = "EXCLUDE"
            candidates_payload.append({"security_id": raw.get("security_id", ""), "recommended_discovery_action": requested_discovery, "proposed_stage": raw.get("proposed_stage", "UNKNOWN"), "rationale": "recorded raw universe", "evidence_ids": raw.get("evidence_ids", [f"E-{raw.get('security_id','UNKNOWN')}-RAW-1"])})
        discovery = self._work_stage(run, "STOCK_DISCOVERY", "workflow.stock_scout", {"raw_input": raw_candidates, "default_payload": self._valid_payload("DiscoveryCandidateSetV2", {"run_mode": run.mode.value, "sector_gate_receipt": sector_receipt, "candidates": candidates_payload})}, None, [], {"approved_sector_context": self._typed_context("SECTOR_ANALYSIS", "SectorAnalysisResult", sector), "sector_gate_receipt": self._typed_context("SECTOR_GATE", "GateReceipt", sector_receipt), "industry_driver_snapshot": self._typed_context("INDUSTRY_DATA", "IndustryDriverSnapshot", sector_raw.get("industry_driver_snapshot", {})), "candidate_universe_packet": self._typed_context("MARKET_DATA", "RawUniverse", raw_candidates), "deterministic_filter_results": self._typed_context("PYTHON_DISCOVERY_FILTER", "FilterResult", candidates_payload), "technical_feature_snapshot": self._typed_context("PYTHON_TECHNICAL_FEATURES", "TechnicalFeatures", {})})
        qualified: list[dict[str, Any]] = []
        for found in discovery.get("candidates", []):
            raw = next((x for x in raw_candidates if x.get("security_id") == found.get("security_id")), {}); sid = found.get("security_id")
            if not sid or found.get("recommended_discovery_action") not in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}: continue
            eids = self._seed_evidence({**raw, "security_id": sid, "evidence_ids": found.get("evidence_ids")})
            stage = self.stage_gate.evaluate(found.get("proposed_stage", "UNKNOWN"), found.get("proposed_stage") in {"STAGE_0", "STAGE_1", "STAGE_2"}, run.rule_set)
            self.store.record_stage_result(run.run_id, None, "STAGE_GATE", sid, stage.as_dict(), eids, self.store.dependency_hash(eids, run.rule_set.rule_set_hash, run.context_manifest_hash), self.store.current_evidence_epoch_for(eids))
            if stage.decision != GateDecision.PASS: continue
            raw_capital = raw.get("capital_prescreen") or {}
            def tri(k: str) -> dict[str, str]:
                v = raw_capital.get(k, False)
                state = str(v.get("state", "UNKNOWN")).upper() if isinstance(v, dict) else ("TRUE" if v is True else "FALSE" if v is False else "UNKNOWN")
                return {"state": state, "details": {"summary": "recorded capital fact", "evidence_ids": eids, "unknowns": []}, "evidence_ids": eids}
            prescreen = self._work_stage(run, "CAPITAL_PRESCREEN", "utility.capital_structure_prescreen", {"raw_input": raw_capital, "default_payload": self._valid_payload("CapitalStructurePrescreenResultV2", {"extraction_status": "COMPLETE", "identity_status": "CONFIRMED", "active_atm": tri("active_atm"), "large_shelf_and_financing_need": tri("large_shelf_and_financing_need"), "toxic_convertible": tri("toxic_convertible"), "material_warrant": tri("material_warrant"), "imminent_financing": tri("imminent_financing"), "cash_runway_critical": tri("cash_runway_critical"), "evidence_ids": eids, "unknowns": []})}, sid, eids, {"security_identity": self._typed_context("SECURITY_NORMALIZATION", "SecurityIdentity", raw), "cheap_sec_packet": self._typed_context("RECORDED_INPUT", "CheapSECObservation", raw_capital), "stage_gate_receipt": self._typed_context("STAGE_GATE", "GateReceipt", stage.as_dict())})
            gate = self.prescreen_gate.evaluate({**prescreen, "complete": prescreen.get("extraction_status") == "COMPLETE"}, run.rule_set)
            self.store.record_stage_result(run.run_id, None, "CAPITAL_PRESCREEN_GATE", sid, gate.as_dict(), eids, self.store.dependency_hash(eids, run.rule_set.rule_set_hash, run.context_manifest_hash), self.store.current_evidence_epoch_for(eids))
            if gate.decision not in {GateDecision.PASS, GateDecision.PASS_WITH_CONSTRAINTS}: continue
            failures = raw.get("failure_paths") or self._default_failure_paths(eids)
            try: validate_failure_paths(failures)
            except ContractViolation: continue
            if self.config.strict_inputs:
                capability_candidate = {**raw, "security_id": sid, "evidence_ids": eids, "capital_prescreen_result": prescreen, "stage_gate": stage.as_dict()}
                capability_results = {}
                for capability_stage in ("CAP_FUNDAMENTAL_CHANGE", "CAP_CATALYST_EXPECTATION_RESEARCH", "CAP_DIRECTIONAL_PROBABILITY"):
                    capability_results[capability_stage] = self._run_capability(run, capability_stage, capability_candidate, data, capability_results)
            research = self._work_stage(run, "DEEP_RESEARCH", "workflow.stock_researcher", {"raw_input": raw, "default_payload": self._valid_payload("StockResearchResultV2", {"research_status": "COMPLETE" if raw.get("research_status", "COMPLETE") == "COMPLETE" else "INCOMPLETE", "failure_paths": self._schema_failure_paths(failures, eids), "evidence_ids": eids})}, sid, eids, {"candidate_context": self._typed_context("STOCK_DISCOVERY", "CandidateContext", discovery), "industry_driver_snapshot": self._typed_context("INDUSTRY_DATA", "IndustryDriverSnapshot", sector_raw.get("industry_driver_snapshot", {})), "capital_prescreen_extraction_receipt": self._typed_context("CAPITAL_PRESCREEN", "PrescreenResult", prescreen), "evidence_packet": self._typed_context("EVIDENCE_STORE", "EvidencePacket", eids), "company_facts": self._typed_context("SEC_CHEAP_PRESCREEN", "CompanyFacts", {}), "industry_overlay": self._typed_context("INDUSTRY_DATA", "IndustryOverlay", {}), "capital_prescreen_gate_receipt": self._typed_context("CAPITAL_GATE", "GateReceipt", gate.as_dict()), "stage_gate_receipt": self._typed_context("STAGE_GATE", "GateReceipt", stage.as_dict())})
            if research.get("research_status") != "COMPLETE": continue
            sec = self._work_stage(run, "FULL_SEC_FORENSIC", "utility.sec_extraction", {"raw_input": raw, "default_payload": self._valid_payload("SECExtractionResultV2", {"status": "COMPLETE"})}, sid, eids, {"sec_document": self._typed_context("SEC_PROVIDER", "SECArtifacts", {}), "sec_targets": self._typed_context("SECURITY_NORMALIZATION", "SECTargets", {"security_id": sid})})
            if sec.get("status") != "COMPLETE": continue
            if self.config.strict_inputs:
                standard_audit = self._run_capability(run, "STANDARD_AUDIT", {**capability_candidate, "research_result": research, "sec_result": sec}, data, {"research_result": research, "issue_ledger": self.store.list_debate_issues(run.run_id, sid)})
                self._persist_audit_issues(run, sid, standard_audit, eids)
            audit = self._work_stage(run, "ADVERSARIAL_AUDIT", "workflow.adversarial_reviewer", {"raw_input": raw, "default_payload": self._valid_payload("AdversarialReviewResultV2", {"audit_recommendation": "SUPPORTS_CONTINUATION", "failure_paths": self._schema_failure_paths(failures, eids)})}, sid, eids, {"research_result": self._typed_context("DEEP_RESEARCH", "ResearchResult", research), "evidence_packet": self._typed_context("EVIDENCE_STORE", "EvidencePacket", eids), "issue_ledger": self._typed_context("DEBATE_LEDGER", "IssueLedger", self.store.list_debate_issues(run.run_id, sid) if hasattr(self.store, "list_debate_issues") else [])})
            self._persist_audit_issues(run, sid, audit, eids)
            if audit.get("audit_recommendation") in {"CHALLENGES_CONTINUATION", "AUDIT_EVIDENCE_INCOMPLETE"} or raw.get("audit_recommendation") in {"CHALLENGES_CONTINUATION", "AUDIT_EVIDENCE_INCOMPLETE"}: continue
            qualified_status, _ = self.store.qualified_candidate_status(run.run_id, sid, strict=self.config.strict_inputs)
            if not qualified_status: continue
            qualified.append({**raw, **found, "security_id": sid, "evidence_ids": eids, "failure_paths": failures, "research_result": research, "sec_result": sec, "audit_result": audit})
        return qualified, None

    @staticmethod
    def _default_failure_paths(eids: list[str]) -> list[dict[str, Any]]:
        return [{"category": c, "scenario": f"{c.lower()} deterioration", "causal_path": f"{c.lower()} -> thesis damage", "probability_direction": "INCREASES_DOWNSIDE", "severity": "MAJOR", "source_evidence_ids": eids[:1]} for c in ("FUNDAMENTAL", "CAPITAL_STRUCTURE", "PRICING_EXPECTATION")]

    @staticmethod
    def _schema_failure_paths(paths: list[dict[str, Any]], eids: list[str]) -> list[dict[str, Any]]:
        result = []
        for p in paths:
            src = list(p.get("source_evidence_ids") or eids[:1])
            result.append({"category": p.get("category", "FUNDAMENTAL"), "severity": p.get("severity", "MAJOR"), "scenario": p.get("scenario", "unknown"), "causal_path": p.get("causal_path", "unknown"), "probability_direction": p.get("probability_direction", "UNKNOWN"), "impact": {"summary": str(p.get("impact", p.get("details", "material downside"))), "evidence_ids": src, "unknowns": []}, "observable_trigger": p.get("observable_trigger", "monitoring trigger"), "thesis_invalidation_link": p.get("thesis_invalidation_link", "thesis invalidation"), "source_evidence_ids": src})
        return result

    def _persist_audit_issues(self, run, subject_id: str, audit: dict[str, Any], evidence_ids: list[str]) -> list[str]:
        issue_ids: list[str] = []
        unresolved = list(audit.get("unresolved_critical_issues") or [])
        unresolved.extend({"severity": "CRITICAL", "finding": str(item)} for item in list(audit.get("unresolved_critical") or []))
        for issue in unresolved:
            if not isinstance(issue, dict):
                continue
            severity = str(issue.get("severity", "CRITICAL")).upper()
            finding = str(issue.get("finding") or "unresolved audit issue")
            issue_ids.append(self.store.record_debate_issue(run.run_id, subject_id, severity, finding, "OPEN"))
        return issue_ids

    def _persist_hunt_reverse_valuation(
        self,
        run,
        candidate: dict[str, Any],
        evidence_ids: list[str],
        research_artifact: RawArtifact,
        research_evidence_id: str,
        universe_artifact: RawArtifact,
        raw_row: dict[str, Any],
    ) -> tuple[bool, Any]:
        """Derive the HUNT expectation gap from persisted numeric evidence.

        Caller-supplied economic_assessment values are intentionally ignored.
        Current price comes from the market/universe observation; company and
        benchmark inputs must be structured inside the persisted research
        artifact.  Python owns all valuation arithmetic and gate thresholds.
        """
        inputs = extract_valuation_inputs(research_artifact.payload)
        current_price, price_source = observed_market_price(raw_row)
        receipt = None
        market_evidence_id = ""
        market_input_artifact = None
        if current_price is not None:
            market_payload = {
                "security_id": candidate["security_id"],
                "current_price": float(current_price),
                "price_source": price_source,
                "source_universe_artifact_id": universe_artifact.artifact_id,
                "source_universe_payload_hash": universe_artifact.payload_hash,
            }
            market_input_artifact = RawArtifact(
                f"artifact-valuation-market-{canonical_hash(market_payload)}",
                "python-valuation",
                "VALUATION_MARKET_INPUT",
                candidate["security_id"],
                universe_artifact.observed_at,
                market_payload,
                canonical_hash(market_payload),
                universe_artifact.source_observed_at or universe_artifact.observed_at,
                utc_now(),
            )
            self.store.save_raw_artifact(market_input_artifact)
            market_evidence_id = f"E-VALUATION_MARKET:{market_input_artifact.payload_hash}"
            self.store.upsert_evidence(Evidence(
                market_evidence_id, candidate["security_id"], "MARKET_DATA",
                market_input_artifact.source_observed_at or market_input_artifact.observed_at,
                0, market_input_artifact.payload_hash, "RAW", raw_artifact_id=market_input_artifact.artifact_id,
            ))
            if market_evidence_id not in evidence_ids:
                evidence_ids.append(market_evidence_id)
        source_result_ids: list[str] = []
        for stage in ("CAP_CATALYST_EXPECTATION_RESEARCH", "DEEP_RESEARCH", "FULL_SEC_FORENSIC", "ADVERSARIAL_AUDIT"):
            row = self.store.get_stage_result(run.run_id, stage, candidate["security_id"])
            if row and row.get("status") == "SUCCEEDED":
                source_result_ids.append(str(row["result_id"]))
        if isinstance(inputs, dict) and current_price is not None and market_input_artifact is not None:
            receipt = build_reverse_valuation_receipt(
                security_id=candidate["security_id"],
                current_price=float(current_price),
                valuation_inputs=inputs,
                market_artifact_id=market_input_artifact.artifact_id,
                research_artifact_id=research_artifact.artifact_id,
                market_evidence_id=market_evidence_id,
                research_evidence_id=research_evidence_id,
                source_result_ids=source_result_ids,
            )
        gate = self.expectation_gap_gate.evaluate(receipt, run.rule_set)
        dependency_ids = list(dict.fromkeys(evidence_ids))
        if receipt is not None:
            reverse_payload = receipt.as_dict()
            reverse_artifact = RawArtifact(
                f"artifact-reverse-valuation-{receipt.calculation_hash}",
                "python-valuation",
                "REVERSE_VALUATION",
                candidate["security_id"],
                utc_now(),
                reverse_payload,
                canonical_hash(reverse_payload),
                research_artifact.source_observed_at or research_artifact.observed_at,
                utc_now(),
            )
            self.store.save_raw_artifact(reverse_artifact)
            reverse_evidence_id = f"E-REVERSE_VALUATION:{reverse_artifact.payload_hash}"
            self.store.upsert_evidence(Evidence(
                reverse_evidence_id, candidate["security_id"], "python-valuation",
                reverse_artifact.source_observed_at or reverse_artifact.observed_at,
                0, reverse_artifact.payload_hash, "DERIVED", raw_artifact_id=reverse_artifact.artifact_id,
            ))
            if reverse_evidence_id not in evidence_ids:
                evidence_ids.append(reverse_evidence_id)
            dependency_ids = list(dict.fromkeys(evidence_ids))
            candidate["reverse_valuation"] = reverse_payload
        candidate["expectation_gap_gate_receipt"] = gate.as_dict()
        dep_hash = self.store.dependency_hash(dependency_ids, run.rule_set.rule_set_hash, run.context_manifest_hash)
        self.store.record_stage_result(
            run.run_id, None, "EXPECTATION_GAP_GATE", candidate["security_id"], gate.as_dict(),
            dependency_ids, dep_hash, self.store.current_evidence_epoch_for(dependency_ids),
        )
        return receipt is not None, gate

    def _persisted_stage_value(self, run, stage: str, subject_id: str | None) -> dict[str, Any]:
        row = self.store.get_stage_result(run.run_id, stage, subject_id)
        if not row or row.get("status") != "SUCCEEDED":
            raise ContractViolation(f"required authoritative stage result is missing: {stage}")
        try:
            value = json.loads(row.get("result_json") or "{}")
        except (TypeError, ValueError) as exc:
            raise ContractViolation(f"authoritative stage result is malformed: {stage}") from exc
        if not isinstance(value, dict):
            raise ContractViolation(f"authoritative stage result is not an object: {stage}")
        return value

    def _strict_gate_snapshot(self, run, subject_id: str, market_execution_gate: dict[str, Any]) -> dict[str, Any]:
        """Build the Final Synthesis gate context only from persisted Python results."""
        snapshot = {
            "stage_gate": self._persisted_stage_value(run, "STAGE_GATE", subject_id),
            "capital_prescreen_gate": self._persisted_stage_value(run, "CAPITAL_PRESCREEN_GATE", subject_id),
            "catalyst_gate": self._persisted_stage_value(run, "CATALYST_GATE", subject_id),
            "expectation_gap_gate": self._persisted_stage_value(run, "EXPECTATION_GAP_GATE", subject_id),
            "full_sec_forensic": self._persisted_stage_value(run, "FULL_SEC_FORENSIC", subject_id),
            "standard_audit": self._persisted_stage_value(run, "STANDARD_AUDIT", subject_id),
            "adversarial_audit": self._persisted_stage_value(run, "ADVERSARIAL_AUDIT", subject_id),
            "market_execution_gate": market_execution_gate,
        }
        return snapshot

    @staticmethod
    def _enforce_synthesis(action: ExecutionAction, synthesis: dict[str, Any]) -> None:
        actionable = {ExecutionAction.STARTER, ExecutionAction.ADD, ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT}
        status = synthesis.get("recommendation_status")
        recommended = synthesis.get("recommended_action")
        if action in actionable and (status != "READY" or recommended != action.value):
            raise ContractViolation("Final Synthesis is not READY for the requested authoritative action")

    def run(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
        if self.config.strict_inputs:
            return self._run_strict(mode, data)
        try: rules = self._rules(data)
        except (ContractViolation, ValueError) as exc: return RunOutcome("unstarted", mode, "BLOCKED_BY_CRITICAL_ISSUE", blocked_reason=str(exc))
        context = self.prompts.context_manifest({"run_mode": mode.value, "effective_rule_pack": rules.rule_set_hash}, ["run_mode", "effective_rule_pack"])
        run = self.store.create_run(mode, rules, context["manifest_hash"], int(data.get("evidence_epoch", 0)))
        try:
            candidates, block = self._qualified_candidates(data, run); ids = tuple(c["security_id"] for c in candidates)
            if not candidates:
                outcome = "NO_QUALIFIED_CANDIDATE" if mode == RunMode.HUNT_ONLY else "BLOCKED_BY_EVIDENCE_GAP"; self.store.finish_run(run.run_id, outcome); return RunOutcome(run.run_id, mode, outcome, ids, blocked_reason=block)
            if mode == RunMode.HUNT_ONLY: self.store.finish_run(run.run_id, "QUALIFIED_CANDIDATE_POOL"); return RunOutcome(run.run_id, mode, "QUALIFIED_CANDIDATE_POOL", ids)
            ranked = self._rank_execution_candidates(candidates, data)
            return self._execution_review(run, ranked[0], data, ids)
        except (ContractViolation, PromptContractError, RuntimeError, ValueError) as exc:
            terminal_outcome, reason = self._terminal_block_for_exception(exc)
            self.store.finish_run(run.run_id, terminal_outcome)
            return RunOutcome(run.run_id, mode, terminal_outcome, (), blocked_reason=reason)

    @staticmethod
    def _terminal_block_for_exception(exc: Exception) -> tuple[str, str]:
        """Map deterministic evidence failures to precise terminal reasons."""
        message = str(exc)
        lowered = message.lower()
        if "market execution" in lowered or "marketexecutiongate" in lowered:
            return "BLOCKED_BY_EVIDENCE_GAP", "MARKET_EXECUTION_INCOMPLETE"
        return "BLOCKED_BY_CRITICAL_ISSUE", message

    def _persist_market_asset_artifacts(self, context_payload: dict[str, Any]) -> None:
        """Persist per-asset raw observations and exact Evidence receipts.

        Providers may return an aggregate context for prompt composition, but
        each required asset must remain independently auditable in SQLite.
        This helper only persists provider observations; it never computes a
        gate decision or accepts a provider completeness claim.
        """
        for raw in context_payload.get("asset_raw_artifacts") or []:
            if not isinstance(raw, dict):
                continue
            artifact_id = str(raw.get("artifact_id") or "")
            provider = str(raw.get("provider") or "")
            payload = raw.get("payload")
            subject_id = str(raw.get("subject_id") or "")
            observed_at = str(raw.get("observed_at") or "")
            payload_hash = str(raw.get("payload_hash") or "")
            if not artifact_id or not provider or not isinstance(payload, dict) or not subject_id or not observed_at or not payload_hash:
                raise ContractViolation("market asset raw artifact is incomplete")
            artifact = RawArtifact(
                artifact_id,
                provider,
                str(raw.get("artifact_type") or "MARKET_CONTEXT_ASSET"),
                subject_id,
                observed_at,
                payload,
                payload_hash,
                str(raw.get("source_observed_at") or observed_at),
                str(raw.get("retrieved_at") or utc_now()),
            )
            self.store.save_raw_artifact(artifact)
            evidence_id = str(raw.get("evidence_id") or f"E-{artifact_id}")
            self.store.upsert_evidence(Evidence(
                evidence_id,
                subject_id,
                provider,
                artifact.source_observed_at or artifact.observed_at,
                0,
                artifact.payload_hash,
                "RAW",
                raw_artifact_id=artifact.artifact_id,
            ))

    def _run_strict(self, mode: RunMode, data: dict[str, Any]) -> RunOutcome:
        """Provider-backed production path with fail-closed raw-input handling."""
        try:
            rules = self._rules(data)
            market_provider = self.config.market_data_provider
            if market_provider is None:
                raise ContractViolation("strict production mode requires MarketDataProvider")
            context_artifact = market_provider.fetch_market_context(data.get("market_query") or {})
            # Provider labels are observations, not an authoritative gate
            # decision.  Completeness is accepted only from a verified
            # provider flag or deterministic Python normalization of raw data.
            context_payload = dict(context_artifact.payload)
            self._require_provider_artifact_fresh(context_artifact, market_provider, rules.max_age_market_context_hours * 3600, "market context", rules.max_future_skew_seconds)
            self.store.save_raw_artifact(context_artifact)
            self._persist_market_asset_artifacts(context_payload)
            raw_series = context_payload.get("source") or context_payload.get("candles") or context_payload.get("series")
            derived_context = deterministic_market_context_from_payload(raw_series) if raw_series is not None else {}
            # Provider/LLM complete flags are explicitly non-authoritative.
            # Merge only deterministic normalization fields; the Python gate
            # computes final completeness from asset receipts and freshness.
            if derived_context:
                _merge_deterministic_market_context(context_payload, derived_context)
            context_payload["complete"] = False
            context = self.prompts.context_manifest({"run_mode": mode.value, "effective_rule_pack": rules.rule_set_hash, "market_context_artifact": context_artifact.payload_hash}, ["run_mode", "effective_rule_pack", "market_context_artifact"])
            run = self.store.create_run(mode, rules, context["manifest_hash"], self.store.current_evidence_epoch())
            market_result = self._work_stage(run, "MARKET_ANALYSIS", "workflow.market_analyst", {"raw_input": context_payload, "default_payload": self._valid_payload("MarketContextExecutionAssessmentV2", {"run_mode": mode.value})}, None, [], {"market_snapshot": self._typed_context("MARKET_DATA", "MarketContext", context_payload), "market_breadth": self._typed_context("MARKET_DATA", "Breadth", context_payload.get("breadth")), "sector_relative_strength": self._typed_context("MARKET_DATA", "SectorRelativeStrength", context_payload.get("sector_relative_strength", {}))})
            evaluation_time: datetime | None = None
            if isinstance(market_provider, RecordedMarketDataProvider):
                # Recorded acceptance is replayed against its captured clock,
                # never promoted to live evidence.  This keeps deterministic
                # fixtures from expiring with wall time while the default live
                # path still uses the current Python clock.
                recorded_times: list[datetime] = []
                for receipt in (context_payload.get("assets") or {}).values():
                    if not isinstance(receipt, dict) or not receipt.get("observed_at"):
                        continue
                    try:
                        value = datetime.fromisoformat(str(receipt["observed_at"]).replace("Z", "+00:00"))
                        if value.tzinfo is None:
                            value = value.replace(tzinfo=timezone.utc)
                        recorded_times.append(value.astimezone(timezone.utc))
                    except (TypeError, ValueError):
                        pass
                if recorded_times:
                    evaluation_time = max(recorded_times)
            market_gate = self.market_context_gate.evaluate(context_payload, rules, evaluation_time=evaluation_time)
            context_complete = market_gate.decision == GateDecision.PASS
            self.store.record_funnel(run.run_id, "MARKET_CONTEXT_GATE", 1 if context_complete else 0, {"receipt_hash": market_gate.receipt_hash, "core_input_complete": market_gate.core_input_complete})
            if market_gate.decision != GateDecision.PASS:
                self.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE"); return RunOutcome(run.run_id, mode, "NO_QUALIFIED_CANDIDATE", blocked_reason="MARKET_CONTEXT_GATE")
            universe_query = dict(data.get("universe_query") or {})
            # A strict live run must discover a broad U.S. universe unless the
            # caller explicitly supplies a bounded fixture/replay symbol set.
            # Existing rule thresholds are passed as provider-side hints only;
            # deterministic_universe_prefilter remains the authority.
            if not isinstance(market_provider, (RecordedMarketDataProvider,)) and not (universe_query.get("symbols") or universe_query.get("tickers")):
                universe_query.setdefault("broad", True)
                universe_query.setdefault("markets", ["NASDAQ", "NYSE", "AMEX"])
                universe_query.setdefault("min_price", rules.universe_min_price)
                universe_query.setdefault("min_market_cap", rules.universe_min_market_cap)
                universe_query.setdefault("min_average_dollar_volume", rules.universe_min_average_dollar_volume)
                universe_query.setdefault("technical_count", int((data.get("technical_query") or {}).get("count", 100)))
            universe_artifact = market_provider.fetch_universe(universe_query)
            self._require_provider_artifact_fresh(universe_artifact, market_provider, rules.max_age_universe_hours * 3600, "universe", rules.max_future_skew_seconds)
            self.store.save_raw_artifact(universe_artifact)
            raw_rows = universe_artifact.payload.get("candidates") or universe_artifact.payload.get("securities") or []
            if not isinstance(raw_rows, list):
                raw_rows = []
            valid_rows = [row for row in raw_rows if isinstance(row, dict)]
            prefilter = deterministic_universe_prefilter(
                valid_rows,
                min_price=rules.universe_min_price,
                min_market_cap=rules.universe_min_market_cap,
                min_average_dollar_volume=rules.universe_min_average_dollar_volume,
            )
            prefilter_artifact = RawArtifact(
                f"artifact-universe-filter-{canonical_hash(prefilter)}",
                "python-discovery",
                "UNIVERSE_FILTER_RESULT",
                None,
                universe_artifact.observed_at,
                prefilter,
                canonical_hash(prefilter),
                universe_artifact.source_observed_at or universe_artifact.observed_at,
                utc_now(),
            )
            self.store.save_raw_artifact(prefilter_artifact)
            eligible_ids = set(prefilter.get("eligible_security_ids") or [])
            rows = [row for row in valid_rows if str(row.get("security_id") or row.get("ticker") or "") in eligible_ids]
            counts = dict(prefilter.get("counts") or {})
            self.store.record_funnel(run.run_id, "RAW_UNIVERSE", int(counts.get("RAW_UNIVERSE", len(valid_rows))), {"provider": universe_artifact.provider, "artifact_id": universe_artifact.artifact_id})
            self.store.record_funnel(run.run_id, "SUPPORTED_SECURITY", int(counts.get("SUPPORTED_SECURITY", 0)), {"filter_artifact_id": prefilter_artifact.artifact_id, "filter_version": prefilter.get("version")})
            self.store.record_funnel(run.run_id, "PRICE_FILTER", int(counts.get("PRICE_FILTER", 0)), {"threshold": rules.universe_min_price, "filter_artifact_id": prefilter_artifact.artifact_id})
            self.store.record_funnel(run.run_id, "MARKET_CAP_FILTER", int(counts.get("MARKET_CAP_FILTER", 0)), {"threshold": rules.universe_min_market_cap, "filter_artifact_id": prefilter_artifact.artifact_id})
            self.store.record_funnel(run.run_id, "ADV_FILTER", int(counts.get("ADV_FILTER", 0)), {"threshold": rules.universe_min_average_dollar_volume, "filter_artifact_id": prefilter_artifact.artifact_id})
            # The broad provider records quote/candle coverage separately from
            # the deterministic ADV pass count.  This makes an UNKNOWN
            # liquidity observation visible instead of silently looking like a
            # legitimate low-liquidity rejection.
            universe_payload = universe_artifact.payload if isinstance(universe_artifact.payload, dict) else {}
            if "probe_count" in universe_payload or "quote_scan_count" in universe_payload:
                probe_count = int(universe_payload.get("probe_count") or 0)
                not_evaluated_count = int(universe_payload.get("probe_not_evaluated_count") or 0)
            else:
                probe_count = int(counts.get("ADV_FILTER", 0))
                not_evaluated_count = 0
            liquidity_details = {
                "provider": universe_artifact.provider,
                "strategy": universe_payload.get("probe_strategy"),
                "rotation_key": universe_payload.get("liquidity_rotation_key"),
                "rotation_offset": universe_payload.get("liquidity_rotation_offset"),
                "priority_probe_count": universe_payload.get("liquidity_priority_probe_count"),
                "rotation_probe_count": universe_payload.get("liquidity_rotation_probe_count"),
            }
            self.store.record_funnel(run.run_id, "ADV_PROBED", probe_count, liquidity_details)
            self.store.record_funnel(run.run_id, "ADV_NOT_EVALUATED", not_evaluated_count, {**liquidity_details, "security_ids": universe_payload.get("probe_not_evaluated_ids") or []})
            self.store.record_funnel(run.run_id, "SECTOR_COUNT", len({str(row.get("sector")) for row in rows if row.get("sector")}))
            self.store.record_funnel(run.run_id, "INDUSTRY_COUNT", len({str(row.get("industry")) for row in rows if row.get("industry")}))
            if not rows:
                self.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE")
                return RunOutcome(run.run_id, mode, "NO_QUALIFIED_CANDIDATE", blocked_reason="UNIVERSE_FILTER")
            filtered_payload = {"securities": rows, "source_universe_artifact_id": universe_artifact.artifact_id, "filter_artifact_id": prefilter_artifact.artifact_id}
            filtered_universe_artifact = RawArtifact(
                f"artifact-filtered-universe-{canonical_hash(filtered_payload)}",
                "python-discovery",
                "FILTERED_UNIVERSE",
                None,
                universe_artifact.observed_at,
                filtered_payload,
                canonical_hash(filtered_payload),
                universe_artifact.source_observed_at or universe_artifact.observed_at,
                utc_now(),
            )
            self.store.save_raw_artifact(filtered_universe_artifact)
            identities = self.security_normalizer.normalize(filtered_universe_artifact)
            identity_map = {identity.security_id: identity for identity in identities}
            # Keep the complete rows for Python authority, but send only a
            # bounded summary to the non-authoritative reasoning provider.
            # Broad live universes may contain hundreds of rows with full
            # candle/volume histories; those histories are not required for
            # sector/discovery narration and can overflow provider limits.
            model_universe_rows = _compact_model_universe_rows(rows)
            sector_input = {"market_context": context_payload, "industry_driver_snapshot": data.get("industry_driver_snapshot") or {}}
            sector = self._work_stage(run, "SECTOR_ANALYSIS", "workflow.sector_analyst", {"raw_input": sector_input, "default_payload": self._valid_payload("SectorOpportunityAssessmentV2")}, None, [], {"market_context_result": self._typed_context("MARKET_ANALYSIS", "MarketAnalysisResult", market_result), "sector_data_packet": self._typed_context("MARKET_DATA", "SectorData", {"rows": model_universe_rows}), "industry_driver_snapshot": self._typed_context("INDUSTRY_DATA", "IndustryDriverSnapshot", sector_input["industry_driver_snapshot"]), "market_context_gate_receipt": self._typed_context("MARKET_CONTEXT_GATE", "GateReceipt", market_gate.as_dict())})
            # Sector eligibility is a raw observation interpreted by Python;
            # the LLM narrative above cannot grant the gate.
            sector_eligible = bool(rows) and all(bool(row.get("sector") or row.get("industry") or row.get("market") or row.get("sector_eligible")) for row in rows if isinstance(row, dict))
            sector_gate = self.sector_gate.evaluate({"eligible": sector_eligible}, rules)
            self.store.record_funnel(run.run_id, "SECTOR_GATE", 1 if sector_gate.decision == GateDecision.PASS else 0, {"receipt_hash": sector_gate.receipt_hash})
            if not rows or sector_gate.decision != GateDecision.PASS:
                self.store.finish_run(run.run_id, "NO_QUALIFIED_CANDIDATE"); return RunOutcome(run.run_id, mode, "NO_QUALIFIED_CANDIDATE", blocked_reason="SECTOR_GATE")
            technical_by_sid: dict[str, Any] = {}
            discovery_default = []
            for row in rows:
                if not isinstance(row, dict) or not row.get("security_id"):
                    continue
                if not row.get("prices") and not row.get("candles"):
                    candle_fetcher = getattr(market_provider, "fetch_candles", None)
                    if callable(candle_fetcher):
                        candle_artifact = candle_fetcher(str(row.get("ticker") or row["security_id"]), "1d", int((data.get("technical_query") or {}).get("count", 100)))
                        self._require_provider_artifact_fresh(candle_artifact, market_provider, rules.max_age_universe_hours * 3600, "technical candles", rules.max_future_skew_seconds)
                        self.store.save_raw_artifact(candle_artifact)
                        raw_candles = candle_artifact.payload.get("result") or candle_artifact.payload.get("candles") or []
                        if isinstance(raw_candles, list):
                            row["candles"] = raw_candles
                prices = row.get("prices") or [c.get("close") for c in (row.get("candles") or []) if isinstance(c, dict) and c.get("close") is not None]
                if not isinstance(prices, list) or len(prices) < 2:
                    continue
                try:
                    features = self.technical_calculator.calculate(str(row["security_id"]), [float(p) for p in prices], row.get("volumes"), universe_artifact.source_observed_at, (universe_artifact.artifact_id,))
                except (TypeError, ValueError):
                    continue
                stage_name, deterministic_ok = deterministic_stage_from_features(features)
                technical_by_sid[str(row["security_id"])] = features
                discovery_default.append({"security_id": row["security_id"], "recommended_discovery_action": "DEEP_DIVE_NOW" if deterministic_ok else "EXCLUDE", "proposed_stage": stage_name, "rationale": "deterministic technical feature classifier", "evidence_ids": row.get("evidence_ids", [])})
            discovery_input = {"universe": model_universe_rows, "technical_features": {sid: f.features for sid, f in technical_by_sid.items()}}
            self.store.record_funnel(run.run_id, "TECHNICAL_FEATURES", len(technical_by_sid), {"calculator_version": self.technical_calculator.version})
            self.store.record_funnel(run.run_id, "STAGE_ELIGIBLE", len(discovery_default), {"classifier": "deterministic_stage_from_features"})
            self.store.record_funnel(run.run_id, "STAGE_DISCOVERY_READY", sum(1 for item in discovery_default if item.get("recommended_discovery_action") in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}), {"source": "deterministic technical stage only", "catalyst_status": "NOT_EVALUATED"})
            discovery = self._work_stage(run, "STOCK_DISCOVERY", "workflow.stock_scout", {"raw_input": discovery_input, "default_payload": self._valid_payload("DiscoveryCandidateSetV2", {"run_mode": mode.value, "candidates": discovery_default})}, None, [], {"approved_sector_context": self._typed_context("SECTOR_ANALYSIS", "SectorAnalysisResult", sector), "sector_gate_receipt": self._typed_context("SECTOR_GATE", "GateReceipt", sector_gate.as_dict()), "industry_driver_snapshot": self._typed_context("INDUSTRY_DATA", "IndustryDriverSnapshot", sector_input["industry_driver_snapshot"]), "candidate_universe_packet": self._typed_context("MARKET_DATA", "RawUniverse", model_universe_rows), "deterministic_filter_results": self._typed_context("PYTHON_DISCOVERY_FILTER", "FilterResult", discovery_default), "technical_feature_snapshot": self._typed_context("PYTHON_TECHNICAL_FEATURES", "TechnicalFeatures", {sid: f.features for sid, f in technical_by_sid.items()})})
            qualified: list[dict[str, Any]] = []
            stage_pass_count = capital_pass_count = capital_fail_count = capital_unknown_count = 0
            catalyst_pass_count = catalyst_unknown_count = catalyst_not_evaluated_count = 0
            expectation_gap_count = expectation_gap_reject_count = expectation_gap_unknown_count = 0
            deep_research_count = full_sec_count = audit_count = 0
            # A live broad universe can legitimately contain issuers for which
            # the configured non-SEC source is unavailable or stale.  That is
            # a candidate-level data/provider failure, not a reason to abort
            # the entire HUNT transaction.  Keep the failure explicit in the
            # funnel and continue evaluating the remaining candidates.
            research_provider_failures: list[dict[str, Any]] = []
            sec_provider_failures: list[dict[str, Any]] = []
            sec_stale_data: list[dict[str, Any]] = []
            for found in discovery.get("candidates", []):
                sid = found.get("security_id"); raw = next((row for row in rows if isinstance(row, dict) and row.get("security_id") == sid), None); identity = identity_map.get(sid)
                if not sid or raw is None or identity is None or sid not in technical_by_sid or found.get("recommended_discovery_action") not in {"DEEP_DIVE_NOW", "DEEP_DIVE_SECONDARY"}:
                    continue
                evidence_ids = list(raw.get("evidence_ids") or [])
                if evidence_ids:
                    existing_ids = {
                        str(item["evidence_id"])
                        for item in self.store.connection.execute(
                            "SELECT evidence_id FROM evidence WHERE evidence_id IN (%s)" % ",".join("?" for _ in evidence_ids),
                            tuple(evidence_ids),
                        ).fetchall()
                    }
                    missing_ids = [str(item) for item in evidence_ids if str(item) not in existing_ids]
                    if missing_ids and not isinstance(market_provider, RecordedMarketDataProvider):
                        # A live provider-supplied orphan receipt is a
                        # provenance defect, never something to silently bind
                        # to an unrelated artifact.
                        raise ContractViolation(f"live candidate evidence is not persisted: {missing_ids}")
                    if missing_ids:
                        # Legacy recorded fixtures use stable placeholder IDs
                        # (for example E1).  Bind those IDs to the recorded
                        # universe artifact so report projections remain
                        # receipt-backed without creating a live shortcut.
                        for missing_id in missing_ids:
                            self.store.upsert_evidence(Evidence(
                                missing_id, sid, "recorded-fixture-seed",
                                universe_artifact.source_observed_at or universe_artifact.observed_at,
                                0, universe_artifact.payload_hash, "RAW",
                                raw_artifact_id=universe_artifact.artifact_id,
                            ))
                if not evidence_ids:
                    # A live Toss universe row has no issuer-research Evidence
                    # yet.  Bind the candidate to the already-persisted live
                    # universe RawArtifact instead of manufacturing a
                    # RECORDED_PROVIDER seed receipt.  Recorded fixtures keep
                    # their legacy seeding path in the non-strict runtime.
                    evidence_ids = [f"E-UNIVERSE:{universe_artifact.artifact_id}:{sid}"]
                    self.store.upsert_evidence(Evidence(
                        evidence_ids[0], sid, universe_artifact.provider,
                        universe_artifact.source_observed_at or universe_artifact.observed_at,
                        0, universe_artifact.payload_hash, "RAW",
                        raw_artifact_id=universe_artifact.artifact_id,
                    ))
                feature_stage, feature_eligible = deterministic_stage_from_features(technical_by_sid[sid])
                feature_payload = {"security_id": sid, "features": technical_by_sid[sid].features, "calculator_version": technical_by_sid[sid].calculator_version}
                feature_payload_hash = canonical_hash(feature_payload)
                feature_artifact = RawArtifact(f"artifact-{feature_payload_hash}", "python-technical", "TECHNICAL_FEATURES", sid, technical_by_sid[sid].as_of, feature_payload, feature_payload_hash, universe_artifact.source_observed_at or universe_artifact.observed_at)
                self.store.save_raw_artifact(feature_artifact)
                feature_evidence_id = f"E-TECHNICAL_FEATURES:{feature_payload_hash}"
                self.store.upsert_evidence(Evidence(feature_evidence_id, sid, "python-technical", feature_artifact.source_observed_at or feature_artifact.observed_at, 0, feature_artifact.payload_hash, "DERIVED", raw_artifact_id=feature_artifact.artifact_id))
                evidence_ids.append(feature_evidence_id)
                stage = self.stage_gate.evaluate(feature_stage, feature_eligible, rules)
                self.store.record_stage_result(run.run_id, None, "STAGE_GATE", sid, stage.as_dict(), evidence_ids, self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash), self.store.current_evidence_epoch_for(evidence_ids))
                if stage.decision != GateDecision.PASS:
                    continue
                stage_pass_count += 1
                sec_provider = self.config.sec_provider
                if sec_provider is None:
                    continue
                try:
                    cik = identity.cik if identity.cik and str(identity.cik).strip("0") else None
                    resolver = getattr(sec_provider, "resolve_cik", None)
                    if not cik and callable(resolver):
                        cik = resolver(identity.ticker)
                    identity_payload = {"security_id": sid, "cik": cik}
                    cheap_submissions = sec_provider.fetch_submissions(identity_payload)
                    cheap_facts_raw = sec_provider.fetch_facts(identity_payload)
                    cheap_builder = getattr(sec_provider, "fetch_cheap_facts", None)
                    cheap_artifact = cheap_builder(identity_payload, cheap_submissions, cheap_facts_raw) if callable(cheap_builder) else RawArtifact(f"cheap-{sid}", getattr(sec_provider, "provider_name", "sec"), "SEC_CHEAP_FACTS", sid, utc_now(), {"extraction_status": "INCOMPLETE", "unknowns": ["provider_missing_cheap_facts"]}, canonical_hash({"sid": sid, "cheap": "missing"}), utc_now())
                    cheap_artifacts = [cheap_submissions, cheap_facts_raw, cheap_artifact]
                    for artifact in cheap_artifacts:
                        self._require_provider_artifact_fresh(artifact, sec_provider, rules.max_age_sec_hours * 3600, "SEC cheap prescreen", rules.max_future_skew_seconds)
                        self.store.save_raw_artifact(artifact)
                        cheap_evidence_id = f"E-{artifact.artifact_type}:{artifact.payload_hash}"
                        self.store.upsert_evidence(Evidence(
                            cheap_evidence_id,
                            sid,
                            artifact.provider,
                            artifact.source_observed_at or artifact.observed_at,
                            0,
                            artifact.payload_hash,
                            "RAW",
                            raw_artifact_id=artifact.artifact_id,
                        ))
                        evidence_ids.append(cheap_evidence_id)
                except (ProviderError, ContractViolation, RuntimeError) as exc:
                    reason = str(exc)[:240]
                    # A stale/absent SEC observation is an evidence gap, not
                    # a negative investment fact.  Keep it distinct from
                    # transport/parser failures, but classify both paths as
                    # NOT_EVALUATED so Shadow attribution never counts a data
                    # problem as an investment rejection.
                    stale_sec = "stale SEC cheap prescreen input exceeds max-age" in reason
                    failure = {"security_id": sid, "error": reason}
                    stage_name = "SEC_STALE_DATA" if stale_sec else "SEC_PROVIDER_FAILURE"
                    if stale_sec:
                        sec_stale_data.append(failure)
                    else:
                        sec_provider_failures.append(failure)
                    self.store.record_stage_result(
                        run.run_id, None, stage_name, sid,
                        {"status": "NOT_EVALUATED" if stale_sec else "FAILED", "decision": "INSUFFICIENT_EVIDENCE" if stale_sec else "FAILED", "reason": reason, "security_id": sid},
                        evidence_ids,
                        self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash),
                        self.store.current_evidence_epoch_for(evidence_ids),
                    )
                    continue
                raw_capital = cheap_artifact.payload
                if not isinstance(raw_capital, dict) or raw_capital.get("extraction_status") not in {"COMPLETE", "PARTIAL"} or any(key not in raw_capital for key in CapitalPrescreenGate.CANONICAL_FIELDS):
                    continue
                prescreen_default = {"extraction_status": raw_capital.get("extraction_status"), "identity_status": "CONFIRMED", "evidence_ids": evidence_ids, "unknowns": list(raw_capital.get("unknowns") or [])}
                prescreen_default.update({key: raw_capital[key] for key in CapitalPrescreenGate.CANONICAL_FIELDS})
                prescreen = self._work_stage(run, "CAPITAL_PRESCREEN", "utility.capital_structure_prescreen", {"raw_input": raw_capital, "default_payload": self._valid_payload("CapitalStructurePrescreenResultV2", prescreen_default)}, sid, evidence_ids, {"security_identity": self._typed_context("SECURITY_NORMALIZATION", "SecurityIdentity", identity.__dict__), "cheap_sec_packet": self._typed_context("SEC_CHEAP_PRESCREEN", "CheapSECResult", raw_capital), "stage_gate_receipt": self._typed_context("STAGE_GATE", "GateReceipt", stage.as_dict())})
                # An incomplete cheap SEC packet is explicitly escalated to
                # full forensic review.  This preserves UNKNOWN semantics and
                # prevents normal issuers from disappearing before research;
                # the Gate still rejects any explicit hard exclusion.
                capital_gate = self.prescreen_gate.evaluate({**prescreen, "complete": prescreen.get("extraction_status") == "COMPLETE", "allow_full_forensic_escalation": not isinstance(sec_provider, RecordedSECProvider)}, rules)
                self.store.record_stage_result(run.run_id, None, "CAPITAL_PRESCREEN_GATE", sid, capital_gate.as_dict(), evidence_ids, self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash), self.store.current_evidence_epoch_for(evidence_ids))
                if capital_gate.decision not in {GateDecision.PASS, GateDecision.PASS_WITH_CONSTRAINTS}:
                    capital_fail_count += int(capital_gate.decision == GateDecision.REJECT)
                    capital_unknown_count += int(capital_gate.decision == GateDecision.INSUFFICIENT_EVIDENCE)
                    continue
                capital_pass_count += 1
                failures = raw.get("failure_paths") or self._default_failure_paths(evidence_ids)
                research_provider = self.config.research_provider
                if research_provider is None:
                    continue
                try:
                    research_artifact = research_provider.fetch(sid, data.get("research_query") or {})
                    self._require_provider_artifact_fresh(research_artifact, research_provider, rules.max_age_research_hours * 3600, "research", rules.max_future_skew_seconds)
                except (ProviderError, ContractViolation, RuntimeError) as exc:
                    # Do not fabricate a research artifact or silently convert
                    # the provider failure into a negative investment fact.
                    # The candidate remains non-qualified and the run records
                    # the exact sanitized failure for operations review.
                    reason = str(exc)[:240]
                    research_provider_failures.append({"security_id": sid, "error": reason})
                    self.store.record_stage_result(
                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,
                        {"status": "FAILED", "reason": reason, "security_id": sid},
                        evidence_ids,
                        self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash),
                        self.store.current_evidence_epoch_for(evidence_ids),
                    )
                    continue
                research_payload = research_artifact.payload
                # Providers may expose either the legacy recorded envelope
                # (``source``) or the canonical normalized Evidence contract
                # directly (source_url/source_observed_at/content).  Both
                # paths must carry real source provenance; a provider status
                # string or an LLM summary alone is never sufficient.
                nested_source = research_payload.get("source") if isinstance(research_payload, dict) else None
                direct_source = (
                    isinstance(research_payload, dict)
                    and research_payload.get("source_url")
                    and research_payload.get("source_observed_at")
                    and research_payload.get("provider") == research_artifact.provider
                    and research_payload.get("content") not in (None, "", [], {})
                )
                nested_valid = isinstance(nested_source, dict) and (
                    nested_source.get("source_url") or nested_source.get("url")
                ) and nested_source.get("content") not in (None, "", [], {})
                if not direct_source and not nested_valid:
                    research_provider_failures.append({"security_id": sid, "error": "normalized research source contract incomplete"})
                    self.store.record_stage_result(
                        run.run_id, None, "RESEARCH_PROVIDER_FAILURE", sid,
                        {"status": "FAILED", "reason": "normalized research source contract incomplete", "security_id": sid},
                        evidence_ids,
                        self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash),
                        self.store.current_evidence_epoch_for(evidence_ids),
                    )
                    continue
                self.store.save_raw_artifact(research_artifact)
                research_evidence_id = f"E-{research_artifact.artifact_type}:{research_artifact.payload_hash}"
                research_source_class = str(research_payload.get("source_class") or research_artifact.provider) if isinstance(research_payload, dict) else research_artifact.provider
                self.store.upsert_evidence(Evidence(research_evidence_id, sid, research_source_class, research_artifact.source_observed_at or research_artifact.observed_at, 0, research_artifact.payload_hash, "RAW", raw_artifact_id=research_artifact.artifact_id))
                evidence_ids.append(research_evidence_id)
                catalyst_packet = extract_catalyst_packet(
                    research_artifact.payload,
                    artifact_id=research_artifact.artifact_id,
                    evidence_id=research_evidence_id,
                    fallback_source_observed_at=research_artifact.source_observed_at or research_artifact.observed_at,
                )
                catalyst_gate = self.catalyst_gate.evaluate(
                    catalyst_packet,
                    rules,
                    now=self._provider_evaluation_time(research_artifact, research_provider),
                )
                # A raw research artifact without a structured catalyst is not
                # evidence that no catalyst exists.  Preserve the fail-closed
                # Gate decision while recording an explicit not-evaluated state
                # for reporting and later structured extraction improvements.
                catalyst_missing = not bool(catalyst_packet.get("catalysts"))
                catalyst_result = catalyst_gate.as_dict()
                if catalyst_missing:
                    catalyst_result["evaluation_status"] = "NOT_EVALUATED_CATALYST_EVIDENCE"
                    catalyst_result["reason_code"] = "NOT_EVALUATED_CATALYST_EVIDENCE"
                catalyst_dep_hash = self.store.dependency_hash(evidence_ids, rules.rule_set_hash, run.context_manifest_hash)
                self.store.record_stage_result(
                    run.run_id, None, "CATALYST_GATE", sid, catalyst_result, evidence_ids,
                    catalyst_dep_hash, self.store.current_evidence_epoch_for(evidence_ids),
                )
                if catalyst_gate.decision != GateDecision.PASS:
                    catalyst_unknown_count += 1
                    catalyst_not_evaluated_count += int(catalyst_missing)
                    continue
                catalyst_pass_count += 1
                if self.config.strict_inputs:
                    capability_candidate = {**raw, "security_id": sid, "evidence_ids": evidence_ids, "capital_prescreen_result": prescreen, "research_evidence": research_artifact.payload, "company_facts": cheap_facts_raw.payload}
                    capability_results = {}
                    for capability_stage in ("CAP_FUNDAMENTAL_CHANGE", "CAP_CATALYST_EXPECTATION_RESEARCH", "CAP_DIRECTIONAL_PROBABILITY"):
                        capability_results[capability_stage] = self._run_capability(run, capability_stage, capability_candidate, data, capability_results)
                research = self._work_stage(run, "DEEP_RESEARCH", "workflow.stock_researcher", {"raw_input": {"company": raw, "research_artifact": research_artifact.payload}, "default_payload": self._valid_payload("StockResearchResultV2", {"research_status": "COMPLETE", "failure_paths": self._schema_failure_paths(failures, evidence_ids), "evidence_ids": evidence_ids})}, sid, evidence_ids, {"candidate_context": self._typed_context("STOCK_DISCOVERY", "CandidateContext", discovery), "industry_driver_snapshot": self._typed_context("INDUSTRY_DATA", "IndustryDriverSnapshot", data.get("industry_driver_snapshot") or {}), "capital_prescreen_extraction_receipt": self._typed_context("CAPITAL_PRESCREEN", "PrescreenResult", prescreen), "evidence_packet": self._typed_context("EVIDENCE_STORE", "EvidencePacket", evidence_ids), "company_facts": self._typed_context("SEC_CHEAP_PRESCREEN", "CompanyFacts", cheap_facts_raw.payload), "industry_overlay": self._typed_context("INDUSTRY_DATA", "IndustryOverlay", {}), "capital_prescreen_gate_receipt": self._typed_context("CAPITAL_GATE", "GateReceipt", capital_gate.as_dict()), "stage_gate_receipt": self._typed_context("STAGE_GATE", "GateReceipt", stage.as_dict())})
                try: validate_failure_paths(research.get("failure_paths") or [])
                except ContractViolation: continue
                deep_research_count += 1
                sec_artifacts = [cheap_submissions, cheap_facts_raw, sec_provider.fetch_filings(identity_payload)]
                for artifact in sec_artifacts:
                    self._require_provider_artifact_fresh(artifact, sec_provider, rules.max_age_sec_hours * 3600, "SEC", rules.max_future_skew_seconds)
                validate_sec_artifacts(sec_artifacts)
                for artifact in sec_artifacts:
                    self.store.save_raw_artifact(artifact)
                    sec_evidence_id = f"E-{artifact.artifact_type}:{artifact.payload_hash}"
                    self.store.upsert_evidence(Evidence(
                        sec_evidence_id,
                        sid,
                        artifact.provider,
                        artifact.source_observed_at or artifact.observed_at,
                        0,
                        artifact.payload_hash,
                        "RAW",
                        raw_artifact_id=artifact.artifact_id,
                    ))
                    evidence_ids.append(sec_evidence_id)
                sec_input = {"company": raw, "sec_artifacts": [artifact.payload for artifact in sec_artifacts]}
                sec = self._work_stage(run, "FULL_SEC_FORENSIC", "utility.sec_extraction", {"raw_input": sec_input, "default_payload": self._valid_payload("SECExtractionResultV2", {"status": "COMPLETE"})}, sid, evidence_ids, {"sec_document": self._typed_context("SEC_PROVIDER", "SECArtifacts", sec_input["sec_artifacts"]), "sec_targets": self._typed_context("SECURITY_NORMALIZATION", "SECTargets", identity_payload)})
                full_sec_count += 1
                if self.config.strict_inputs:
                    standard_audit = self._run_capability(run, "STANDARD_AUDIT", {**capability_candidate, "research_result": research, "sec_result": sec}, data, {"research_result": research, "issue_ledger": self.store.list_debate_issues(run.run_id, sid)})
                    self._persist_audit_issues(run, sid, standard_audit, evidence_ids)
                audit = self._work_stage(run, "ADVERSARIAL_AUDIT", "workflow.adversarial_reviewer", {"raw_input": {"company": raw, "research": research, "sec": sec}, "default_payload": self._valid_payload("AdversarialReviewResultV2", {"audit_recommendation": "SUPPORTS_CONTINUATION", "failure_paths": self._schema_failure_paths(failures, evidence_ids)})}, sid, evidence_ids, {"research_result": self._typed_context("DEEP_RESEARCH", "ResearchResult", research), "evidence_packet": self._typed_context("EVIDENCE_STORE", "EvidencePacket", evidence_ids), "issue_ledger": self._typed_context("DEBATE_LEDGER", "IssueLedger", [])})
                self._persist_audit_issues(run, sid, audit, evidence_ids)
                audit_count += 1
                if research.get("research_status") != "COMPLETE" or sec.get("status") != "COMPLETE" or audit.get("audit_recommendation") not in {"SUPPORTS_CONTINUATION", "SUPPORTS_WITH_CONDITIONS"}:
                    continue
                research_source = research_artifact.payload.get("source") if isinstance(research_artifact.payload.get("source"), dict) else research_artifact.payload
                candidate_record = {**raw, **found, "security_id": sid, "identity_hash": identity.identity_hash, "evidence_ids": evidence_ids, "failure_paths": research.get("failure_paths") or failures, "research_result": research, "sec_result": sec, "audit_result": audit, "technical_features": technical_by_sid[sid].features, "capital_prescreen_result": prescreen, "catalyst_gate_receipt": catalyst_gate.as_dict(), "economic_scenario": research_source.get("economic_scenario") if isinstance(research_source, dict) else None, "economic_scenario_artifact_id": research_artifact.artifact_id}
                expectation_known, expectation_gate = self._persist_hunt_reverse_valuation(
                    run, candidate_record, evidence_ids, research_artifact, research_evidence_id, universe_artifact, raw
                )
                if expectation_gate.decision != GateDecision.PASS:
                    expectation_gap_reject_count += int(expectation_gate.decision == GateDecision.REJECT)
                    expectation_gap_unknown_count += int(expectation_gate.decision == GateDecision.INSUFFICIENT_EVIDENCE)
                    continue
                expectation_gap_count += int(expectation_known)
                qualified_status, _ = self.store.qualified_candidate_status(run.run_id, sid)
                if not qualified_status:
                    continue
                qualified.append(candidate_record)
            for stage_name, count in (
                ("STAGE_GATE_PASS", stage_pass_count),
                ("CAPITAL_PRESCREEN_PASS", capital_pass_count),
                ("CAPITAL_PRESCREEN_FAIL", capital_fail_count),
                ("CAPITAL_PRESCREEN_UNKNOWN", capital_unknown_count),
                ("CATALYST_PASS", catalyst_pass_count),
                ("CATALYST_UNKNOWN", catalyst_unknown_count),
                ("CATALYST_NOT_EVALUATED", catalyst_not_evaluated_count),
                ("DEEP_RESEARCH", deep_research_count),
                ("FULL_SEC_FORENSIC", full_sec_count),
                ("ADVERSARIAL_AUDIT", audit_count),
                ("STANDARD_AUDIT", sum(1 for row in self.store.list_stage_results(run.run_id) if row.get("stage") == "STANDARD_AUDIT" and row.get("status") == "SUCCEEDED")),
                ("EXPECTATION_GAP_KNOWN", expectation_gap_count),
                ("EXPECTATION_GAP_REJECT", expectation_gap_reject_count),
                ("EXPECTATION_GAP_UNKNOWN", expectation_gap_unknown_count),
                ("QUALIFIED_CANDIDATE_POOL", len(qualified)),
            ):
                self.store.record_funnel(run.run_id, stage_name, count)
            self.store.record_funnel(
                run.run_id,
                "RESEARCH_PROVIDER_FAILURE",
                len(research_provider_failures),
                {"failures": research_provider_failures[:100]},
            )
            self.store.record_funnel(
                run.run_id,
                "SEC_PROVIDER_FAILURE",
                len(sec_provider_failures),
                {"failures": sec_provider_failures[:100]},
            )
            self.store.record_funnel(
                run.run_id,
                "SEC_STALE_DATA",
                len(sec_stale_data),
                {"failures": sec_stale_data[:100]},
            )
            if not qualified:
                outcome = "NO_QUALIFIED_CANDIDATE" if mode == RunMode.HUNT_ONLY else "BLOCKED_BY_EVIDENCE_GAP"
                self.store.finish_run(run.run_id, outcome); return RunOutcome(run.run_id, mode, outcome)
            if mode == RunMode.HUNT_ONLY:
                self.store.finish_run(run.run_id, "QUALIFIED_CANDIDATE_POOL"); return RunOutcome(run.run_id, mode, "QUALIFIED_CANDIDATE_POOL", tuple(c["security_id"] for c in qualified))
            ranked = self._rank_execution_candidates(qualified, data)
            return self._strict_execution_review(run, ranked[0], data)
        except (ContractViolation, PromptContractError, ProviderError, RuntimeError, ValueError, KeyError) as exc:
            outcome, reason = self._terminal_block_for_exception(exc)
            if "run" in locals():
                self.store.finish_run(run.run_id, outcome)
                return RunOutcome(run.run_id, mode, outcome, blocked_reason=reason)
            return RunOutcome("unstarted", mode, outcome, blocked_reason=reason)

    def _materialize_economic_scenario(self, run, candidate: dict[str, Any]) -> dict[str, Any]:
        """Persist a provider-backed economic scenario before execution arithmetic.

        The scenario is copied only from the raw ResearchEvidenceProvider
        artifact already bound to the qualified candidate.  It is persisted as
        a deterministic, non-LLM StageResult with the same run/security,
        evidence, and upstream stage receipts; caller JSON is never used.
        """
        artifact_id = str(candidate.get("economic_scenario_artifact_id") or "")
        if not artifact_id:
            raise ContractViolation("economic scenario provider artifact is missing")
        row = self.store.connection.execute(
            "SELECT * FROM raw_artifacts WHERE artifact_id=? AND subject_id=?",
            (artifact_id, candidate["security_id"]),
        ).fetchone()
        if not row:
            raise ContractViolation("economic scenario provider artifact is not persisted")
        try:
            artifact_payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, ValueError) as exc:
            raise ContractViolation("economic scenario provider artifact is malformed") from exc
        source = artifact_payload.get("source") if isinstance(artifact_payload, dict) and isinstance(artifact_payload.get("source"), dict) else artifact_payload
        scenario = source.get("economic_scenario") if isinstance(source, dict) else None
        if not isinstance(scenario, dict):
            raise ContractViolation("economic scenario provider evidence is missing")
        required = ("bull_value", "base_value", "bear_value", "bull_probability", "base_probability", "bear_probability", "opportunity_cost_score")
        if any(not isinstance(scenario.get(key), (int, float)) or isinstance(scenario.get(key), bool) for key in required):
            raise ContractViolation("economic scenario provider evidence is incomplete")
        lineage = ("DEEP_RESEARCH", "FULL_SEC_FORENSIC", "ADVERSARIAL_AUDIT", "PORTFOLIO_REVIEW")
        declared_lineage = tuple(str(item) for item in (scenario.get("source_stage_lineage") or lineage))
        if set(declared_lineage) != set(lineage):
            raise ContractViolation("economic scenario provider lineage is incomplete")
        evidence_ids = sorted(set(str(item) for item in (scenario.get("evidence_ids") or [])))
        if not evidence_ids or not set(evidence_ids).issubset(set(candidate.get("evidence_ids") or [])):
            raise ContractViolation("economic scenario provider evidence is not bound to candidate")
        source_rows = []
        for stage in lineage:
            source_row = self.store.get_stage_result(run.run_id, stage, candidate["security_id"])
            if not source_row or source_row.get("status") != "SUCCEEDED":
                raise ContractViolation("economic scenario source stage is missing")
            source_rows.append(str(source_row["result_id"]))
        expected_hash = canonical_hash({
            "security_id": candidate["security_id"],
            "evidence_ids": evidence_ids,
            "bull_value": float(scenario["bull_value"]),
            "base_value": float(scenario["base_value"]),
            "bear_value": float(scenario["bear_value"]),
            "bull_probability": float(scenario["bull_probability"]),
            "base_probability": float(scenario["base_probability"]),
            "bear_probability": float(scenario["bear_probability"]),
            "opportunity_cost_score": float(scenario["opportunity_cost_score"]),
            "source_stage_lineage": sorted(set(lineage)),
        })
        if str(scenario.get("scenario_value_hash") or "") != expected_hash:
            raise ContractViolation("economic scenario provider provenance hash mismatch")
        bound = dict(scenario)
        bound.update({"security_id": candidate["security_id"], "evidence_ids": evidence_ids, "source_stage_lineage": list(lineage), "provider_artifact_id": artifact_id})
        dependency_ids = sorted(set(str(item) for item in (candidate.get("evidence_ids") or [])))
        dep_hash = self.store.dependency_hash(dependency_ids, run.rule_set.rule_set_hash, run.context_manifest_hash)
        payload = {"economic_scenario": bound, "source_result_ids": source_rows, "source_stage_lineage": list(lineage), "provider_artifact_id": artifact_id}
        self.store.record_stage_result(
            run.run_id, None, "ECONOMIC_SCENARIO", candidate["security_id"], payload,
            dependency_ids, dep_hash, self.store.current_evidence_epoch_for(dependency_ids),
        )
        return payload

    def _economic_receipt_for_execution(self, run, candidate: dict[str, Any], data: dict[str, Any], current_price: float, portfolio_result: dict[str, Any]) -> dict[str, Any]:
        """Build a Python-arithmetic receipt from a same-run economic StageResult.

        In strict mode caller economic_assessment values are only an
        untrusted selector/compatibility declaration. Numeric scenario values
        must come from a persisted provider-backed economic StageResult belonging
        to this run and security. Python then validates the evidence lineage
        and performs the authoritative arithmetic.
        """
        del portfolio_result
        assessments = data.get("economic_assessments") if isinstance(data.get("economic_assessments"), dict) else {}
        declaration = data.get("economic_assessment") or assessments.get(candidate.get("security_id")) or {}
        if not isinstance(declaration, dict):
            declaration = {}

        required = (
            "bull_value", "base_value", "bear_value",
            "bull_probability", "base_probability", "bear_probability",
            "opportunity_cost_score",
        )
        source_stage_lineage = (
            "DEEP_RESEARCH",
            "FULL_SEC_FORENSIC",
            "ADVERSARIAL_AUDIT",
            "PORTFOLIO_REVIEW",
        )
        source_result_ids: list[str] = []
        for stage in source_stage_lineage:
            row = self.store.get_stage_result(run.run_id, stage, candidate["security_id"])
            if row is None:
                row = self.store.get_stage_result(run.run_id, stage, None)
            if row and row.get("status") == "SUCCEEDED":
                source_result_ids.append(str(row["result_id"]))
        scenario_owner_row = self.store.get_stage_result(run.run_id, "ECONOMIC_SCENARIO", candidate["security_id"])
        if scenario_owner_row and scenario_owner_row.get("status") == "SUCCEEDED":
            source_result_ids.append(str(scenario_owner_row["result_id"]))
        if len(source_result_ids) < len(source_stage_lineage):
            raise ContractViolation("economic scenario is missing authoritative stage-result lineage")

        if self.config.strict_inputs:
            scenario_result_id = str(declaration.get("scenario_stage_result_id") or "")
            scenario_row = None
            if scenario_result_id:
                row = self.store.connection.execute(
                    "SELECT * FROM stage_results WHERE run_id=? AND result_id=? "
                    "AND stage='ECONOMIC_SCENARIO' AND subject_id=? AND status='SUCCEEDED'",
                    (run.run_id, scenario_result_id, candidate["security_id"]),
                ).fetchone()
                scenario_row = dict(row) if row else None
            else:
                scenario_row = self.store.get_stage_result(
                    run.run_id, "ECONOMIC_SCENARIO", candidate["security_id"]
                )
            if not scenario_row or str(scenario_row.get("result_id")) not in set(source_result_ids):
                raise ContractViolation("strict economic scenario requires a same-run scenario stage-result receipt")
            try:
                persisted_payload = json.loads(scenario_row.get("result_json") or "{}")
            except (TypeError, ValueError) as exc:
                raise ContractViolation("economic scenario stage-result is malformed") from exc
            persisted = persisted_payload.get("economic_scenario") if isinstance(persisted_payload, dict) else None
            if not isinstance(persisted, dict):
                raise ContractViolation("same-run scenario stage-result lacks economic_scenario payload")
            if str(persisted.get("security_id") or candidate["security_id"]) != candidate["security_id"]:
                raise ContractViolation("economic scenario security identity mismatch")
            template = dict(persisted)
            template["scenario_stage_result_id"] = str(scenario_row["result_id"])
            template["source_result_ids"] = list(persisted_payload.get("source_result_ids") or source_result_ids)
            template["source_stage_lineage"] = list(persisted_payload.get("source_stage_lineage") or source_stage_lineage)
            template["evidence_ids"] = list(persisted.get("evidence_ids") or candidate["evidence_ids"])
            template["scenario_value_hash"] = persisted.get("scenario_value_hash")
        else:
            template = dict(declaration)
            if any(not isinstance(template.get(key), (int, float)) for key in required):
                raise ContractViolation("economic scenario input is incomplete")

        if any(not isinstance(template.get(key), (int, float)) or isinstance(template.get(key), bool) for key in required):
            raise ContractViolation("economic scenario lacks numeric values")
        declared_sources = template.get("source_result_ids") or source_result_ids
        if isinstance(declared_sources, str):
            declared_sources = [declared_sources]
        if not set(str(item) for item in declared_sources).issubset(set(source_result_ids)):
            raise ContractViolation("economic scenario provenance references a result outside this run")
        declared_lineage = template.get("source_stage_lineage") or list(source_stage_lineage)
        if sorted(set(str(item) for item in declared_lineage)) != sorted(set(source_stage_lineage)):
            raise ContractViolation("economic scenario stage lineage is incomplete")
        declared_evidence = template.get("evidence_ids") or candidate["evidence_ids"]
        if isinstance(declared_evidence, str):
            declared_evidence = [declared_evidence]
        if not set(str(item) for item in declared_evidence).issubset(set(candidate["evidence_ids"])):
            raise ContractViolation("economic scenario provenance references evidence outside this candidate")
        scenario_hash = template.get("scenario_value_hash")
        if self.config.strict_inputs and not scenario_hash:
            raise ContractViolation("strict economic scenario requires scenario_value_hash provenance")
        if scenario_hash:
            expected_hash = canonical_hash({
                "security_id": candidate["security_id"],
                "evidence_ids": sorted(set(str(item) for item in declared_evidence)),
                "bull_value": float(template["bull_value"]),
                "base_value": float(template["base_value"]),
                "bear_value": float(template["bear_value"]),
                "bull_probability": float(template["bull_probability"]),
                "base_probability": float(template["base_probability"]),
                "bear_probability": float(template["bear_probability"]),
                "opportunity_cost_score": float(template["opportunity_cost_score"]),
                "source_stage_lineage": sorted(set(source_stage_lineage)),
            })
            if str(scenario_hash) != expected_hash:
                raise ContractViolation("economic scenario provenance hash mismatch")

        # Only Evidence rows that are active in this subject/run may be bound
        # to the authoritative economic receipt.  Recorded fixtures sometimes
        # carry a legacy placeholder such as ``E1`` in a provider payload;
        # retaining that orphan ID would make FinalAllocation fail despite a
        # complete same-run receipt.  Live providers must already have exact
        # Evidence→RawArtifact rows, so this is an intersection, never a
        # fabricated evidence fallback.
        active_rows = self.store.connection.execute(
            "SELECT evidence_id FROM evidence WHERE subject_id=? AND status='ACTIVE'",
            (candidate["security_id"],),
        ).fetchall()
        active_ids = {str(row["evidence_id"]) for row in active_rows}
        receipt_evidence_ids = [str(item) for item in candidate["evidence_ids"] if str(item) in active_ids]
        if not receipt_evidence_ids:
            raise ContractViolation("economic receipt has no active candidate evidence")
        receipt = make_economic_assessment_receipt(
            security_id=candidate["security_id"],
            current_price=float(current_price),
            bull_value=float(template["bull_value"]),
            base_value=float(template["base_value"]),
            bear_value=float(template["bear_value"]),
            bull_probability=float(template["bull_probability"]),
            base_probability=float(template["base_probability"]),
            bear_probability=float(template["bear_probability"]),
            opportunity_cost_score=float(template["opportunity_cost_score"]),
            evidence_ids=receipt_evidence_ids,
            source_result_ids=source_result_ids,
        )
        receipt["source_stage_lineage"] = list(source_stage_lineage)
        return receipt

    @staticmethod
    def _rank_execution_candidates(candidates: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
        """Rank the qualified pool from Python-owned HUNT valuation receipts.

        Caller-provided economic_assessments are intentionally ignored here.
        They may constrain a later selected-candidate execution scenario, but
        they cannot select which security receives the one Fresh Money review.
        """
        del data
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for candidate in candidates:
            reverse = candidate.get("reverse_valuation")
            if not isinstance(reverse, dict):
                raise ContractViolation(f"execution ranking missing reverse valuation: {candidate.get('security_id')}")
            upside = reverse.get("benchmark_implied_upside_pct")
            if not isinstance(upside, (int, float)) or isinstance(upside, bool):
                raise ContractViolation(f"execution ranking missing Python implied upside: {candidate.get('security_id')}")
            ranked.append((float(upside), str(candidate.get("security_id")), candidate))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [candidate for _, _, candidate in ranked]

    @staticmethod
    def _resolve_risk_budget_pct(run, data: dict[str, Any]) -> tuple[float, str]:
        """Resolve the Python-owned risk policy from EffectiveRuleSet."""
        policy = float(getattr(run.rule_set, "max_per_position_risk_budget_pct", getattr(run.rule_set, "per_position_risk_budget_pct", 1.0)))
        if not math.isfinite(policy) or policy <= 0:
            raise ContractViolation("EffectiveRuleSet risk budget is invalid")
        requested = data.get("risk_budget_pct")
        if requested is None:
            return policy, "EFFECTIVE_RULE_SET"
        try:
            constrained = float(requested)
        except (TypeError, ValueError) as exc:
            raise ContractViolation("risk_budget_pct must be numeric") from exc
        if not math.isfinite(constrained) or constrained <= 0 or constrained > policy:
            raise ContractViolation("caller risk_budget_pct cannot exceed EffectiveRuleSet policy")
        return constrained, "EFFECTIVE_RULE_SET_CAPPED_USER_CONSTRAINT"

    def _build_execution_context(
        self,
        market_artifact: RawArtifact,
        portfolio_artifact: RawArtifact,
        portfolio_snapshot: Any,
        data: dict[str, Any],
        security_id: str,
    ):
        """Merge read-only market facts with Python-owned execution inputs.

        Toss is a quote/holdings source; it cannot manufacture a stop, equity,
        or completeness assertion.  Equity is always taken from the
        normalized same-run portfolio snapshot.  A stop must come from an
        explicit Python execution-input contract (or a complete non-Toss
        provider fixture); otherwise execution remains fail-closed.
        """
        payload = market_artifact.payload if isinstance(market_artifact.payload, dict) else {}
        if market_artifact.subject_id not in (None, "", security_id):
            raise ContractViolation("market execution artifact subject does not match security")
        if getattr(portfolio_snapshot, "snapshot_id", None) != portfolio_artifact.artifact_id:
            raise ContractViolation("portfolio snapshot receipt does not match raw artifact")
        if getattr(portfolio_snapshot, "payload_hash", None) != portfolio_artifact.payload_hash:
            raise ContractViolation("portfolio snapshot hash does not match raw artifact")
        execution_inputs = data.get("authoritative_execution_inputs")
        if not isinstance(execution_inputs, dict):
            # Complete recorded/configured providers may supply a validated
            # execution stop.  An incomplete provider (notably Toss) may not.
            execution_inputs = payload if payload.get("core_input_complete") is True else {}
        stop = execution_inputs.get("execution_stop")
        if stop in (None, ""):
            raise ContractViolation("required execution input missing: execution_stop")
        equity = getattr(portfolio_snapshot, "total_equity", None)
        if equity in (None, ""):
            raise ContractViolation("required execution input missing: account_equity")
        try:
            gap_risk = execution_inputs.get("gap_risk", payload.get("gap_risk", 0.0))
            event_risk_pct = execution_inputs.get("event_risk_pct", payload.get("event_risk_pct", 0.0))
            snapshot = self.market_normalizer.normalize_execution_context(
                market_artifact,
                security_id,
                account_equity=float(equity),
                execution_stop=float(stop),
                gap_risk=float(gap_risk or 0.0),
                event_risk_pct=float(event_risk_pct or 0.0),
                source_artifact_ids=(portfolio_artifact.artifact_id,),
            )
        except (TypeError, ValueError, ProviderError) as exc:
            raise ContractViolation(f"invalid merged execution context: {exc}") from exc
        return snapshot

    def _strict_execution_review(self, run, candidate: dict[str, Any], data: dict[str, Any]) -> RunOutcome:
        provider = self.config.market_data_provider
        if provider is None:
            raise ContractViolation("MarketDataProvider is required for execution review")
        market_artifact = provider.fetch_execution_snapshot(candidate["security_id"], data.get("market_query") or {})
        self._require_provider_artifact_fresh(market_artifact, provider, run.rule_set.max_age_market_execution_minutes * 60, "market execution", run.rule_set.max_future_skew_seconds)
        self.store.save_raw_artifact(market_artifact)
        market_evidence_id = f"E-MARKET_EXECUTION:{market_artifact.payload_hash}"
        self.store.upsert_evidence(Evidence(
            market_evidence_id,
            candidate["security_id"],
            market_artifact.provider,
            market_artifact.source_observed_at or market_artifact.observed_at,
            0,
            market_artifact.payload_hash,
            "RAW",
            raw_artifact_id=market_artifact.artifact_id,
        ))
        candidate["evidence_ids"] = sorted(set(candidate.get("evidence_ids", [])) | {market_evidence_id})
        portfolio_provider = self.config.portfolio_provider
        if portfolio_provider is None:
            raise ContractViolation("PortfolioProvider is required for execution review")
        portfolio_artifact = portfolio_provider.fetch_snapshot(data.get("portfolio_query") or {})
        self._require_provider_artifact_fresh(portfolio_artifact, portfolio_provider, run.rule_set.max_age_portfolio_minutes * 60, "portfolio", run.rule_set.max_future_skew_seconds)
        self.store.save_raw_artifact(portfolio_artifact)
        portfolio_snapshot = self.portfolio_normalizer.normalize(portfolio_artifact)
        market_snapshot = self._build_execution_context(market_artifact, portfolio_artifact, portfolio_snapshot, data, candidate["security_id"])
        market = {"core_input_complete": True, "current_price": market_snapshot.current_price, "execution_stop": market_snapshot.execution_stop, "account_equity": market_snapshot.account_equity, "worst_plausible_gap": market_snapshot.gap_risk or 0.0, "event_risk_pct": market_snapshot.event_risk_pct or 0.0, "currency": market_snapshot.currency, "source_artifact_ids": list(market_snapshot.source_artifact_ids), "context_payload_hash": market_snapshot.payload_hash, "context_source": "PYTHON_MERGED"}
        market_gate = self.market_execution_gate.evaluate(market, run.rule_set)
        if market_gate.decision not in {GateDecision.PASS, GateDecision.PASS_WITH_CONSTRAINTS}:
            if self.store.unresolved_critical(run.run_id, candidate["security_id"]):
                raise ContractViolation("unresolved CRITICAL issue")
            raise ContractViolation("MarketExecutionGate did not pass")
        market_currency = str(getattr(market_snapshot, "currency", "UNKNOWN")).upper()
        portfolio_currency = str(getattr(portfolio_snapshot, "currency", "UNKNOWN")).upper()
        if market_currency != "UNKNOWN" and portfolio_currency != "UNKNOWN" and market_currency != portfolio_currency:
            raise ContractViolation("market and portfolio currencies do not match")
        portfolio_evidence_id = f"E-PORTFOLIO_SNAPSHOT:{portfolio_artifact.payload_hash}"
        self.store.upsert_evidence(Evidence(portfolio_evidence_id, candidate["security_id"], portfolio_artifact.provider, portfolio_artifact.source_observed_at or portfolio_artifact.observed_at, 0, portfolio_artifact.payload_hash, "RAW", raw_artifact_id=portfolio_artifact.artifact_id))
        candidate["evidence_ids"] = sorted(set(candidate.get("evidence_ids", [])) | {portfolio_evidence_id})
        position = next((position for position in portfolio_snapshot.positions if position.subject_id == candidate["security_id"]), None)
        position_receipt = make_position_snapshot_receipt(position, portfolio_snapshot)
        if position_receipt is not None:
            position_dependency_ids = [portfolio_evidence_id]
            self.store.record_stage_result(
                run.run_id, None, "POSITION_SNAPSHOT_RECEIPT", candidate["security_id"], position_receipt,
                position_dependency_ids,
                self.store.dependency_hash(position_dependency_ids, run.rule_set.rule_set_hash, run.context_manifest_hash),
                self.store.current_evidence_epoch_for(position_dependency_ids),
            )
        portfolio_payload = self._valid_payload("PortfolioComparisonResultV2")
        portfolio_schema = self.prompts.registry["schemas"]["PortfolioComparisonResultV2"]["properties"]["alternatives"]["items"]
        alt = self._atom(portfolio_schema, self.prompts.registry["$defs"])
        if position_receipt is not None:
            portfolio_scope = "EXISTING_POSITION"
            portfolio_path = "EXISTING_FULL"
            portfolio_rationale = "existing position under review; final action remains downstream"
        else:
            portfolio_scope = "CANDIDATE"
            portfolio_path = "NEW_STARTER"
            portfolio_rationale = "new candidate under review; final action remains downstream"
        alt.update({"asset_id": candidate["security_id"], "asset_kind": "SECURITY", "action_scope": portfolio_scope, "capital_path": portfolio_path, "position_snapshot_receipt": position_receipt, "prior_add_trigger_id": None, "evidence_ids": candidate["evidence_ids"], "strengthening_evidence_ids": []})
        portfolio_payload.update({"alternatives": [alt], "preferred_recommendation": {"asset_id": candidate["security_id"], "capital_path": portfolio_path, "rationale": portfolio_rationale}})
        portfolio_result = self._work_stage(run, "PORTFOLIO_REVIEW", "workflow.portfolio_reviewer", {"raw_input": {"snapshot": portfolio_artifact.payload, "snapshot_hash": portfolio_snapshot.payload_hash}, "default_payload": portfolio_payload}, candidate["security_id"], candidate["evidence_ids"], {"candidate_results": self._typed_context("QUALIFIED_CANDIDATE_POOL", "CandidateResults", {"research": candidate.get("research_result"), "sec": candidate.get("sec_result"), "audit": candidate.get("audit_result")}), "portfolio_snapshot": self._typed_context("PORTFOLIO_PROVIDER", "PortfolioSnapshot", portfolio_artifact.payload), "cash_state": self._typed_context("PORTFOLIO_PROVIDER", "CashState", {"cash": portfolio_snapshot.cash, "total_equity": portfolio_snapshot.total_equity}), "risk_metrics": self._typed_context("RISK_INPUTS", "RiskMetrics", data.get("risk_inputs") or {}), "market_execution_gate_receipt": self._typed_context("MARKET_EXECUTION_GATE", "GateReceipt", self.market_execution_gate.evaluate(market, run.rule_set).as_dict())})
        self._materialize_economic_scenario(run, candidate)
        economic = self._economic_receipt_for_execution(run, candidate, data, market_snapshot.current_price, portfolio_result)
        self.store.validate_economic_receipt(run.run_id, candidate["security_id"], economic, candidate["evidence_ids"])
        risk_budget_pct, risk_budget_source = self._resolve_risk_budget_pct(run, data)
        preliminary_risk = self.risk_engine.assess(market_snapshot.current_price, market_snapshot.execution_stop, float(economic["structural_asymmetry"]), float(economic["probability_weighted_ev"]), market_snapshot.account_equity or portfolio_snapshot.total_equity, risk_budget_pct, market_snapshot.gap_risk or 0.0, market_snapshot.event_risk_pct or 0.0, None)
        preliminary_risk["risk_budget_source"] = risk_budget_source
        preliminary_risk["risk_target_position_shares"] = int(preliminary_risk["shares"])
        execution_candidate = {**candidate, "research_result": candidate.get("research_result"), "sec_result": candidate.get("sec_result"), "audit_result": candidate.get("audit_result")}
        execution_capabilities = {}
        stage_gate_row = self.store.get_stage_result(run.run_id, "STAGE_GATE", candidate["security_id"])
        try:
            stage_gate_value = __import__("json").loads(stage_gate_row.get("result_json") or "null") if stage_gate_row else None
        except (TypeError, ValueError):
            stage_gate_value = None
        if not isinstance(stage_gate_value, dict):
            raise ContractViolation("execution requires persisted StageGate receipt")
        capability_prior = {"portfolio": portfolio_result, "risk": preliminary_risk, "market": market, "market_gate": self.market_execution_gate.evaluate(market, run.rule_set).as_dict(), "stage_gate": stage_gate_value}
        for capability_stage in ("CAP_PROBABILITY_EDGE", "CAP_CATALYST_EXPECTATION_EXEC", "CAP_CAPITAL_FORENSICS", "CAP_ENTRY_READINESS", "CAP_FAILURE_INVALIDATION"):
            execution_capabilities[capability_stage] = self._run_capability(run, capability_stage, execution_candidate, data, {**capability_prior, **execution_capabilities})
        requested_constraint = data.get("requested_action")
        if requested_constraint is not None and str(requested_constraint) not in {item.value for item in ExecutionAction}:
            raise ContractViolation("caller requested_action is not a canonical ExecutionAction")
        market_execution_receipt = self.market_execution_gate.evaluate(market, run.rule_set).as_dict()
        gate_snapshot = self._strict_gate_snapshot(run, candidate["security_id"], market_execution_receipt)
        synthesis_input = {"candidate": candidate, "requested_action_constraint": requested_constraint, "market": market, "portfolio_comparison": portfolio_result, "risk": preliminary_risk, "economic_assessment": economic, "capability_results": execution_capabilities, "gate_snapshot": gate_snapshot}
        persisted_market_context = self._persisted_stage_value(run, "MARKET_ANALYSIS", None)
        synthesis = self._work_stage(run, "FINAL_SYNTHESIS", "workflow.final_synthesis_agent", {"raw_input": synthesis_input, "default_payload": self._valid_payload("FinalSynthesisRecommendationV2", {"run_mode": run.mode.value, "target_security_id": candidate["security_id"], "recommendation_status": "BLOCKED_BY_EVIDENCE_GAP", "recommended_action": None, "action_scope": "CANDIDATE", "starter_plan": None, "add_plan": None})}, candidate["security_id"], candidate["evidence_ids"], {"action_scope": self._typed_context("PYTHON_AUTHORITY", "ActionScope", "CANDIDATE"), "validated_research_results": self._typed_context("DEEP_RESEARCH", "ResearchResult", candidate.get("research_result")), "adversarial_results": self._typed_context("ADVERSARIAL_AUDIT", "AuditResult", candidate.get("audit_result")), "portfolio_comparison": self._typed_context("PORTFOLIO_REVIEW", "PortfolioComparison", portfolio_result), "deterministic_gate_snapshot": self._typed_context("PYTHON_GATES", "GateSnapshot", gate_snapshot), "market_context": self._typed_context("MARKET_CONTEXT", "MarketContext", persisted_market_context), "market_execution_gate_receipt": self._typed_context("MARKET_EXECUTION_GATE", "GateReceipt", market_execution_receipt), "risk_engine_results": self._typed_context("PYTHON_RISK_ENGINE", "RiskAssessment", preliminary_risk), "context_manifest": self._typed_context("PROMPT_RUNTIME", "ContextManifest", {"run_id": run.run_id, "dependency_ids": candidate["evidence_ids"]})})
        recommendation = synthesis.get("recommended_action")
        try:
            action = ExecutionAction(recommendation) if synthesis.get("recommendation_status") == "READY" and recommendation else ExecutionAction.WATCH
        except ValueError:
            raise ContractViolation("Final Synthesis returned a non-canonical action")
        # Caller intent may constrain an action but can never promote a
        # recommendation or provide the authoritative action itself.
        if requested_constraint is not None and action.value != str(requested_constraint):
            action = ExecutionAction.WATCH
        self._enforce_synthesis(action, synthesis)
        if action in {ExecutionAction.ADD, ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT} and position_receipt is None:
            raise ContractViolation("existing-position action requires same-run PositionSnapshotReceiptV2")
        if action in {ExecutionAction.ADD, ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT}:
            validate_recommendation_identity(action, candidate["security_id"], position_receipt)
        plan = synthesis.get("starter_plan") if action == ExecutionAction.STARTER else None
        add_plan = synthesis.get("add_plan") if action == ExecutionAction.ADD else None
        if action == ExecutionAction.STARTER:
            if not isinstance(plan, dict):
                raise ContractViolation("strict STARTER requires synthesis starter_plan")
            validate_starter_plan(plan, run.rule_set)
        if action == ExecutionAction.ADD:
            strengthening_ids = list((synthesis.get("strengthening_evidence_receipt") or {}).get("strengthening_evidence_ids") or [])
            self.store.require_strengthening_evidence(candidate["security_id"], strengthening_ids)
            validate_add_lineage(candidate["security_id"], add_plan or {}, position_receipt, synthesis.get("prior_add_trigger_receipt") or {}, synthesis.get("fresh_evidence_delta_receipt") or {}, synthesis.get("strengthening_evidence_receipt") or {})
        if action == ExecutionAction.STARTER and isinstance(plan, dict):
            max_position = int(plan["maximum_position"]["shares"])
        elif action == ExecutionAction.ADD and isinstance(add_plan, dict):
            resulting_cap = add_plan.get("resulting_position_cap") or {}
            max_position = int(resulting_cap["shares"]) if isinstance(resulting_cap.get("shares"), (int, float)) else None
        else:
            max_position = None
        risk = self.risk_engine.assess(market_snapshot.current_price, market_snapshot.execution_stop, float(economic["structural_asymmetry"]), float(economic["probability_weighted_ev"]), market_snapshot.account_equity or portfolio_snapshot.total_equity, risk_budget_pct, market_snapshot.gap_risk or 0.0, market_snapshot.event_risk_pct or 0.0, max_position)
        risk["risk_budget_source"] = risk_budget_source
        risk["risk_target_position_shares"] = int(risk["shares"])
        current_position_shares = int(position.shares) if position and position.position_exists else 0
        try:
            allocation_shares = transaction_shares(
                action,
                position_shares=current_position_shares,
                risk_target_shares=int(risk["shares"]),
                price=float(market_snapshot.current_price),
                equity=float(market_snapshot.account_equity or portfolio_snapshot.total_equity),
                add_plan=add_plan,
            )
        except ExecutionQuantityError as exc:
            raise ContractViolation(str(exc)) from exc
        allocation_capital_pct = (
            allocation_shares * market_snapshot.current_price / (market_snapshot.account_equity or portfolio_snapshot.total_equity) * 100
            if action in {ExecutionAction.STARTER, ExecutionAction.ADD, ExecutionAction.FULL} and allocation_shares > 0
            else 0.0
        )
        resulting_position_shares = 0 if action == ExecutionAction.EXIT else (current_position_shares - allocation_shares if action == ExecutionAction.TRIM else current_position_shares + allocation_shares)
        risk["current_position_shares"] = current_position_shares
        risk["transaction_shares"] = allocation_shares
        risk["resulting_position_shares"] = resulting_position_shares
        allocation = {"security_id": candidate["security_id"], "action_scope": "EXISTING_POSITION" if action in {ExecutionAction.ADD, ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT} else "CANDIDATE", "shares": allocation_shares, "capital_pct": allocation_capital_pct, "risk": risk, "position_snapshot_receipt": position_receipt, "current_position_shares": current_position_shares, "risk_target_position_shares": int(risk["risk_target_position_shares"]), "transaction_shares": allocation_shares, "resulting_position_shares": resulting_position_shares}
        if action == ExecutionAction.STARTER:
            allocation["starter_plan"] = plan
            if risk["shares"] <= 0:
                raise ContractViolation("strict RiskEngine produced zero-size STARTER")
        if action == ExecutionAction.ADD:
            allocation["add_plan"] = add_plan
            allocation["strengthening_evidence_ids"] = list((synthesis.get("strengthening_evidence_receipt") or {}).get("strengthening_evidence_ids") or [])
        dep = self.store.dependency_hash(candidate["evidence_ids"], run.rule_set.rule_set_hash, run.context_manifest_hash)
        allocation["economic_assessment"] = economic
        self.store.record_execution_context(run.run_id, candidate["security_id"], {"market": market, "portfolio": portfolio_result, "position_snapshot_receipt": position_receipt, "risk": risk, "economic_assessment": economic, "synthesis": synthesis}, run.context_manifest_hash, dep, self.store.current_evidence_epoch_for(candidate["evidence_ids"]), candidate["evidence_ids"])
        allocation_id = self.final_allocation_gate.commit(run, action, allocation)
        self.store.finish_run(run.run_id, "FINAL_ACTION_COMMITTED")
        return RunOutcome(run.run_id, run.mode, "FINAL_ACTION_COMMITTED", (candidate["security_id"],), authoritative_action=action, allocation={**allocation, "allocation_id": allocation_id})

    def _execution_review(self, run, candidate: dict[str, Any], data: dict[str, Any], ids: tuple[str, ...]) -> RunOutcome:
        target = candidate["security_id"]; market = data.get("market_execution") or {}; market_receipt = self.market_execution_gate.evaluate(market, run.rule_set)
        if market.get("source_observed_at") or market.get("observed_at") or market.get("as_of"):
            require_fresh(market.get("source_observed_at") or market.get("observed_at") or market.get("as_of"), run.rule_set.max_age_market_execution_minutes * 60, "market execution", run.rule_set.max_future_skew_seconds)
        portfolio_input = data.get("portfolio_snapshot") or {}
        if portfolio_input.get("source_observed_at") or portfolio_input.get("observed_at") or portfolio_input.get("as_of"):
            require_fresh(portfolio_input.get("source_observed_at") or portfolio_input.get("observed_at") or portfolio_input.get("as_of"), run.rule_set.max_age_portfolio_minutes * 60, "portfolio", run.rule_set.max_future_skew_seconds)
        if market_receipt.decision not in {GateDecision.PASS, GateDecision.PASS_WITH_CONSTRAINTS}: self.store.finish_run(run.run_id, "BLOCKED_BY_EVIDENCE_GAP"); return RunOutcome(run.run_id, run.mode, "BLOCKED_BY_EVIDENCE_GAP", ids, blocked_reason="MARKET_EXECUTION_GATE")
        if data.get("unresolved_critical"): self.store.record_debate_issue(run.run_id, target, "CRITICAL", "live input unresolved critical"); self.store.finish_run(run.run_id, "BLOCKED_BY_CRITICAL_ISSUE"); return RunOutcome(run.run_id, run.mode, "BLOCKED_BY_CRITICAL_ISSUE", ids, blocked_reason="UNRESOLVED_CRITICAL")
        portfolio_payload = self._valid_payload("PortfolioComparisonResultV2")
        portfolio_schema = self.prompts.registry["schemas"]["PortfolioComparisonResultV2"]["properties"]["alternatives"]["items"]
        alt = self._atom(portfolio_schema, self.prompts.registry["$defs"])
        alt.update({"asset_id": target, "asset_kind": "SECURITY", "action_scope": "CANDIDATE", "capital_path": "NEW_STARTER", "position_snapshot_receipt": None, "prior_add_trigger_id": None, "evidence_ids": candidate["evidence_ids"], "strengthening_evidence_ids": []})
        portfolio_payload.update({"alternatives": [alt], "preferred_recommendation": {"asset_id": target, "capital_path": "NEW_STARTER", "rationale": "recorded portfolio review"}})
        portfolio_result = self._work_stage(run, "PORTFOLIO_REVIEW", "workflow.portfolio_reviewer", {"raw_input": data.get("portfolio_snapshot", {}), "default_payload": portfolio_payload}, target, candidate["evidence_ids"], {"candidate_results": self._typed_context("QUALIFIED_CANDIDATE_POOL", "CandidateResults", candidate), "portfolio_snapshot": self._typed_context("PORTFOLIO_INPUT", "PortfolioSnapshot", data.get("portfolio_snapshot", {})), "cash_state": self._typed_context("PORTFOLIO_INPUT", "CashState", {"account_equity": data.get("account_equity")}), "risk_metrics": self._typed_context("RISK_INPUTS", "RiskMetrics", data.get("risk_inputs") or {}), "market_execution_gate_receipt": self._typed_context("MARKET_EXECUTION_GATE", "GateReceipt", market_receipt.as_dict())})
        risk_budget_pct, risk_budget_source = self._resolve_risk_budget_pct(run, data)
        price_value = market.get("current_price", data.get("current_price"))
        stop_value = market.get("execution_stop", data.get("execution_stop"))
        equity_value = market.get("account_equity", data.get("account_equity"))
        if price_value in (None, "") or stop_value in (None, "") or equity_value in (None, ""):
            raise ContractViolation("required execution input missing: current_price, execution_stop, account_equity")
        price = float(price_value); stop = float(stop_value); equity = float(equity_value)
        risk = self.risk_engine.assess(price, stop, float(market.get("structural_asymmetry", price)), float(market.get("probability_weighted_ev", 0)), equity, risk_budget_pct, float(market.get("worst_plausible_gap", 0)), float(market.get("event_risk_pct", 0)), market.get("maximum_position_shares"))
        risk["risk_budget_source"] = risk_budget_source
        risk["risk_target_position_shares"] = int(risk["shares"])
        action = ExecutionAction(data.get("requested_action", "STARTER")); position = data.get("position_snapshot") or {}
        if action in {ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT}: validate_recommendation_identity(action, target, position)
        if action == ExecutionAction.STARTER:
            plan = data.get("starter_plan");
            if plan is None: raise ContractViolation("STARTER requires StarterPlanV2")
            validate_starter_plan(plan, run.rule_set)
            risk = self.risk_engine.assess(price, stop, float(market.get("structural_asymmetry", price)), float(market.get("probability_weighted_ev", 0)), equity, risk_budget_pct, float(market.get("worst_plausible_gap", 0)), float(market.get("event_risk_pct", 0)), int(plan["starter_shares"]))
            risk["risk_budget_source"] = risk_budget_source
            risk["risk_target_position_shares"] = int(risk["shares"])
            if risk["shares"] <= 0: raise ContractViolation("RiskEngine produced zero-size STARTER")
        if action in {ExecutionAction.NO_TRADE, ExecutionAction.WATCH} and risk["shares"] > 0:
            raise ContractViolation("non-actionable action cannot carry a positive allocation")
        if action == ExecutionAction.ADD: validate_add_lineage(target, data.get("add_plan") or {}, position, data.get("prior_add_trigger_receipt") or {}, data.get("fresh_evidence_delta_receipt") or {}, data.get("strengthening_evidence_receipt") or {})
        if action == ExecutionAction.ADD:
            strengthening_ids = list((data.get("strengthening_evidence_receipt") or {}).get("strengthening_evidence_ids") or [])
            for eid in strengthening_ids:
                observed = utc_now()
                payload = {"evidence_id": eid, "subject_id": target, "source": "diagnostic-recorded-strengthening"}
                payload_hash = canonical_hash(payload)
                strengthening_artifact = RawArtifact(
                    f"artifact-strengthening-{payload_hash}",
                    "diagnostic-recorded",
                    "STRENGTHENING_EVIDENCE",
                    target,
                    observed,
                    payload,
                    payload_hash,
                    observed,
                    utc_now(),
                )
                self.store.save_raw_artifact(strengthening_artifact)
                self.store.upsert_evidence(Evidence(eid, target, "STRENGTHENING_PROVIDER", observed, 0, payload_hash, "RECORDED", raw_artifact_id=strengthening_artifact.artifact_id))
            self.store.require_strengthening_evidence(target, strengthening_ids)
            candidate["evidence_ids"] = sorted(set(candidate.get("evidence_ids", [])) | set(strengthening_ids))
        synthesis = self._work_stage(run, "FINAL_SYNTHESIS", "workflow.final_synthesis_agent", {"raw_input": {"candidate": candidate, "requested_action": action.value, "portfolio_comparison": portfolio_result, "risk": risk}, "default_payload": self._valid_payload("FinalSynthesisRecommendationV2", {"run_mode": run.mode.value, "target_security_id": target, "recommendation_status": "BLOCKED_BY_EVIDENCE_GAP", "recommended_action": None, "action_scope": "CANDIDATE", "starter_plan": None, "add_plan": None})}, target, candidate["evidence_ids"], {"action_scope": self._typed_context("PYTHON_AUTHORITY", "ActionScope", "CANDIDATE"), "validated_research_results": self._typed_context("DEEP_RESEARCH", "ResearchResult", candidate.get("research_result")), "adversarial_results": self._typed_context("ADVERSARIAL_AUDIT", "AuditResult", candidate.get("audit_result")), "portfolio_comparison": self._typed_context("PORTFOLIO_REVIEW", "PortfolioComparison", portfolio_result), "deterministic_gate_snapshot": self._typed_context("PYTHON_GATES", "GateSnapshot", {"market": market_receipt.as_dict(), "stage": "PASS", "capital": "PASS"}), "market_context": self._typed_context("MARKET_CONTEXT", "MarketContext", data.get("market_context") or {}), "market_execution_gate_receipt": self._typed_context("MARKET_EXECUTION_GATE", "GateReceipt", market_receipt.as_dict()), "risk_engine_results": self._typed_context("PYTHON_RISK_ENGINE", "RiskAssessment", risk), "context_manifest": self._typed_context("PROMPT_RUNTIME", "ContextManifest", {"run_id": run.run_id, "dependency_ids": candidate["evidence_ids"]})})
        self._enforce_synthesis(action, synthesis)
        current_position_shares = int(position.get("shares") or 0) if isinstance(position, dict) else 0
        tx_shares = int(risk["shares"]) if action in {ExecutionAction.STARTER, ExecutionAction.ADD, ExecutionAction.FULL} else (current_position_shares if action == ExecutionAction.EXIT else 0)
        resulting_position_shares = 0 if action == ExecutionAction.EXIT else current_position_shares + tx_shares
        risk["current_position_shares"] = current_position_shares
        risk["transaction_shares"] = tx_shares
        risk["resulting_position_shares"] = resulting_position_shares
        allocation = {"security_id": target, "action": action.value, "shares": tx_shares, "capital_pct": round(tx_shares * price / equity * 100, 6) if action in {ExecutionAction.STARTER, ExecutionAction.ADD, ExecutionAction.FULL} else 0.0, "action_scope": "EXISTING_POSITION" if action in {ExecutionAction.ADD, ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT} else "CANDIDATE", "risk": risk, "current_position_shares": current_position_shares, "transaction_shares": tx_shares, "resulting_position_shares": resulting_position_shares}
        if action == ExecutionAction.STARTER:
            allocation["starter_plan"] = data.get("starter_plan")
        if action == ExecutionAction.ADD:
            allocation["add_plan"] = data.get("add_plan")
            allocation["strengthening_evidence_ids"] = list((data.get("strengthening_evidence_receipt") or {}).get("strengthening_evidence_ids") or [])
        dep = self.store.dependency_hash(candidate["evidence_ids"], run.rule_set.rule_set_hash, run.context_manifest_hash); self.store.record_execution_context(run.run_id, target, {"market_gate": market_receipt.as_dict(), "portfolio": data.get("portfolio_snapshot") or {}, "risk": risk, "stage_gate": "PASS", "capital_gate": "PASS", "full_sec": "COMPLETE", "audit": "COMPLETE", "synthesis": synthesis}, run.context_manifest_hash, dep, self.store.current_evidence_epoch_for(candidate["evidence_ids"]), candidate["evidence_ids"])
        rec = ActionRecommendation(target, action, "READY", "EXISTING_POSITION" if action in {ExecutionAction.ADD, ExecutionAction.FULL, ExecutionAction.TRIM, ExecutionAction.EXIT} else "CANDIDATE", data.get("starter_plan") if action == ExecutionAction.STARTER else None, data.get("add_plan") if action == ExecutionAction.ADD else None, position or None, data.get("prior_add_trigger_receipt"), data.get("fresh_evidence_delta_receipt"), data.get("strengthening_evidence_receipt"), False, dep)
        allocation_id = self.final_allocation_gate.commit(run, action, allocation); self.store.finish_run(run.run_id, "FINAL_ACTION_COMMITTED")
        return RunOutcome(run.run_id, run.mode, "FINAL_ACTION_COMMITTED", ids, rec, action, {**allocation, "allocation_id": allocation_id})


class ProductionStockAgent(StockAgent):
    """Strict adapter-backed facade used for provider acceptance runs."""

    def __init__(self, config: StockAgentConfig, store: SQLiteStore | None = None, provider: Any | None = None, router: ModelRouter | None = None) -> None:
        if not config.strict_inputs:
            config.strict_inputs = True
        super().__init__(config=config, store=store, provider=provider, router=router)
