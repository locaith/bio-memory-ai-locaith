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
                   locked_by='migration:hbf-adopt' — projection_outbox KHÔNG có cột
                   worker_id, chỉ có locked_by; worker_id nằm ở ledger.
                   Sửa 19/08 sau khi chủ bắt lỗi schema trong plan)
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

Bốn dao thử (K1–K4) + kiểm admissibility + hai mutant:

```
K1  ADOPT → REPLAY      không duplicate
K2  ADOPT → FORGET      không resurrection
K3  ADOPT → RESTART     managed state bền
K4  ADOPT → REAPPLY     idempotent — chạy migration LẦN HAI trên candidate
                        đã adopt: 0 thay đổi. "Lễ nhập tịch chạy lần hai mà
                        cấp thêm hộ chiếu thì là máy photocopy."

ADMISSIBILITY (tách khỏi K1–K4): inject failure TRƯỚC COMMIT của transaction
adoption → mở lại candidate → ZERO partial state (không outbox terminal mồ
côi, không ledger thiếu audit, không audit thiếu ledger).

M1  MISSING_LEDGER = MISSING_PROJECTION           MUST DIE
M2  FULL_CONTRACT → CONTENT_ONLY (đổi một trường comparator FULL thật sự
    kiểm, content giữ nguyên)                     MUST DIE
```

## HBF-1.1 — số đo cuối (comparator FULL, epsilon theo cơ chế)

Bản comparator đầu tự gọi FULL nhưng đo 6 trường — chủ bắt đúng họ SP-0.
Bản FULL thật (≥20 trường + kiểm kê tái-tạo-được tường minh) đổi con số:

```
ADOPT_FULL_CONTRACT          203   full_projection_contract_v1
ADOPT_HISTORICAL_PARTIAL      37   full_contract_except_observed_at_epsilon_v1
ADOPT_CURATED_PRESERVED       18   content_plus_curated_provenance_v1
ADOPT_CURATED_PARTIAL          9   ..._except_observed_at_v1
EVENT_ONLY_SKIP               35 · TOMBSTONE_EXCLUDE 2 · ALREADY_MANAGED 24
TRUE_MISSING/DIVERGENT/UNRECOVERABLE/UNEXPLAINED   0/0/0/0   (tổng 328)
```

Partial class sinh từ CƠ CHẾ đo được: đường ghi trước khi `observed_at`
inheritance được thêm vào `remember()` đóng dấu đồng hồ riêng — drift luôn
DƯƠNG, 0.51–53.86ms; epsilon = 100ms đặt theo cơ chế cùng-lời-gọi (thang
staleness tính bằng ngày), KHÔNG phải số làm đẹp count. "240 FULL" cũ là
claim mạnh hơn measurement — con số trung thực là 203.

## HBF-2 — KẾT QUẢ DIỄN TẬP (19/08, VERIFIED OFFLINE)

Chữ ký HBF-2 mang hai ràng buộc, cả hai đã khắc vào code:

1. **Epsilon là admissibility policy ĐÃ ĐO trên population này** (0.505–53.861ms,
   luôn dương), không phải định luật "same call luôn <100ms" — OS scheduling
   không cho guarantee đó. `DriftPolicy` mang đúng docstring này; audit giữ
   `observed_at_delta_ms` thật từng hàng; proof giữ chữ `except_observed_at...`;
   không có đường promote lên FULL.
2. **structured_content phải đóng TRƯỚC transaction**: đo bằng slot extractor
   tất định — kết quả thật: **240/240 hook rows `reconstructed_equal`**, không
   hàng nào phải hạ proof. Không tái tạo được thì proof HẠ
   (`..._except_structured_content_v1`), không ép count; chưa đo thì CỔNG ĐÓNG.

Nuance đã ký: **adopt AS-IS** — không sửa observed_at của 46 hàng drift
("chúng ta hiểu vì sao khác" ≠ "chúng ta được phép rewrite quá khứ");
27 curated giữ `canonical_candidate_hash = NULL` — trung thực hơn một hash
đẹp nhưng vô nghĩa, vì chính bàn tay tác giả là contract.

```
population tươi (frozen c46f3129…)   331 events
  ADOPT 203 + 37 + 18 + 9 = 267  ·  EVENT_ONLY 36  ·  TOMBSTONE 2
  ALREADY_MANAGED 26  ·  lớp chặn 0/0/0/0/0
  (delta vs HBF-1.1: +1 event-only, +2 managed — event mới của chính phiên)

transaction    267 adopt (outbox terminal locked_by='migration:hbf-adopt'
               + ledger + audit), 36 skip, 2 excluded-audit, 303 outbox,
               267 ledger, 305 audit — semantic delta 0 (digest bằng)
abort test     inject trước COMMIT → ZERO partial state
K1 replay      không duplicate; ledger authority giữ khi outbox row mất
K2 forget      không resurrection sau replay+drain
K3 restart     managed state bền qua process mới
K4 reapply     lần hai + classify tươi: 0 thay đổi, digest bằng
M1             DIES — TRUE_MISSING=267 chặn cổng; witness: xoá ledger 1 hàng
               → invariant bắt tại cửa sổ phát hiện; phớt lờ → duplicate=2
M2             DIES — đổi trust_tier, content nguyên → DIVERGENT chặn cổng
install        candidate → DISPOSABLE canonical (không phải real DB):
               INSTALL_CONFIRMED, fresh process: integrity ok, fk 0,
               actionable replay debt 0, sha khớp manifest, không stale -wal
ghi chú đo     2 event bia mộ đã có outbox completed từ trước khi bị quên
               → skipped_tombstoned=0 là số đúng; hình dạng bia-mộ-không-
               outbox được dao synthetic phủ (tests/test_historical_adoption)

REAL STORE MIGRATION          NOT PERFORMED
HBF-3 REAL ADOPTION           LOCKED — chờ chữ ký
```

