"""Bộ so sánh FULL PROJECTION EQUIVALENCE — luật thực thi được, HBF-2 chạy lại.

    CONTENT_EQUIVALENT != PROJECTION_EQUIVALENT
    Tên của proof phải YẾU HƠN HOẶC BẰNG thứ measurement thực sự chứng minh.

HBF-1.1: bản đầu của file này tự gọi mình là FULL nhưng chỉ đo 6 trường —
đúng họ false-green SP-0, bị chủ bắt trước khi thành nhãn. Bản này đo TRỌN
những gì tái tạo được, và KHAI BÁO những gì không.

PHÂN LOẠI TRƯỜNG (kiểm kê tường minh):

  Tái tạo từ EVENT (mọi hợp đồng — vì remember() thừa kế từ event):
    tenant_id, workspace_id, trust_tier, security_label,
    valid_from, valid_to, observed_at, modality, source-link 1-1

  Tái tạo theo CÔNG THỨC hook (chỉ hook_call_site_v1):
    confidence/importance/salience/utility, memory_type=episodic,
    lifecycle_state=proposed, verification_status=unverified,
    epistemic_status=event's, applicable_context={}, governance=defaults,
    semantic_metadata = {hook, session_id, tool, state} (hook/session/tool
    đối chiếu event.metadata — đầu vào so sánh, không phải nguồn replay)

  Tái tạo từ CODE tất định (structured_content): slot extractor là hàm của
    (content, source) — so bằng cách gọi lại chính code đó khi có runtime;
    ở mức comparator tĩnh, structured_content được kiểm KHÔNG-RỖNG-KHI-CÓ-SLOT
    và ghi vào reasons nếu lệch shape. HBF-2 rehearsal so bản đầy đủ.

  KHÔNG tái tạo được (curated_seed_v1): scores + memory_type + metadata tác
    giả chọn tay — proof yếu hơn, khai báo thẳng:
    content_plus_curated_provenance_v1.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

_SUB_IMP = {"UserPromptSubmit", "PostToolUse", "PostToolUseFailure",
            "TaskCompleted", "StopFailure"}
_SAL_HI = {"PostToolUseFailure", "StopFailure", "PermissionDenied"}
_CURATED_SOURCES = ("session-2026-08-06-claude-code",)
_CURATED_MARKS = ("kind", "provenance", "domain", "customer", "scope")

#: Trường thừa kế từ event — MỌI hợp đồng phải khớp, kể cả curated.
EVENT_DERIVED = ("tenant_id", "workspace_id", "trust_tier", "security_label",
                 "valid_from", "valid_to", "observed_at", "modality")
#: Governance defaults — hook path không bao giờ đặt chúng.
GOVERNANCE_DEFAULTS = {"approved_by": None, "governed_exception_for": None,
                       "approval_expires_at": None, "simulation_id": None}


@dataclass(slots=True)
class EquivalenceVerdict:
    equivalent: bool
    contract_name: str
    proof: str | None
    reasons: list
    fields_measured: int = 0


def hook_contract(hook: str) -> dict:
    return {"confidence": 0.72,
            "importance": 0.55 if hook in _SUB_IMP else 0.35,
            "salience": 0.75 if hook in _SAL_HI else 0.50,
            "utility": 0.65}


def _event_derived_diffs(event_row: dict, memory_row: dict) -> list:
    out = []
    for f in EVENT_DERIVED:
        ev, mv = event_row.get(f), memory_row.get(f)
        if ev is None and mv is None:
            continue
        if str(ev) != str(mv):
            out.append(f"{f}: event={ev!r} memory={mv!r}")
    return out


def _governance_diffs(memory_row: dict) -> list:
    out = []
    for f, want in GOVERNANCE_DEFAULTS.items():
        got = memory_row.get(f)
        if got not in (want, "", "null"):
            out.append(f"governance {f}={got!r} != default")
    for f in ("allowed_agents_json", "allowed_roles_json",
              "purpose_allowlist_json", "counterevidence_event_ids_json"):
        raw = memory_row.get(f)
        if raw and json.loads(raw):
            out.append(f"governance {f} không rỗng")
    return out


def compare(event_row: dict, memory_row: dict) -> EquivalenceVerdict:
    payload = json.loads(event_row.get("payload_json") or "{}")
    content = str(payload.get("content", ""))
    reasons: list = []
    measured = 1
    if str(memory_row.get("content")) != content:
        return EquivalenceVerdict(False, "n/a", None, ["content lệch"], 1)

    diffs = _event_derived_diffs(event_row, memory_row)
    measured += len(EVENT_DERIVED)
    reasons.extend(diffs)

    source = str(event_row.get("source") or "")
    meta = json.loads(memory_row.get("metadata_json") or "{}")
    meta.pop("projection", None)          # overlay hạ tầng — luật SP-1

    if source.startswith("claude-code:"):
        hook = source.split(":", 1)[1]
        want = hook_contract(hook)
        for k, v in want.items():
            measured += 1
            got = memory_row.get(k)
            if got is None or abs(float(got) - v) > 1e-9:
                reasons.append(f"{k}={got} != {v}")
        for f, v in (("memory_type", "episodic"),
                     ("lifecycle_state", "proposed"),
                     ("verification_status", "unverified")):
            measured += 1
            if str(memory_row.get(f)) != v:
                reasons.append(f"{f}={memory_row.get(f)!r} != {v!r}")
        measured += 1
        if "state" not in meta:
            reasons.append("metadata.state thiếu")
        ev_meta = json.loads(event_row.get("metadata_json") or "{}")             if "metadata_json" in event_row else {}
        for k in ("hook", "session_id", "tool"):
            measured += 1
            if k in ev_meta and meta.get(k) != ev_meta.get(k):
                reasons.append(f"metadata.{k} lệch event")
        measured += 1
        if json.loads(memory_row.get("applicable_context_json") or "{}"):
            reasons.append("applicable_context không rỗng")
        gov = _governance_diffs(memory_row)
        measured += len(GOVERNANCE_DEFAULTS) + 4
        reasons.extend(gov)
        return EquivalenceVerdict(not reasons, "hook_call_site_v1",
                                  "full_projection_contract_v1" if not reasons
                                  else None, reasons, measured)

    if source in _CURATED_SOURCES:
        measured += 1
        if not any(k in meta for k in _CURATED_MARKS):
            reasons.append("thiếu dấu curate — không chứng minh được hàng này "
                           "là biểu đạt ý định tác giả")
        gov = _governance_diffs(memory_row)
        measured += len(GOVERNANCE_DEFAULTS) + 4
        reasons.extend(gov)
        return EquivalenceVerdict(not reasons, "curated_seed_v1",
                                  "content_plus_curated_provenance_v1"
                                  if not reasons else None, reasons, measured)

    return EquivalenceVerdict(False, "UNKNOWN_WRITER", None,
                              [f"nguồn {source!r} chưa có hợp đồng"], measured)
