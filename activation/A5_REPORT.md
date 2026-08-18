# A5 — STAGED NEW-WRITE ACTIVATION: BÁO CÁO

```
A5 NEW-WRITE ACTIVATION     FAIL — dừng đúng stop-condition 3, đã rollback sạch
──────────────────────────────────────────────────────────────
projection mode hiện hành   LEGACY (env đã gỡ ngay khi phát hiện)
new writes processed        A5.1-3: requested 75 / attempted 75 / confirmed 75
                            A5.4 window: requested 3 / materialized 6  ← LỖI
lost jobs                   0
duplicate materializations  3 (mỗi prompt qua hook thành HAI memory)
retry/unresolved            0
forget operations           3 stage-forget, mỗi cái ForgetReport tombstoned=1
tombstones confirmed        có, đọc từ ForgetReport + đếm bảng
unrelated pre-existing      A5.1-3: 0. A5.4 window: 5 hàng đổi (chưa phân loại
                            xong — nghi access-stats do recall; đã rollback)
historical jobs enqueued    0 — interlock xanh ở mọi stage
rollback status             ĐÃ THỰC THI, store khớp byte snapshot pre-A5,
                            0 residue; env legacy; store về đúng trạng thái trước A5
worker topology             single host, single worker, common clock
clock-domain constraint     giữ
```

## Phát hiện chính — vì sao A5.4 fail trong khi A5.1–A5.3 xanh

**A5.1–A5.3 đo sai population.** Các stage gọi `observe()` trực tiếp — đường
đó sạch tuyệt đối (75/75, exactly-once, tombstone chuẩn). Nhưng production
không đi đường đó: hook đi qua `ClaudeCodeHookAdapter.ingest`, và `ingest` gọi
**cả hai** `observe()` (hooks.py:71) lẫn `remember()` (hooks.py:101).

Hình dạng hai-lời-gọi này có trước outbox, và trong legacy nó đúng:
`observe` ghi event, `remember` ghi memory — một bản. Trong outbox mode, nhánh
`observe` mọc thêm một projection job → worker build bản #1; `remember` ghi
thẳng bản #2. **Mỗi prompt thành hai memory giống hệt nhau**, qua đúng lệnh
production, tái hiện 3/3.

Ledger không cứu được: bản ghi thẳng của `remember()` không đi qua ledger, nên
dedup theo projection key không nhìn thấy nó.

## Phân loại

**ADAPTER/FACADE INTEGRATION BUG dưới outbox mode** — không phải lỗi của
outbox runtime (75/75 sạch qua `observe()`), không phải lỗi của adapter trong
legacy (đường đang chạy hằng ngày vẫn đúng). Lỗi nằm ở **hợp đồng giữa hai
tầng khi đổi mode**: adapter viết cho thời legacy, chưa outbox-aware.

Ba hướng vá khả dĩ, **chưa chọn — quyết định của chủ**:
- a. dưới outbox, adapter chỉ `observe()` và để worker build (remember bỏ nhánh ghi thẳng)
- b. `remember()` ghi thẳng thì đồng thời ghi ledger, để job về sau thành ALREADY_BUILT
- c. hook giữ legacy vĩnh viễn; outbox chỉ dành cho đường API mới

## Việc đã làm đúng directive

- STOP ngay tại stop-condition 3, không vá speculative
- env gỡ trước tiên — phiên thật không bao giờ chạy outbox với bug này
- rollback về snapshot pre-A5, khớp byte, 0 residue
- mọi write giữa snapshot và rollback đều là dữ liệu test có kiểm soát
  (không prompt thật nào của chủ rơi vào khoảng đó)

## Ghi chú phụ (không hành động)

- `hook_cli` giờ có bounded drain khi mode ≠ legacy — bất hoạt dưới legacy,
  cần cho mọi lần thử A5 sau; đã kèm guard không cho worker hỏng phá phiên.
- Pre-A5 snapshot cho thấy store có 296 events / 223 memories so với 292/264
  lúc A1 — chênh lệch nằm TRƯỚC A5 (trong snapshot), nhiều khả năng là
  consolidation của hook giữa các phiên; ngoài phạm vi A5, ghi lại để ai đó
  nhìn khi rảnh.

## Trạng thái

```
Projection runtime (đường observe/API)   hoạt động đúng trên store thật, 75/75
NEW-WRITE ACTIVATION qua hook            NOT ACTIVATED — chờ chủ chọn hướng vá
HISTORICAL BACKFILL                      NOT ACTIVATED — gate riêng
store thật                               đúng trạng thái pre-A5, legacy mode
```
