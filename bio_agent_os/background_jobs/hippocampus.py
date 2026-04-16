"""
background_jobs/hippocampus.py — Tiến trình "Ngủ" (Sleep Consolidation).

Mô phỏng Hồi Hải Mã (Hippocampus) của não người:
  1. Real-time: Dán nhãn metadata cho dữ liệu thô ngay khi tiếp nhận
  2. Sleep mode: Đọc L1, gọi LLM nén thành Luật Logic, lưu vào Persona (Core Identity)

Đây là tiến trình quan trọng nhất — biến "Sự kiện" thành "Nhận thức".
"""

import asyncio
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

from bio_agent_os.core.llm_engine import LLMEngine
from bio_agent_os.core.persona import Persona
from bio_agent_os.memory.l1_working import L1WorkingMemory
from bio_agent_os.memory.l2_semantic import L2SemanticMemory


# ─── Pydantic Schemas for Structured LLM Output ──────────

class MemoryLabel(BaseModel):
    """Schema cho việc dán nhãn dữ liệu thô."""
    topic: str = Field(description="Chủ đề chính (vd: Bug fix, Cảm xúc, Kiến thức)")
    importance_score: int = Field(description="Độ quan trọng 1-10 cho sinh tồn/tri thức lâu dài")
    is_junk_or_transient: bool = Field(description="True nếu là rác/cảm xúc nhất thời")
    user_state: str = Field(description="Trạng thái cảm xúc người nói (Vui, Buồn, Bình thường...)")

class EncodedRule(BaseModel):
    """Schema cho việc nén sự kiện thành logic trừu tượng."""
    abstract_rule: str = Field(description="Quy tắc logic cốt lõi rút ra, loại bỏ mọi ngữ cảnh cụ thể")


class Hippocampus:
    """
    The brain's consolidation engine.
    
    Two modes of operation:
      1. label()     — Real-time: Tag incoming data with importance/junk metadata
      2. consolidate() — Sleep mode: Compress surviving L1 entries into Core Logic
    
    Usage:
        hippo = Hippocampus(engine=llm, l1=l1_mem, persona=persona)
        
        # Real-time labeling
        metadata = await hippo.label("BUG: API trả về 500 do thiếu await")
        l1.add(content, metadata=metadata)
        
        # Sleep consolidation  
        stats = await hippo.consolidate()
        print(f"Encoded {stats['encoded']} rules")
    """

    def __init__(
        self,
        engine: LLMEngine,
        l1: L1WorkingMemory,
        persona: Persona,
        l2: Optional[L2SemanticMemory] = None,
    ):
        self.engine = engine
        self.l1 = l1
        self.persona = persona
        self.l2 = l2
        self._log: List[str] = []

    @property
    def logs(self) -> List[str]:
        return list(self._log)

    def clear_logs(self):
        self._log.clear()

    # ─── Real-time Labeling ───────────────────────────────────

    async def label(self, raw_data: str, source: str = "unknown") -> Dict[str, Any]:
        """
        Hippocampus Librarian — Analyze and tag raw input in real-time.
        Returns metadata dict for storage in L1.
        """
        self._log.append(f"Hippocampus đang phân tích từ {source}...")

        prompt = f"""Bạn là Hồi Hải Mã (Hippocampus) của một AI.
Phân loại dữ liệu thô sau: đánh giá độ quan trọng (1-10), xác định có phải rác không.
Dữ liệu: "{raw_data[:500]}"
"""
        try:
            metadata = await self.engine.generate_structured(
                prompt, schema=MemoryLabel, temperature=0.1
            )
            self._log.append(
                f"Lưu Episodic: Điểm {metadata['importance_score']}/10 | "
                f"Rác: {metadata['is_junk_or_transient']}"
            )
            return metadata
        except Exception as e:
            self._log.append(f"Lỗi label: {e}")
            return {
                "topic": "unknown",
                "importance_score": 5,
                "is_junk_or_transient": False,
                "user_state": "unknown",
            }

    async def label_and_store(self, raw_data: str, source: str = "unknown") -> Dict[str, Any]:
        """Label raw data and immediately store in L1."""
        metadata = await self.label(raw_data, source)
        entry = self.l1.add(content=raw_data, source=source, metadata=metadata)
        return entry

    # ─── Sleep Consolidation (Encoding Shift) ─────────────────

    async def consolidate(self) -> Dict[str, int]:
        """
        Sleep mode — Encoding Shift.
        
        Read L1 survivors (entries that outlived their TTL),
        compress them into abstract logic rules via LLM,
        and store in Persona (Core Identity).
        
        Returns stats: {encoded: N, failed: N}
        """
        self._log.append("----- BẮT ĐẦU VÒNG LẶP ĐÊM (Sleep Consolidation) -----")
        survivors = self.l1.get_survivors()
        stats = {"encoded": 0, "failed": 0}

        if not survivors:
            self._log.append("Không có sự kiện nào cần nén.")
            self._log.append("----- KẾT THÚC VÒNG LẶP ĐÊM -----")
            return stats

        self._log.append(f"Tìm thấy {len(survivors)} sự kiện cần chuyển hóa.")

        for entry in survivors:
            content = entry["content"]
            metadata = entry.get("metadata", {})

            self._log.append(f"Đang nén: '{content[:40]}...'")

            prompt = f"""Dựa trên nội dung thô: "{content}"
Metadata (Cảm xúc: {metadata.get('user_state', 'N/A')}).

Bạn là cơ chế mã hóa (Encoding Shift) của não bộ.
Hãy rút ra MỘT quy tắc logic trừu tượng (abstract rule) có thể áp dụng phổ quát.
Loại bỏ mọi chi tiết ngữ cảnh, cảm xúc, thời gian cụ thể.
"""
            try:
                result = await self.engine.generate_structured(
                    prompt, schema=EncodedRule, temperature=0.2
                )
                rule_text = result["abstract_rule"]
                rule_id = self.persona.add_rule(rule_text)
                self.l1.mark_encoded(entry["timestamp"])

                # Also store in L2 if available
                if self.l2:
                    self.l2.store(
                        content=rule_text,
                        importance=metadata.get("importance_score", 5),
                        tags=[metadata.get("topic", "general")],
                        source_rule_id=rule_id,
                    )

                self._log.append(f"+ Core Identity mới: {rule_text[:80]}...")
                stats["encoded"] += 1
            except Exception as e:
                self._log.append(f"Lỗi nén: {e}")
                stats["failed"] += 1

        self._log.append("----- KẾT THÚC VÒNG LẶP ĐÊM -----")
        return stats

    def __repr__(self) -> str:
        return f"Hippocampus(l1={self.l1.count} entries, persona={self.persona.rule_count} rules)"
