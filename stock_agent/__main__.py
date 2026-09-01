"""Canonical ``python -m stock_agent`` entry point."""
from __future__ import annotations

import json
import os

from .bootstrap import install_production_stack, production_composition

install_production_stack()

if os.getenv("STOCK_AGENT_COMPOSITION_PROBE") == "1":
    print(json.dumps(production_composition(), sort_keys=True))
else:
    from .cli import main  # noqa: E402
    raise SystemExit(main())
