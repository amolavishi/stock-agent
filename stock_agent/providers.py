from __future__ import annotations

from typing import Any, Protocol

from .schemas import CompanyState, CriticReview, EvidenceItem, MarketSnapshot, ResearchAnalysis


class MarketDataProvider(Protocol):
    def snapshot(self, ticker: str) -> MarketSnapshot: ...
    def company_state(self, ticker: str) -> CompanyState: ...


class EvidenceCollector(Protocol):
    def collect(self, ticker: str) -> list[EvidenceItem]: ...


class ResearchAgentProtocol(Protocol):
    def run(self, state: CompanyState, market: MarketSnapshot,
            evidence: list[EvidenceItem]) -> ResearchAnalysis: ...


class CriticAgentProtocol(Protocol):
    def run(self, research: ResearchAnalysis, state: CompanyState,
            market: MarketSnapshot) -> CriticReview: ...


class LLMProvider(Protocol):
    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]: ...


class DiscordNotifier(Protocol):
    def send(self, message: str) -> None: ...


class MockLLMProvider:
    async def generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        return {"mock": True, "prompt_length": len(prompt)}


class MockDiscordNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, message: str) -> None:
        self.messages.append(message)

