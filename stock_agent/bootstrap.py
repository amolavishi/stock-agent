"""Canonical production composition for Stock Agent production entry points.

Legacy install_* layers remain during migration, but composition now has one
explicit, idempotent owner.  ``python -m stock_agent`` and
``stock_agent.production`` both call this function.  Ordinary package/submodule
imports remain side-effect-light so unit/library behavior cannot be silently
changed by import order.
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

    from .catalyst_acquisition_v15 import install_catalyst_evidence_acquisition_v15
    install_catalyst_evidence_acquisition_v15()

    from .v8_primary import install_v8_primary_policy
    install_v8_primary_policy()

    from . import hunt_pipeline_v16 as hunt_pipeline_v16_module
    from .catalyst_extractor_v16 import install_v16_extractor
    install_v16_extractor(hunt_pipeline_v16_module)
    hunt_pipeline_v16_module.install_hunt_pipeline_v16()

    from .hunt_resilience_v17 import install_hunt_resilience_v17
    install_hunt_resilience_v17()

    # Preserve the complete production Final Allocation writer immediately
    # before V1.8 adds its integrity guard.  V1.8.2 uses this exact writer to
    # replace an over-broad run-level failure veto with a subject-scoped veto
    # without bypassing any pre-existing qualification/lineage/risk checks.
    from . import store as store_module
    if not hasattr(store_module, "_pre_v18_commit_final_allocation"):
        store_module._pre_v18_commit_final_allocation = store_module.SQLiteStore.commit_final_allocation

    from .hunt_integrity_v18 import install_hunt_integrity_v18
    install_hunt_integrity_v18()
    from .hunt_integrity_v181 import install_hunt_integrity_v181
    install_hunt_integrity_v181()
    from .hunt_integrity_v182 import install_hunt_integrity_v182
    install_hunt_integrity_v182()

    # V8 NEXT is the active successor investment-policy contract.  It is
    # deliberately installed after the V1.8 integrity layers so it can
    # supersede the legacy Step-18 source pin and add the 00A breadth floor
    # without weakening any earlier failure/lineage guard.
    from .v8_next_successor import install_v8_next_successor
    install_v8_next_successor()

    # Tighten the Python grade engine before wiring Step15->20 into the live
    # candidate loop.  The model remains analysis-only; Python owns grade,
    # arithmetic, lineage, caps and final qualification.
    from .v8_next_certification_v11 import install_v8_next_certification_v11
    install_v8_next_certification_v11()
    from .v8_next_runtime import install_v8_next_runtime
    install_v8_next_runtime()

    # Provider health is a transport probe only.  Do not make PRIMARY recreate
    # a full MarketAnalysisResult merely to test Luna connectivity; canonical
    # manifest hashes belong to research outputs, not health semantics.
    from .shadow_health_v19 import install_shadow_health_v19
    install_shadow_health_v19()

    from .shadow_pointer_guard import install_shadow_pointer_guard
    install_shadow_pointer_guard()

    _INSTALLED = True


def production_composition() -> dict[str, Any]:
    """Return a deterministic description of the actual production stack."""
    install_production_stack()
    from . import runtime
    from . import shadow
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
        "shadow_health_version": getattr(shadow, "SHADOW_HEALTH_VERSION", None),
    }