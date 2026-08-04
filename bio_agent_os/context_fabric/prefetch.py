from __future__ import annotations

from typing import Any

from bio_agent_os.cognitive.models import AccessContext, MemoryType
from bio_agent_os.cognitive.retrieval import HybridRetrievalEngine, RetrievalResult

from .models import PrefetchPlan


class PredictivePrefetcher:
    """Anticipatory memory retrieval based on the next likely action."""

    INTENT_MAP = {
        "deploy": ("policy", "procedure", "world_state", "exception"),
        "debug": ("procedure", "episode", "causal", "world_state"),
        "review": ("policy", "fact", "episode", "evidence"),
        "communicate": ("social", "identity", "policy", "fact"),
        "plan": ("prospective", "causal", "counterfactual", "world_state"),
        "operate": ("procedure", "policy", "world_state", "prospective"),
    }

    def __init__(self, retrieval: HybridRetrievalEngine):
        self.retrieval = retrieval

    def plan(self, action: str, state: dict[str, Any] | None = None, limit: int = 8) -> PrefetchPlan:
        state = state or {}
        lowered = action.lower()
        if any(x in lowered for x in ("deploy", "release", "production", "triển khai")):
            intent = "deploy"
        elif any(x in lowered for x in ("debug", "fix", "error", "sửa lỗi")):
            intent = "debug"
        elif any(x in lowered for x in ("review", "audit", "soi", "đánh giá")):
            intent = "review"
        elif any(x in lowered for x in ("email", "reply", "customer", "khách")):
            intent = "communicate"
        elif any(x in lowered for x in ("plan", "strategy", "kế hoạch", "what if")):
            intent = "plan"
        else:
            intent = "operate"
        types = self.INTENT_MAP[intent]
        query = f"{action}. Relevant {' '.join(types)}"
        enriched = {**state, "mode": intent, "goal": action}
        return PrefetchPlan(query=query, predicted_intent=intent, requested_memory_types=types, context_state=enriched, limit=limit,
                            reason=f"Predicted {intent} workflow from the proposed action")

    def execute(self, action: str, context: AccessContext, state: dict[str, Any] | None = None, limit: int = 8) -> tuple[PrefetchPlan, list[RetrievalResult]]:
        plan = self.plan(action, state, limit)
        return plan, self.retrieval.recall(plan.query, context, state=plan.context_state, limit=plan.limit)
