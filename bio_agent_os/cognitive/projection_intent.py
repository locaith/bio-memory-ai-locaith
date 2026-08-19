"""Hợp đồng ghi được LƯU BỀN trong event — một constructor cho mọi writer.

SP-0 đo được product regression: builder outbox dựng `CognitiveMemory` trực
tiếp nên mọi score rơi về default model (0.5) và `metadata.state` biến mất,
trong khi hook legacy truyền tường minh (confidence 0.72, importance theo
hook, utility 0.65, state.mode/stress_state). Cùng nội dung, cùng query:
retrieval score lệch 3.330 vs 3.172 — product-visible.

Sửa bằng cách cho builder "tự suy lại semantics từ hook" sẽ drift ngay lần
call-site đổi giá trị. Nguồn sự thật phải là chính EVENT:

    event.payload["projection_intents"]["cognitive_memory"]  (bất biến, có
    checksum — event.metadata KHÔNG đủ mạnh vì không nằm dưới checksum)

và cả hai writer đi qua ĐÚNG MỘT hàm dựng:

    MemoryProjectionIntent
            │
            ▼
    build_memory_from_event(event, intent)
            │
            ├── LEGACY  remember()
            └── OUTBOX  CognitiveMemoryBuilder

Hai luật trả học phí ở SP-0, giữ tại đây:

    CONTENT_EQUIVALENT != PROJECTION_EQUIVALENT
    EXACTLY-ONCE EXECUTION != SEMANTIC PARITY
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .models import (BeliefState, CognitiveMemory, EpistemicStatus,
                     EventRecord, MemoryType, VerificationStatus)

CONTRACT_VERSION = 1
INTENT_KEY = "projection_intents"


@dataclass(slots=True)
class MemoryProjectionIntent:
    """Điều người ghi MUỐN, đóng băng tại thời điểm ghi, mang theo event."""

    memory_type: str = MemoryType.EPISODIC.value
    confidence: float = 0.6           # default của facade remember() — tầng 2
    importance: float = 0.5
    salience: float = 0.5
    utility: float = 0.5
    lifecycle_state: str = BeliefState.PROPOSED.value
    structured_content: dict[str, Any] = field(default_factory=dict)
    epistemic_status: str | None = None
    verification_status: str = VerificationStatus.UNVERIFIED.value
    applicable_context: dict[str, Any] = field(default_factory=dict)
    semantic_metadata: dict[str, Any] = field(default_factory=dict)
    contract_version: int = CONTRACT_VERSION

    def as_payload_fragment(self) -> dict[str, Any]:
        return {"cognitive_memory": asdict(self)}


def intent_from_payload(payload: dict[str, Any] | None) -> MemoryProjectionIntent | None:
    """Đọc intent từ payload bất biến. Không có → None (event tiền-contract)."""
    raw = ((payload or {}).get(INTENT_KEY) or {}).get("cognitive_memory")
    if not isinstance(raw, dict):
        return None
    known = {f for f in MemoryProjectionIntent.__dataclass_fields__}
    return MemoryProjectionIntent(**{k: v for k, v in raw.items() if k in known})


def build_memory_from_event(event: EventRecord,
                            intent: MemoryProjectionIntent,
                            *, content: str | None = None) -> CognitiveMemory:
    """HÀM DỰNG DUY NHẤT. Mọi field semantic đi từ intent; mọi field nguồn
    gốc đi từ event. Không writer nào được tự map field ngoài chỗ này."""
    payload = event.payload or {}
    return CognitiveMemory(
        tenant_id=event.tenant_id,
        workspace_id=event.workspace_id,
        memory_type=MemoryType(intent.memory_type),
        content=content if content is not None else str(payload.get("content", "")),
        source_event_ids=[event.event_id],
        trust_tier=event.trust_tier,
        security_label=event.security_label,
        valid_from=event.valid_from,
        valid_to=event.valid_to,
        observed_at=event.observed_at,
        lifecycle_state=BeliefState(intent.lifecycle_state),
        confidence=max(0.0, min(float(intent.confidence), 1.0)),
        importance=max(0.0, min(float(intent.importance), 1.0)),
        salience=max(0.0, min(float(intent.salience), 1.0)),
        utility=max(0.0, min(float(intent.utility), 1.0)),
        structured_content=dict(intent.structured_content or {}),
        epistemic_status=(EpistemicStatus(intent.epistemic_status)
                          if intent.epistemic_status else event.epistemic_status),
        verification_status=VerificationStatus(intent.verification_status),
        applicable_context=dict(intent.applicable_context or {}),
        metadata=dict(intent.semantic_metadata or {}),
        modality=event.modality,
    )
