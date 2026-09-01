"""Canonical ``python -m stock_agent`` entry point."""

from .bootstrap import install_production_stack

install_production_stack()

from .cli import main  # noqa: E402

raise SystemExit(main())
