from __future__ import annotations

from .models import ContextBlock, StorageTier


class TieringScheduler:
    """Hardware-aware placement policy independent of any one vendor backend."""

    @staticmethod
    def placement_score(block: ContextBlock, current_goal_relevance: float | None = None) -> float:
        relevance = block.relevance_score if current_goal_relevance is None else current_goal_relevance
        return (
            block.expected_reuse * 0.24
            + block.importance * 0.18
            + block.latency_sensitivity * 0.18
            + block.recomputation_cost * 0.14
            + relevance * 0.18
            + block.trust_score * 0.08
        )

    def choose_tier(self, block: ContextBlock, current_goal_relevance: float | None = None) -> StorageTier:
        if block.tier == StorageTier.QUARANTINE:
            return StorageTier.QUARANTINE
        score = self.placement_score(block, current_goal_relevance)
        if score >= 0.78:
            return StorageTier.HOT
        if score >= 0.58:
            return StorageTier.WARM
        if score >= 0.32:
            return StorageTier.COLD
        return StorageTier.ARCHIVE
