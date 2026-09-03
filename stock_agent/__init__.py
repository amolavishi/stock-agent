"""Stock Agent v1.1 package surface.

Python remains the authority for gates, workflow state, sizing and allocation.
The uncomposed runtime class is exported explicitly as ``BaseProductionStockAgent``.
Accessing ``stock_agent.ProductionStockAgent`` is a lazy compatibility alias to
the *composed* canonical production runtime in ``stock_agent.production`` so the
same public name can no longer mean two different runtime compositions.
"""

from .models import (
    ActionRecommendation,
    EffectiveRuleSet,
    ExecutionAction,
    GateDecision,
    RunMode,
    RunOutcome,
)
from .runtime import ProductionStockAgent as BaseProductionStockAgent, StockAgent
from .providers import CodexExecProvider
from .references import (ReferenceBuilder, ReferenceContractError, ReferencePack,
                         ReferencePackCompiler, ReferenceRecord, ReferenceRequirement,
                         ReferenceResolver)
from .reporting import AuthoritativeHuntReportRenderer, ReportContractError
from .adapters import (ConfiguredJsonMarketDataProvider, ConfiguredResearchEvidenceProvider,
                       FilesystemObsidianProjector, RecordedMarketDataProvider,
                       RecordedPortfolioProvider, RecordedResearchEvidenceProvider,
                       RecordedSECProvider, ResearchEvidenceProvider,
                       IssuerIRWebEvidenceProvider,
                       YahooFinanceNewsEvidenceProvider, CompositeResearchEvidenceProvider,
                       TossMarketDataProvider, TossPortfolioProvider,
                       UnavailableResearchEvidenceProvider)
from .shadow_pointer_guard import install_shadow_pointer_guard as _install_shadow_pointer_guard

# Keep ordinary package import side-effect-light. Production bootstrap occurs
# only when the production symbol is actually requested, while the base class
# remains available under an explicitly non-production name.
_install_shadow_pointer_guard()


def __getattr__(name: str):
    if name == "ProductionStockAgent":
        from .production import ProductionStockAgent as ComposedProductionStockAgent
        return ComposedProductionStockAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ActionRecommendation",
    "EffectiveRuleSet",
    "ExecutionAction",
    "GateDecision",
    "RunMode",
    "RunOutcome",
    "StockAgent",
    "BaseProductionStockAgent",
    "ProductionStockAgent",
    "RecordedMarketDataProvider",
    "RecordedPortfolioProvider",
    "RecordedResearchEvidenceProvider",
    "ResearchEvidenceProvider",
    "RecordedSECProvider",
    "FilesystemObsidianProjector",
    "AuthoritativeHuntReportRenderer",
    "ReportContractError",
    "ConfiguredJsonMarketDataProvider",
    "ConfiguredResearchEvidenceProvider",
    "IssuerIRWebEvidenceProvider",
    "YahooFinanceNewsEvidenceProvider",
    "CompositeResearchEvidenceProvider",
    "TossMarketDataProvider",
    "TossPortfolioProvider",
    "UnavailableResearchEvidenceProvider",
    "CodexExecProvider",
    "ReferenceBuilder",
    "ReferenceContractError",
    "ReferencePack",
    "ReferencePackCompiler",
    "ReferenceRecord",
    "ReferenceRequirement",
    "ReferenceResolver",
]
