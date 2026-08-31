from .alpha_bootstrap import install_alpha_discovery_policy
from .alpha_coverage_v14 import install_alpha_coverage_v14

# Install the proven live-data recall/freshness layers first.
install_alpha_discovery_policy()
install_alpha_coverage_v14()

# RUN-009 exposed a new bottleneck after V8 cheap-SEC recall was fixed:
# prescreen survivors reached CatalystGate, but the one-page research adapter
# left nearly every candidate NOT_EVALUATED_CATALYST_EVIDENCE. Install the
# V8-style source acquisition layer before CLI constructs providers.
from .catalyst_acquisition_v15 import install_catalyst_evidence_acquisition_v15  # noqa: E402
install_catalyst_evidence_acquisition_v15()

# V8 remains the canonical investment/research contract.
from .v8_primary import install_v8_primary_policy  # noqa: E402
install_v8_primary_policy()

# V1.6 changes research sequencing, not certification authority: initial
# catalyst insufficiency becomes evidence debt, Deep Research/Full SEC collect
# evidence, and the unchanged strict CatalystGate is rerun post-research.
from .hunt_pipeline_v16 import install_hunt_pipeline_v16  # noqa: E402
install_hunt_pipeline_v16()

# Import CLI only after every runtime/provider/shadow policy is installed.
from .cli import main  # noqa: E402

raise SystemExit(main())
