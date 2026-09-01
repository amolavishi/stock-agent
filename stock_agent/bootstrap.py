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

    from .discovery_recall_lite_v15 import install_discovery_recall_lite_provider
    install_discovery_recall_lite_provider()
    from .discovery_recall_contract_v151 import install_discovery_recall_contract_v151
    install_discovery_recall_contract_v151()

    from .catalyst_acquisition_v15 import install_catalyst_evidence_acquisition_v15
    install_catalyst_evidence_acquisition_v15()

    from .v8_primary import install_v8_primary_policy
    install_v8_primary_policy()
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

    # Discovery Recall is research-routing authority only. The 02-14 receipts,
    # Secondary queue, rejection sentinel and search-stop audit are enforced
    # before a no-candidate outcome can be considered evaluable.
    from .discovery_recall_lite_v15 import install_discovery_recall_lite_runtime
    install_discovery_recall_lite_runtime()

    # Exact TEST 1-10 forensic invariants. This patches only Discovery routing:
    # UNKNOWN remains nonfatal; a verified discounted-VWAP convert remains a
    # structural fatality; explicit no-1-8W-event cases remain horizon mismatch;
    # structural failures are audit-ledgered but cannot consume research.
    from .discovery_recall_failure_guard_v16 import install_discovery_recall_failure_guard_v16
    install_discovery_recall_failure_guard_v16()

    from .discovery_recall_stop_bridge_v15 import install_discovery_recall_stop_bridge_v15
    install_discovery_recall_stop_bridge_v15()
    from .discovery_recall_contract_v151 import install_discovery_recall_ledger_v151
    install_discovery_recall_ledger_v151()

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
    from . import runtime
    from . import shadow
    from .discovery_recall_firewall_v15 import DISCOVERY_RECALL_FIREWALL_VERSION
    from .discovery_recall_stop_bridge_v15 import DISCOVERY_RECALL_STOP_BRIDGE_VERSION
    from .discovery_recall_contract_v151 import DISCOVERY_RECALL_CONTRACT_VERSION, DISCOVERY_RECALL_LEDGER_VERSION
    from .discovery_recall_failure_guard_v16 import DISCOVERY_RECALL_FAILURE_GUARD_VERSION
    cls = runtime.ProductionStockAgent
    return {
        "runtime_module": cls.__module__,
        "runtime_class": cls.__name__,
        "mro": [f"{item.__module__}.{item.__name__}" for item in cls.__mro__],
        "integrity_version": getattr(cls, "HUNT_INTEGRITY_VERSION", None),
        "integrity_patch_version": getattr(cls, "HUNT_INTEGRITY_PATCH_VERSION", None),
        "allocation_guard_version": getattr(cls, "ALLOCATION_GUARD_VERSION", None),
        "v8_next_successor_version": getattr(cls, "v8_next_successor_version", None),
        "v8_next_runtime_version": getattr(cls, "v8_next_runtime_version", None),
        "v8_policy_version": getattr(cls, "v8_primary_version", None),
        "v8_ruleset_hash": getattr(cls, "v8_ruleset_hash", None),
        "discovery_recall_lite_version": getattr(cls, "discovery_recall_lite_version", None),
        "discovery_recall_contract_version": DISCOVERY_RECALL_CONTRACT_VERSION,
        "discovery_recall_ledger_version": DISCOVERY_RECALL_LEDGER_VERSION,
        "discovery_recall_firewall_version": DISCOVERY_RECALL_FIREWALL_VERSION,
        "discovery_recall_failure_guard_version": DISCOVERY_RECALL_FAILURE_GUARD_VERSION,
        "discovery_recall_stop_bridge_version": DISCOVERY_RECALL_STOP_BRIDGE_VERSION,
        "discovery_recall_forensic_audit_sha256": getattr(cls, "discovery_recall_forensic_audit_sha256", None),
        "v8_next_terminal_capture_version": getattr(cls, "v8_next_terminal_capture_version", None),
        "v8_next_terminal_restore_version": getattr(cls, "v8_next_terminal_restore_version", None),
        "shadow_health_version": getattr(shadow, "SHADOW_HEALTH_VERSION", None),
        "shadow_non_evaluable_guard_version": getattr(shadow.DailyShadowRunner, "shadow_non_evaluable_guard_version", None),
    }