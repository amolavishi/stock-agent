from .alpha_bootstrap import install_alpha_discovery_policy
from .alpha_coverage_v14 import install_alpha_coverage_v14

# Install discovery recall/catalyst/freshness policy first, then replace only
# the live broad-market provider with V1.4's session-robust probe coverage.
# Certification/action authority remains in the existing Python gates.
install_alpha_discovery_policy()
install_alpha_coverage_v14()

from .cli import main  # noqa: E402

raise SystemExit(main())
