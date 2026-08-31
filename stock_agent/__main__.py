from .alpha_bootstrap import install_alpha_discovery_policy
from .alpha_coverage_v14 import install_alpha_coverage_v14

# Install the proven live-data recall/freshness layers first.
install_alpha_discovery_policy()
install_alpha_coverage_v14()

# RUN-009 exposed a new bottleneck after V8 cheap-SEC recall was fixed:
# prescreen survivors reached CatalystGate, but the one-page research adapter
# left nearly every candidate NOT_EVALUATED_CATALYST_EVIDENCE.  Install the
# V8-style source-exhaustion acquisition layer before CLI constructs providers.
# It broadens evidence collection only; CatalystGate/A-grade/action authority
# remain unchanged and fail closed.
from .catalyst_acquisition_v15 import install_catalyst_evidence_acquisition_v15  # noqa: E402
install_catalyst_evidence_acquisition_v15()

# Resolve the V8 PRIMARY base *after* Alpha V1.3/V1.4 have patched runtime
# classes. This makes V8 the canonical research-process contract without
# discarding the session-robust 300-name probe coverage or Alpha technical
# features. Grade/action authority remains Python-owned and downstream.
from .v8_primary import install_v8_primary_policy  # noqa: E402
install_v8_primary_policy()

from .cli import main  # noqa: E402

raise SystemExit(main())
