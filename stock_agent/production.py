"""Explicit direct-import API for the composed production runtime.

Use this module when embedding Stock Agent as a library.  It is intentionally
separate from the side-effect-light package root and guarantees the same stack
installed by ``python -m stock_agent``.
"""
from __future__ import annotations

from .bootstrap import install_production_stack, production_composition

install_production_stack()

from . import runtime as _runtime  # noqa: E402

ProductionStockAgent = _runtime.ProductionStockAgent

__all__ = ["ProductionStockAgent", "production_composition"]
