"""Canonical production composition for Stock Agent MAIN.

Every production entry point must install this stack exactly once. Discovery
MAIN remains the sole discovery owner; no Python heuristic scanner runtime is
installed. The only code imported from Discovery Recall Lite is its live
breadth/provider adapter.
"""
from __future__ import annotations

from typing import Any

_INSTALLED = False


def install_production_stack() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .alpha_bootstrap import install_alpha_discovery_policy
    from .alpha_coverage_v14 import install_alpha_coverage_v14
    install_alpha_discovery_policy()
    install_alpha_coverage_v14()

    # Provider/breadth only. Never install install_discovery_recall_lite_runtime().
    from .discovery_recall_lite_v15 import install_discovery_recall_lite_provider
    install_discovery_recall_lite_provider()

    from .catalyst_acquisition_v15 import install_catalyst_evidence_acquisition_v15
    install_catalyst_evidence_acquisition_v15()

    from .v8_primary import install_v8_primary_policy
    install_v8_primary_policy()

    from .discovery_recall_firewall_v15 import install_discovery_recall_firewall_v15
    install_discovery_recall_firewall_v15()

    from . import hunt_pipeline_v16 as v16
    from .catalyst_extractor_v16 import install_v16_extractor
    install_v16_extractor(v16)
    v16.install_hunt_pipeline_v16()

    from .hunt_resilience_v17 import install_hunt_resilience_v17
    install_hunt_resilience_v17()

    # Independently reproduced MAIN integrity defects: candidate isolation,
    # adverse/late evidence selection, canonical preservation and allocation
    # isolation. These layers add no new grade authority.
    from . import store as store_module
    if not hasattr(store_module, "_pre_v18_commit_final_allocation"):
        store_module._pre_v18_commit_final_allocation = store_module.SQLiteStore.commit_final_allocation
    from .hunt_integrity_v18 import install_hunt_integrity_v18
    from .hunt_integrity_v181 import install_hunt_integrity_v181
    from .hunt_integrity_v182 import install_hunt_integrity_v182
    install_hunt_integrity_v18()
    install_hunt_integrity_v181()
    install_hunt_integrity_v182()

    from .v8_next_terminal_lineage import install_pre_successor_terminal_capture
    install_pre_successor_terminal_capture()

    # Actual Step15/16/17/17.5/18/20 authority chain. Step18 is the only
    # Research Grade writer; Step20 validates and cannot create a grade.
    from .v8_next_successor import install_v8_next_successor
    install_v8_next_successor()
    from .v8_next_certification_v11 import install_v8_next_certification_v11
    install_v8_next_certification_v11()
    from .v8_next_runtime import install_v8_next_runtime
    install_v8_next_runtime()

    # Exact-source V8 MAIN scanners. The source bridge patches prompt
    # registration before MAIN coach installation. Missing/mismatched sources
    # are non-evaluable input failures, never paraphrased substitutes.
    from .v8_main_source_fidelity import install_v8_main_source_fidelity
    install_v8_main_source_fidelity()
    from .v8_main_discovery_coach import install_v8_main_discovery_coach
    install_v8_main_discovery_coach()
    from .v8_main_source_gate import install_v8_main_source_gate
    install_v8_main_source_gate()

    # Preserve strong Discovery names whose technical snapshot is unresolved.
    # This does not waive Stage/Execution gates; it creates explicit evidence
    # debt and forbids a false clean NO_TRADE/search-stop conclusion.
    from .v8_main_recall_conservation import install_v8_main_recall_conservation
    install_v8_main_recall_conservation()

    # Investment Rules v2.0: partial Market Context may continue Discovery,
    # while Market Execution remains strict. The proxy preserves canonical
    # insufficient-evidence receipts and only changes research admission.
    from .v8_market_discovery_admission import install_v8_market_discovery_admission
    install_v8_market_discovery_admission()

    from .v8_next_terminal_lineage import install_post_successor_terminal_restore
    install_post_successor_terminal_restore()

    from .shadow_health_v19 import install_shadow_health_v19
    install_shadow_health_v19()
    from .shadow_pointer_guard import install_shadow_pointer_guard
    install_shadow_pointer_guard()
    from .shadow_non_evaluable_guard import install_shadow_non_evaluable_guard
    install_shadow_non_evaluable_guard()

    _INSTALLED = True


