"""Stock Agent production package.

Python remains authoritative for workflow, gates, risk arithmetic and final
allocation.  Importing the package installs the same explicit production
composition used by ``python -m stock_agent`` so entry-point order cannot
silently select a weaker runtime.
"""

from .bootstrap import install_production_stack as _install_production_stack

_install_production_stack()

from .models import (
    ActionRecommendation,
    EffectiveRuleSet,
    ExecutionAction,
    GateDecision,
    RunMode,
    RunOutcome,
)
from .runtime import ProductionStockAgent, StockAgent
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

__all__ = [
    "ActionRecommendation",
    "EffectiveRuleSet",
    "ExecutionAction",
    "GateDecision",
    "RunMode",
    "RunOutcome",
    "StockAgent",
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
