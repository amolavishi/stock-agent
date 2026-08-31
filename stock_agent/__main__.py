from .alpha_bootstrap import install_alpha_discovery_policy

# Install the discovery-recall policy before cli imports its concrete provider
# and ProductionStockAgent classes.  Certification/action authority remains in
# the existing Python gates.
install_alpha_discovery_policy()

from .cli import main  # noqa: E402

raise SystemExit(main())
