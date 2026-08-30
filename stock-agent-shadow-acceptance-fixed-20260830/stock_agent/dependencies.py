from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import DependencyState, EffectiveRuleSet, canonical_hash
from .store import SQLiteStore


@dataclass(frozen=True)
class FreshnessDecision:
    fresh: bool
    state: str
    reason: str | None = None


class DependencyFence:
    """Python-owned dependency hash/epoch fence used before every commit."""

    def __init__(self, store: SQLiteStore) -> None:
        self.store = store

    def state(self, dependency_ids: Iterable[str], rules: EffectiveRuleSet, context_manifest_hash: str) -> DependencyState:
        ids = sorted(set(dependency_ids))
        return DependencyState(self.store.dependency_hash(ids, rules.rule_set_hash, context_manifest_hash), self.store.current_evidence_epoch_for(ids), rules.rule_set_hash, context_manifest_hash)

    @staticmethod
    def compare(expected: DependencyState, current: DependencyState) -> FreshnessDecision:
        if expected.dependency_hash != current.dependency_hash:
            return FreshnessDecision(False, "STALE_ON_ARRIVAL", "dependency_hash_changed")
        if expected.evidence_epoch != current.evidence_epoch:
            return FreshnessDecision(False, "STALE_ON_ARRIVAL", "evidence_epoch_changed")
        if expected.rule_set_hash != current.rule_set_hash:
            return FreshnessDecision(False, "STALE_ON_ARRIVAL", "rule_set_changed")
        if expected.context_manifest_hash != current.context_manifest_hash:
            return FreshnessDecision(False, "STALE_ON_ARRIVAL", "context_manifest_changed")
        return FreshnessDecision(True, "FRESH")


