"""
core/router.py — Intent Router (Bộ phân luồng).

Phân loại intent của User: 
  - Có cần truy xuất L2 Semantic Memory không?
  - Hay chỉ dùng L1 Working Memory (hội thoại gần đây)?
  - Có cần kích hoạt Knowledge Graph lookup không?
"""

from enum import Enum
from typing import Optional
from bio_agent_os.core.llm_engine import LLMEngine
from pydantic import BaseModel, Field


class IntentType(str, Enum):
    """Loại intent mà Router phân loại được."""
    CASUAL = "casual"           # Chat thông thường, chỉ cần L1
    KNOWLEDGE = "knowledge"     # Cần truy xuất kiến thức từ L2/Graph
    TASK = "task"               # Yêu cầu thực thi tác vụ cụ thể
    RECALL = "recall"           # Người dùng hỏi lại thông tin cũ


class RoutedIntent(BaseModel):
    """Kết quả phân loại intent."""
    intent_type: str = Field(description="Loại intent: casual, knowledge, task, recall")
    needs_l2: bool = Field(description="True nếu cần truy xuất Long-term Semantic Memory")
    needs_graph: bool = Field(description="True nếu cần tra cứu Knowledge Graph")
    summary: str = Field(description="Tóm tắt ngắn gọn ý định của user")


class IntentRouter:
    """
    Phân loại user message và quyết định pipeline xử lý.
    
    Flow:
      User message → Router → { L1 only | L1 + L2 | L1 + L2 + Graph }
    
    Usage:
        router = IntentRouter(engine=llm_engine)
        intent = await router.classify("Hôm qua tao nói gì về project X?")
        if intent.needs_l2:
            # query L2 semantic memory
    """

    def __init__(self, engine: LLMEngine):
        self.engine = engine

    async def classify(self, user_message: str) -> RoutedIntent:
        """Classify user intent and determine which memory layers to activate."""
        prompt = f"""Bạn là bộ phân luồng (Router) của một hệ thống AI.
Hãy phân loại tin nhắn sau của người dùng:

Tin nhắn: "{user_message}"

Phân loại theo:
- intent_type: "casual" (chat thường), "knowledge" (hỏi kiến thức), "task" (yêu cầu làm việc), "recall" (hỏi lại thông tin cũ)
- needs_l2: True nếu cần tra cứu bộ nhớ dài hạn (kiến thức chuyên sâu, sự kiện cũ)
- needs_graph: True nếu câu hỏi liên quan đến mối quan hệ giữa các thực thể (người, dự án, công nghệ)
- summary: Tóm tắt ý định 1 câu
"""
        try:
            result = await self.engine.generate_structured(
                prompt, schema=RoutedIntent, temperature=0.1
            )
            return RoutedIntent(**result)
        except Exception:
            # Fallback: treat as casual
            return RoutedIntent(
                intent_type="casual",
                needs_l2=False,
                needs_graph=False,
                summary=user_message[:100],
            )

    def quick_classify(self, user_message: str) -> IntentType:
        """
        Fast heuristic classification (no LLM call).
        Use when latency matters more than accuracy.
        """
        msg = user_message.lower()
        recall_keywords = ["hôm qua", "lần trước", "nhớ không", "đã nói", "bao giờ"]
        knowledge_keywords = ["giải thích", "là gì", "tại sao", "cách nào", "so sánh"]
        task_keywords = ["giúp", "làm", "tạo", "viết", "sửa", "deploy", "build"]

        if any(kw in msg for kw in recall_keywords):
            return IntentType.RECALL
        elif any(kw in msg for kw in knowledge_keywords):
            return IntentType.KNOWLEDGE
        elif any(kw in msg for kw in task_keywords):
            return IntentType.TASK
        else:
            return IntentType.CASUAL
