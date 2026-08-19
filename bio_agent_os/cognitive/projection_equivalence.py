"""Bộ so sánh FULL PROJECTION EQUIVALENCE — luật thực thi được, HBF-2 chạy lại.

    CONTENT_EQUIVALENT != PROJECTION_EQUIVALENT

HBF-0.1 so sánh bằng một khối inline — đủ cho inventory, không đủ làm luật.
Đây là bản tái sử dụng: mỗi HỢP ĐỒNG GHI lịch sử có tên, có phạm vi kiểm được,
và có tên-proof riêng để audit adoption ghi lại đúng mức mạnh của bằng chứng.

Hai hợp đồng lịch sử đã khảo cổ được (19/08):

  hook_call_site_v1   — event source `claude-code:<hook>`; semantics là CÔNG
                        THỨC theo hook type (0.72 / 0.55|0.35 / 0.75|0.50 /
                        0.65 + metadata.state). Kiểm được từng field.
                        proof = full_projection_contract_v1

  curated_seed_v1     — đợt seed thủ công 06/08 (`session-2026-08-06-...`):
                        tác giả chọn tay từng confidence/importance/type;
                        EVENT CHỈ MANG CONTENT, không mang score — nên không
                        có công thức nào tái tạo được intent, và hàng đã lưu
                        chính LÀ biểu đạt ý định. Kiểm được: content trùng
                        byte + link 1-1 + dấu curate trong metadata.
                        proof = content_plus_curated_provenance_v1
                        (YẾU HƠN CÓ KHAI BÁO — audit phải ghi đúng tên này,
                        không được nói full contract)

Chuẩn hoá metadata: chỉ MỘT namespace hạ tầng `metadata["projection"]` được
loại khỏi phép so — luật đã hồ sơ hoá ở SP-1.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

_SUB_IMP = {"UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
            "TaskCompleted", "StopFailure"}
_SAL_HI = {"PostToolUseFailure", "StopFailure", "PermissionDenied"}
_CURATED_SOURCES = ("session-2026-08-06-claude-code",)
_CURATED_MARKS = ("kind", "provenance", "domain", "customer", "scope")


@dataclass(slots=True)
class EquivalenceVerdict:
    equivalent: bool
    contract_name: str
    proof: str | None
    reasons: list


def hook_contract(hook: str) -> dict:
    return {"confidence": 0.72,
            "importance": 0.55 if hook in _SUB_IMP else 0.35,
            "salience": 0.75 if hook in _SAL_HI else 0.50,
            "utility": 0.65}


def compare(event_row: dict, memory_row: dict) -> EquivalenceVerdict:
    """event_row: source, payload_json. memory_row: content + semantic fields.

    Trả verdict THEO HỢP ĐỒNG của writer đã tạo event — không có hợp đồng
    nhận diện được thì KHÔNG tương đương, không đoán.
    """
    payload = json.loads(event_row.get("payload_json") or "{}")
    content = str(payload.get("content", ""))
    reasons: list = []

    if str(memory_row.get("content")) != content:
        return EquivalenceVerdict(False, "n/a", None, ["content lệch"])

    source = str(event_row.get("source") or "")
    meta = json.loads(memory_row.get("metadata_json") or "{}")
    meta.pop("projection", None)          # overlay hạ tầng — luật SP-1

    if source.startswith("claude-code:"):
        hook = source.split(":", 1)[1]
        want = hook_contract(hook)
        for k, v in want.items():
            got = memory_row.get(k)
            if got is None or abs(float(got) - v) > 1e-9:
                reasons.append(f"{k}={got} != {v}")
        if "state" not in meta:
            reasons.append("metadata.state thiếu")
        return EquivalenceVerdict(not reasons, "hook_call_site_v1",
                                  "full_projection_contract_v1" if not reasons
                                  else None, reasons)

    if source in _CURATED_SOURCES:
        if not any(k in meta for k in _CURATED_MARKS):
            reasons.append("thiếu dấu curate trong metadata — không chứng "
                           "minh được hàng này là biểu đạt ý định tác giả")
        return EquivalenceVerdict(not reasons, "curated_seed_v1",
                                  "content_plus_curated_provenance_v1"
                                  if not reasons else None, reasons)

    return EquivalenceVerdict(False, "UNKNOWN_WRITER", None,
                              [f"nguồn {source!r} chưa có hợp đồng được gọi tên"])
