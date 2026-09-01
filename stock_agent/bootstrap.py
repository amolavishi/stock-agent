"""Canonical production composition for Stock Agent production entry points."""
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

    # Breadth/data acquisition only. This provider patch may widen the public
    # universe/ADV probe, but it has ZERO authority to discover or route a name.
    from .discovery_recall_lite_v15 import install_discovery_recall_lite_provider
    install_discovery_recall_lite_provider()

    from .catalyst_acquisition_v15 import install_catalyst_evidence_acquisition_v15
    install_catalyst_evidence_acquisition_v15()

    from .v8_primary import install_v8_primary_policy
    install_v8_primary_policy()

    # Discovery metadata may guide research routing but can never enter the
    # blind certification packet or become Research Grade authority.
    from .discovery_recall_firewall_v15 import install_discovery_recall_firewall_v15
    install_discovery_recall_firewall_v15()

    from . import hunt_pipeline_v16 as hunt_pipeline_v16_module
    from .catalyst_extractor_v16 import install_v16_extractor
    install_v16_extractor(hunt_pipeline_v16_module)
    hunt_pipeline_v16_module.install_hunt_pipeline_v16()

    from .hunt_resilience_v17 import install_hunt_resilience_v17
    install_hunt_resilience_v17()

    from . import store as store_module
    if not hasattr(store_module, "_pre_v18_commit_final_allocation"):
        store_module._pre_v18_commit_final_allocation = store_module.SQLiteStore.commit_final_allocation

    from .hunt_integrity_v18 import install_hunt_integrity_v18
    install_hunt_integrity_v18()
    from .hunt_integrity_v181 import install_hunt_integrity_v181
    install_hunt_integrity_v181()
    from .hunt_integrity_v182 import install_hunt_integrity_v182
    install_hunt_integrity_v182()

    from .v8_next_terminal_lineage import install_pre_successor_terminal_capture
    install_pre_successor_terminal_capture()
    from .v8_next_successor import install_v8_next_successor
    install_v8_next_successor()

    from .v8_next_certification_v11 import install_v8_next_certification_v11
    install_v8_next_certification_v11()
    from .v8_next_runtime import install_v8_next_runtime
    install_v8_next_runtime()

    # MAIN remains the sole final Discovery engine. This layer does not use
    # Python scanner heuristics. It forces actual model-executed V8 02..14
    # passes and feeds them back to workflow.stock_scout, which remains the one
    # DiscoveryCandidateSetV2 output owner.
    from .v8_main_discovery_coach import install_v8_main_discovery_coach
    install_v8_main_discovery_coach()

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
    from . import adapters
    from . import runtime
    from . import shadow
    from .discovery_recall_failure_guard_v16 import DISCOVERY_RECALL_FAILURE_GUARD_VERSION
    from .discovery_recall_firewall_v15 import DISCOVERY_RECALL_FIREWALL_VERSION
    from .v8_main_discovery_coach import V8_MAIN_DISCOVERY_COACH_VERSION, V8_MAIN_FORENSIC_AUDIT_SHA256
    cls = runtime.ProductionStockAgent
    market_cls = adapters.CompositeLiveMarketContextProvider
    mro = [f"{item.__module__}.{item.__name__}" for item in cls.__mro__]
    return {
        "runtime_module": cls.__module__,
        "runtime_class": cls.__name__,
        "mro": mro,
        "integrity_version": getattr(cls, "HUNT_INTEGRITY_VERSION", None),
        "integrity_patch_version": getattr(cls, "HUNT_INTEGRITY_PATCH_VERSION", None),
        "allocation_guard_version": getattr(cls, "ALLOCATION_GUARD_VERSION", None),
        "v8_next_successor_version": getattr(cls, "v8_next_successor_version", None),
        "v8_next_runtime_version": getattr(cls, "v8_next_runtime_version", None),
        "v8_policy_version": getattr(cls, "v8_primary_version", None),
        "v8_ruleset_hash": getattr(cls, "v8_ruleset_hash", None),
        "v8_main_discovery_coach_version": getattr(cls, "v8_main_discovery_coach_version", V8_MAIN_DISCOVERY_COACH_VERSION),
        "v8_main_forensic_audit_sha256": getattr(cls, "v8_main_forensic_audit_sha256", V8_MAIN_FORENSIC_AUDIT_SHA256),
        "main_is_sole_discovery_owner": True,
        "python_scanner_routing_authority": False,
        "discovery_recall_lite_runtime_installed": any("DiscoveryRecallLiteProductionStockAgent" in item for item in mro),
        "discovery_breadth_provider_version": getattr(market_cls, "discovery_recall_lite_version", None),
        "discovery_recall_firewall_version": DISCOVERY_RECALL_FIREWALL_VERSION,
        # Kept only so old failure-injection fixtures remain reproducible. The
        # production MRO MUST NOT install this Python routing authority.
        "discovery_recall_failure_guard_version": DISCOVERY_RECALL_FAILURE_GUARD_VERSION,
        "discovery_recall_failure_guard_runtime_installed": any("DiscoveryRecallFailureGuardProductionStockAgent" in item for item in mro),
        "v8_next_terminal_capture_version": getattr(cls, "v8_next_terminal_capture_version", None),
        "v8_next_terminal_restore_version": getattr(cls, "v8_next_terminal_restore_version", None),
        "shadow_health_version": getattr(shadow, "SHADOW_HEALTH_VERSION", None),
        "shadow_non_evaluable_guard_version": getattr(shadow.DailyShadowRunner, "shadow_non_evaluable_guard_version", None),
    }
