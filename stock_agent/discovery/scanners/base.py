from __future__ import annotations

from typing import Protocol

from ..schemas import CandidateFeatureSnapshot, DiscoveryContext, ScannerResult


class DiscoveryScanner(Protocol):
    name: str
    version: str

    def evaluate(self, candidate: CandidateFeatureSnapshot, context: DiscoveryContext) -> ScannerResult: ...
