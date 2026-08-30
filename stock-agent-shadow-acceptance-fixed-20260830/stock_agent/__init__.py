"""Stock Agent v1.1 production runtime.

The package deliberately keeps authority in Python. Prompt execution is an
adapter boundary; no LLM response can write gates, workflow state, sizing, or
the final allocation.
"""

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
