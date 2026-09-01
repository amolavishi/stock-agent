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

    from .hunt_integrity_v18 import install_hunt_integrity_v18
    install_hunt_integrity_v18()
    from .hunt_integrity_v181 import install_hunt_integrity_v181
    install_hunt_integrity_v181()

    from .shadow_pointer_guard import install_shadow_pointer_guard
    install_shadow_pointer_guard()

    _INSTALLED = True


def production_composition() -> dict[str, Any]:
    """Return a deterministic description of the actual production stack."""
    install_production_stack()
    from . import runtime
    cls = runtime.ProductionStockAgent
    return {
        "runtime_module": cls.__module__,
        "runtime_class": cls.__name__,
        "mro": [f"{item.__module__}.{item.__name__}" for item in cls.__mro__],
        "integrity_version": getattr(cls, "HUNT_INTEGRITY_VERSION", None),
        "integrity_patch_version": getattr(cls, "HUNT_INTEGRITY_PATCH_VERSION", None),
    }
