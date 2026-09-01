"""Canonical production composition for every Stock Agent entry point.

The repository still contains legacy install_* layers, but composition now has
one explicit, idempotent owner.  Both package import and ``python -m
stock_agent`` call this function so import order cannot silently select a
weaker runtime class.
"""
from __future__ import annotations


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

    from .shadow_pointer_guard import install_shadow_pointer_guard
    install_shadow_pointer_guard()

    _INSTALLED = True
