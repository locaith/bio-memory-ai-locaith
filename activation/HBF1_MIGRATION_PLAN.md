# HBF-1 — HISTORICAL CONTRACT DECISION & MIGRATION PLAN (19/08)

Phạm vi đã ký: semantics + khảo cổ hợp đồng + kế hoạch. KHÔNG mutation.

## Nợ thủ tục ghi sổ (luật mới của HBF)

SP-2 repair ĐÚNG về product nhưng SAI về thủ tục: mở write connection và
UPDATE thẳng store thật thay vì candidate-offline → certify → install_generation.

> **Having a rollback point does not make live in-place mutation a safe
> migration protocol.**

HBF-2 chỉ được mutate candidate generation OFFLINE.

## Khảo cổ 27 CONTRACT_UNKNOWN — đã gọi tên

Cả 27 cùng một nguồn: `session-2026-08-06-claude-code` — đợt seed thủ công
06/08, tác giả (Tuấn Anh + Claude) chọn tay từng confidence/importance/type
(0.9–0.95, procedural/semantic, metadata giàu: domain/kind/provenance/customer).
**Event chỉ mang content** — không công thức nào tái tạo được intent; hàng đã
lưu chính LÀ biểu đạt ý định. Hợp đồng: `curated_seed_v1`, proof
`content_plus_curated_provenance_v1` — **yếu hơn có khai báo** so với
`full_projection_contract_v1`, audit phải ghi đúng tên, không nói vống.

## Luật thực thi được

`bio_agent_os/cognitive/projection_equivalence.py` — comparator tái sử dụng,
HBF-2 chạy lại nguyên trạng. Toàn population re-classify qua nó (số đo mới thắng):

```
TỔNG                        326  == population, UNEXPLAINED = 0
ADOPT (full contract)       240   proof = full_projection_contract_v1
ADOPT (curated)              27   proof = content_plus_curated_provenance_v1
EVENT_ONLY_SKIP              35   terminal SKIPPED, reason=historical_event_only,
                                  proof=substantive_gate_v1 — không memory, không ledger
TOMBSTONE_EXCLUDE             2   không outbox row; tombstone là authority;
                                  metric tách actionable_replay_debt=0 khỏi skipped_tombstoned
ALREADY_MANAGED              22   NO-OP
TRUE_MISSING / DIVERGENT / UNRECOVERABLE   0 / 0 / 0
```

## Thiết kế HBF-2 (chưa ký)

Mutate MỘT candidate offline, một transaction:

```
BEGIN
  outbox terminal (COMPLETED cho adopt / SKIPPED cho event-only,
                   worker_id='migration:hbf-adopt' — không giả worker thật)
  ledger adoption (target_id = memory hiện hữu; KHÔNG sửa cognitive_memories)
  projection_adoption_audit (bảng riêng — ledger tiếp tục là ledger):
      projection_key, event_id, target_id, origin=legacy_projection,
      management_action=adopted, adopted_at, migration_run_id,
      projection_hash, canonical_candidate_hash, equivalence_proof,
      contract_name, contract_version, builder_version_checked,
      source_snapshot_sha256
COMMIT
```

Candidate sau đó: integrity + fk + invariant logic + **replay rehearsal**
(không duplicate) + **forget rehearsal** (không resurrection) + restart.
Chỉ install_generation được đặt nó vào canonical path.

Bốn dao thử + hai mutant bắt buộc:
`ADOPT→REPLAY no-dup · ADOPT→FORGET no-resurrection · ADOPT→RESTART durable ·
MISSING_LEDGER=MISSING_PROJECTION MUST DIE ·
FULL_CONTRACT→CONTENT_ONLY equivalence mutant MUST DIE` (khoá học phí SP-0).