def production_composition() -> dict[str, Any]:
    install_production_stack()
    from . import adapters, runtime, shadow
    from .discovery_recall_firewall_v15 import DISCOVERY_RECALL_FIREWALL_VERSION
    from .v8_main_discovery_coach import V8_MAIN_DISCOVERY_COACH_VERSION, V8_MAIN_FORENSIC_AUDIT_SHA256
    from .v8_main_source_fidelity import V8_MAIN_SOURCE_FIDELITY_VERSION, source_bundle_status
    from .v8_main_source_gate import V8_MAIN_SOURCE_GATE_VERSION
    from .v8_main_recall_conservation import V8_MAIN_RECALL_CONSERVATION_VERSION
    from .v8_market_discovery_admission import V8_MARKET_DISCOVERY_ADMISSION_VERSION
    from .v8_next_successor import V8_NEXT_POLICY_HASH, V8_NEXT_POLICY_VERSION

    cls = runtime.ProductionStockAgent
    mro = [f"{item.__module__}.{item.__name__}" for item in cls.__mro__]
    return {
        "runtime_module": cls.__module__,
        "runtime_class": cls.__name__,
        "mro": mro,
        "main_is_sole_discovery_owner": True,
        "python_scanner_routing_authority": False,
        "discovery_recall_lite_runtime_installed": any("DiscoveryRecallLiteProductionStockAgent" in item for item in mro),
        "discovery_breadth_provider_version": getattr(adapters.CompositeLiveMarketContextProvider, "discovery_recall_lite_version", None),
        "integrity_version": getattr(cls, "HUNT_INTEGRITY_VERSION", None),
        "integrity_patch_version": getattr(cls, "HUNT_INTEGRITY_PATCH_VERSION", None),
        "allocation_guard_version": getattr(cls, "ALLOCATION_GUARD_VERSION", None),
        "v8_primary_version": getattr(cls, "v8_primary_version", None),
        "v8_policy_version": V8_NEXT_POLICY_VERSION,
        "v8_ruleset_hash": V8_NEXT_POLICY_HASH,
        "v8_next_successor_version": getattr(cls, "v8_next_successor_version", None),
        "v8_next_runtime_version": getattr(cls, "v8_next_runtime_version", None),
        "v8_next_terminal_capture_version": getattr(cls, "v8_next_terminal_capture_version", None),
        "v8_next_terminal_restore_version": getattr(cls, "v8_next_terminal_restore_version", None),
        "v8_main_discovery_coach_version": getattr(cls, "v8_main_discovery_coach_version", V8_MAIN_DISCOVERY_COACH_VERSION),
        "v8_main_forensic_audit_sha256": getattr(cls, "v8_main_forensic_audit_sha256", V8_MAIN_FORENSIC_AUDIT_SHA256),
        "v8_main_source_fidelity_version": V8_MAIN_SOURCE_FIDELITY_VERSION,
        "v8_main_source_gate_version": getattr(cls, "v8_main_source_gate_version", V8_MAIN_SOURCE_GATE_VERSION),
        "v8_main_recall_conservation_version": getattr(cls, "v8_main_recall_conservation_version", V8_MAIN_RECALL_CONSERVATION_VERSION),
        "v8_market_discovery_admission_version": getattr(cls, "v8_market_discovery_admission_version", V8_MARKET_DISCOVERY_ADMISSION_VERSION),
        "v8_source_bundle": source_bundle_status(),
        "discovery_recall_firewall_version": DISCOVERY_RECALL_FIREWALL_VERSION,
        "shadow_health_version": getattr(shadow, "SHADOW_HEALTH_VERSION", None),
        "shadow_non_evaluable_guard_version": getattr(shadow.DailyShadowRunner, "shadow_non_evaluable_guard_version", None),
    }
