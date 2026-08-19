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


#: RC-0 — QUYẾT ĐỊNH KHÔNG-CHIẾU cũng là một quyết định, và nó phải bền.
#:
#:     WRITE-TIME DECISION → durable evidence
#:     REPLAY MAY RECOVER IT — REPLAY MAY NOT REINTERPRET IT
#:
#: Trước RC-0, "không đáng thành memory" chỉ được thể hiện bằng SỰ VẮNG MẶT
#: của một outbox row. Nhưng vắng mặt không phải bằng chứng:
#:
#:     ABSENCE OF OUTBOX != EVIDENCE THAT PROJECTION IS OWED
#:
#: replay nhìn hàng vắng rồi tự phán "chắc ai quên build" — và materialize
#: đúng cái marker mà substantive gate đã cắt. Nên quyết định được ghi vào
#: CHÍNH PAYLOAD (dưới checksum, bất biến), sống sót qua cả mất outbox row
#: lẫn version rebuild.
NO_PROJECTION = "no_projection"
DECISION_KEY = "decision"


def no_projection_fragment(reason: str,
                           projection_type: str = "cognitive_memory") -> dict:
    """Mảnh payload khai: writer đã QUYẾT không chiếu, và vì sao."""
    return {projection_type: {DECISION_KEY: NO_PROJECTION,
                              "reason": reason,
                              "contract_version": CONTRACT_VERSION}}


def recorded_decision(payload: dict[str, Any] | None,
                      projection_type: str = "cognitive_memory") -> str | None:
    """Quyết định lúc ghi, đọc từ payload bất biến.

    `"projection"` — writer muốn chiếu (có intent đầy đủ).
    `"no_projection"` — writer đã quyết KHÔNG chiếu, kèm lý do.
    `None` — event không ghi lại quyết định nào. Đây là UNKNOWN, và UNKNOWN
    KHÔNG được đoán theo hình dạng nội dung: một event trông như marker vẫn
    có thể đã có ký ức thật (đo được: 3 hàng `hook=SessionStart` của 07/08).
    """
    raw = ((payload or {}).get(INTENT_KEY) or {}).get(projection_type)
    if not isinstance(raw, dict):
        return None
    if raw.get(DECISION_KEY) == NO_PROJECTION:
        return NO_PROJECTION
    return "projection"


def intent_from_payload(payload: dict[str, Any] | None) -> MemoryProjectionIntent | None:
    """Đọc intent từ payload bất biến. Không có → None (event tiền-contract).

    Một mảnh `no_projection` KHÔNG phải intent — nó là quyết định ngược lại,
    và trả về một intent mặc định ở đây sẽ dựng ký ức từ chính thứ vừa từ
    chối dựng."""
    raw = ((payload or {}).get(INTENT_KEY) or {}).get("cognitive_memory")
    if not isinstance(raw, dict) or raw.get(DECISION_KEY) == NO_PROJECTION:
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