Code: `bio_agent_os/cognitive/historical_adoption.py` (classify + closure +
transaction + invariants) · `tests/test_historical_adoption.py` (10 dao/mutant
trên store synthetic dựng bằng chính đường ghi legacy) ·
`activation/HBF2_rehearsal.py` + `activation/HBF2/hbf2_report.json`.

## HBF-2.1 — CORRECTION PROVENANCE (19/08, chủ bắt trên commit 7c3ee9f)

Hai vết provenance/schema nằm ngay trong transaction adoption của HBF-2 —
behavioral VERIFIED nhưng provenance conformance FAIL, không được qua cửa
store thật:

1. **`locked_by` bị dùng làm provenance.** Bản HBF-2 đầu insert terminal rows
   với `locked_by='migration:hbf-adopt'` — biến field có nghĩa LEASE OWNERSHIP
   thành túi đựng migration provenance, trái với terminal shape của production
   `complete()`/`skip()` (clear cả locked_by lẫn locked_at). 303 "worker ma"
   sẽ sống vĩnh viễn trong mọi query ownership. Sửa: terminal rows mang
   locked_by=NULL, locked_at=NULL; provenance ở ĐÚNG NHÀ — `ledger.worker_id`
   + bảng audit; invariant nhận diện migration rows qua AUDIT JOIN.
2. **Audit điền cột NULL-able chỉ vì có sẵn số nguyên.** `builder_version_checked=1`
   được ghi cho CẢ curated/tombstone/event-only — audit tương lai sẽ nói dối
   "curated này đã kiểm với builder v1" về một comparison chưa từng xảy ra.
   Sửa: hook adopt giữ claim thật (contract v1, builder=1); curated =
   contract curated_seed_v1 v1 + builder NULL; tombstone/event-only = cả ba
   NULL. **NOT_APPLICABLE phải phân biệt được với version 1.**

Hai mutant provenance mới, cả hai regression-locked trong
`tests/test_historical_adoption.py` (13 dao):

```
P1  khôi phục locked_by trên terminal rows
    → terminal_migration_rows_with_lock = 307 > 0        DIES
      (305 hàng migration đặt + 2 outbox completed có sẵn của event bia mộ
       — cũng nằm dưới audit governance nên cùng bị soi)
P2  curated claim builder_version_checked=1
    → curated_builder_version_checked_nonnull = 27 > 0   DIES
```

Rerun đúp toàn bộ rehearsal trên snapshot TƯƠI (sha 215432f2…, số mới thắng):

```
population 335 · adopt 203/37/18/9 = 267 (đóng băng đúng như HBF-1.1)
EVENT_ONLY 38 (+3) · MANAGED 28 (+4) — event mới của chính các phiên
closure 240/240 reconstructed_equal · abort ZERO PARTIAL
K1–K4 PASS · M1/M2/P1/P2 DIES · semantic delta 0
audit provenance by class:
  adopted/hook_call_site_v1      240   cv=1  builder=1
  adopted/curated_seed_v1         27   cv=1  builder=NULL
  skipped_event_only              38   NULL/NULL/NULL
  excluded_tombstoned              2   NULL/NULL/NULL
terminal_migration_rows_with_lock = 0
install → disposable: INSTALL_CONFIRMED, fresh process sạch toàn phần

REAL STORE MIGRATION          NOT PERFORMED
HBF-3 REAL ADOPTION           BLOCKED → chờ ký sau HBF-2.1 PASS
```

Ghi chú thiết kế HBF-3 (chủ đổi ý, 19/08): KHÔNG blast-radius ladder bằng
bốn lần install_generation vào canonical thật. Migration này không
materialize memory, không sửa semantic rows — HBF-2 đã chứng minh full
population trên clone thật, nên mỗi lần replace generation thêm là thêm
operational risk mà KHÔNG mua thêm causal information
(`measurement_delta != product_delta`). HBF-3 sẽ là: fresh snapshot →
classify + candidate OFFLINE → sampled pre-install audit 5→20→50 trên
candidate (KHÔNG install) → full certification → MỘT lần quiesced
install_generation → fresh-process certification → canary production-hook nhỏ.
