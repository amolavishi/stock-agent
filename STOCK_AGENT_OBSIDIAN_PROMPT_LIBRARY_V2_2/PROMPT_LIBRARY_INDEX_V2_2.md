# Prompt Library Index v2.2

총 Prompt: **41** (기존 40 + 신규 1)

Architecture: **Stock Agent Architecture v1.1**  
Investment Rules: **v2.0**

| prompt_id | file | kind | stage | schema | run modes |
|---|---|---|---|---|---|
| `adversarial.consensus_revalidation` | `ADVERSARIAL/consensus_revalidation.md` | LEAF | AUDIT | `ConsensusRevalidationResult` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `adversarial.evidence_contradiction_audit` | `ADVERSARIAL/evidence_contradiction_audit.md` | LEAF | AUDIT | `AdversarialAuditResult` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `adversarial.standard_audit` | `ADVERSARIAL/standard_audit.md` | LEAF | AUDIT | `AdversarialAuditResult` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `adversarial.strong_thesis_destruction` | `ADVERSARIAL/strong_thesis_destruction.md` | LEAF | AUDIT | `AdversarialAuditResult` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.accounting_quality` | `CAPABILITIES/accounting_quality.md` | LEAF | DEEP_RESEARCH | `AccountingQualityAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.capital_structure_forensics` | `CAPABILITIES/capital_structure_forensics.md` | LEAF | FULL_SEC | `CapitalStructureForensicAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.catalyst_expectation_gap` | `CAPABILITIES/catalyst_expectation_gap.md` | LEAF | DEEP_RESEARCH | `CatalystExpectationGapAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.contract_backlog_quality` | `CAPABILITIES/contract_backlog_quality.md` | LEAF | DEEP_RESEARCH | `ContractBacklogQualityAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.directional_probability_hypothesis` | `CAPABILITIES/directional_probability_hypothesis.md` | LEAF | DEEP_RESEARCH | `DirectionalProbabilityHypothesisV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.entry_readiness_execution_structure` | `CAPABILITIES/entry_readiness_execution_structure.md` | LEAF | EXECUTION_RISK | `EntryReadinessExecutionAssessmentV2` | HUNT_AND_EXECUTION_REVIEW |
| `capability.event_probability_ev` | `CAPABILITIES/event_probability_ev.md` | LEAF | EXECUTION_RISK | `EventProbabilityAssessmentV2` | HUNT_AND_EXECUTION_REVIEW |
| `capability.failure_scenarios_invalidation` | `CAPABILITIES/failure_scenarios_invalidation.md` | LEAF | DEEP_RESEARCH | `FailureScenarioInvalidationAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.fundamental_change_quality` | `CAPABILITIES/fundamental_change_quality.md` | LEAF | DEEP_RESEARCH | `FundamentalChangeQualityAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `capability.probability_edge_risk_asymmetry` | `CAPABILITIES/probability_edge_risk_asymmetry.md` | LEAF | EXECUTION_RISK | `ProbabilityEdgeRiskAsymmetryAssessmentV2` | HUNT_AND_EXECUTION_REVIEW |
| `capability.reverse_valuation` | `CAPABILITIES/reverse_valuation.md` | LEAF | DEEP_RESEARCH | `ReverseValuationAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.ai_data_center_infrastructure` | `INDUSTRY/ai_data_center_infrastructure.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.ai_data_services` | `INDUSTRY/ai_data_services.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.biotech` | `INDUSTRY/biotech.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.crypto_linked_equities` | `INDUSTRY/crypto_linked_equities.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.defense_space` | `INDUSTRY/defense_space.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.e_and_p` | `INDUSTRY/e_and_p.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.nuclear_critical_minerals` | `INDUSTRY/nuclear_critical_minerals.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.optical_networking_broadband` | `INDUSTRY/optical_networking_broadband.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.quantum` | `INDUSTRY/quantum.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.saas_ai_software` | `INDUSTRY/saas_ai_software.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.semiconductor_advanced_packaging` | `INDUSTRY/semiconductor_advanced_packaging.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `industry.shipping` | `INDUSTRY/shipping.md` | LEAF | DEEP_RESEARCH | `IndustryOverlayAssessment` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `system.analysis_grounding` | `SYSTEM/analysis_grounding_contract.md` | MIXIN | MIXIN | `—` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `utility.capital_structure_prescreen` | `UTILITIES/capital_structure_prescreen.md` | LEAF | PRESCREEN | `CapitalStructurePrescreenResultV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `utility.claim_evidence_mapping` | `UTILITIES/claim_evidence_mapping.md` | LEAF | DEEP_RESEARCH | `ClaimEvidenceMap` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `utility.evidence_extraction` | `UTILITIES/evidence_extraction.md` | LEAF | DISCOVERY | `EvidenceItems` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `utility.freshness_delta_review` | `UTILITIES/freshness_delta_review.md` | LEAF | AUDIT | `FreshnessDeltaReviewResultV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `utility.missing_evidence_request` | `UTILITIES/missing_evidence_request.md` | LEAF | AUDIT | `EvidenceRequestSet` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `utility.sec_extraction` | `UTILITIES/sec_extraction.md` | LEAF | FULL_SEC | `SECExtractionResultV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `workflow.adversarial_reviewer` | `WORKFLOW/adversarial_reviewer.md` | LEAF | AUDIT | `AdversarialReviewResultV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `workflow.final_synthesis_agent` | `WORKFLOW/final_synthesis_agent.md` | LEAF | FINAL_SYNTHESIS | `FinalSynthesisRecommendationV2` | HUNT_AND_EXECUTION_REVIEW |
| `workflow.market_analyst` | `WORKFLOW/market_analyst.md` | LEAF | DISCOVERY | `MarketContextExecutionAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `workflow.portfolio_reviewer` | `WORKFLOW/portfolio_reviewer.md` | LEAF | EXECUTION_RISK | `PortfolioComparisonResultV2` | HUNT_AND_EXECUTION_REVIEW |
| `workflow.sector_analyst` | `WORKFLOW/sector_analyst.md` | LEAF | DISCOVERY | `SectorOpportunityAssessmentV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `workflow.stock_researcher` | `WORKFLOW/stock_researcher.md` | LEAF | DEEP_RESEARCH | `StockResearchResultV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |
| `workflow.stock_scout` | `WORKFLOW/stock_scout.md` | LEAF | DISCOVERY | `DiscoveryCandidateSetV2` | HUNT_ONLY, HUNT_AND_EXECUTION_REVIEW |

## Dependency semantics

- `compose_with`: 같은 call에 합성되는 mixin만 허용하며 prompt_id 기준 dedupe한다.
- `requires_results`: 선행 leaf call 결과 prerequisite다.
- `requires_capabilities`: capability leaf 결과 prerequisite다.
- `conditional_dependencies`: 조건부 선행 결과이며 composition과 구분한다.
- 모든 leaf call은 최종 output schema owner가 정확히 하나다.


