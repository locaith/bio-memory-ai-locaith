# RC-0 — REPLAY INTENT CLOSURE

*19/08/2026. Lane cuối trước khi Core Integrity (single host) được đóng.*

```
WRITE-TIME DECISION
        ↓
must become durable evidence
        ↓
REPLAY MAY RECOVER IT
        ↓
REPLAY MAY NOT REINTERPRET IT
```

Nếu ingest đã quyết định "đây không phải memory", replay không được vài tuần
sau nhìn một hàng vắng rồi tự phán "chắc ai quên build". Đó là khác biệt giữa
**recovery** và **viết lại lịch sử**.

## Hai luật

```
ABSENCE OF OUTBOX != EVIDENCE THAT PROJECTION IS OWED
INGEST-TIME NO-PROJECTION DECISION MUST SURVIVE REPLAY AND VERSION REBUILD
```

## Bệnh

Substantive gate cắt đúng ở đường ghi: hook non-substantive → `observe()` không
enqueue → không memory. Nhưng nó thể hiện quyết định bằng **sự vắng mặt**, và
replay đọc sự vắng mặt thành "còn nợ":

```
event + không outbox row → MISSING_OUTBOX → enqueue → worker dựng ký ức
```

Nên `replay --repair` materialize `hook=SessionStart` thành ký ức — đúng cái
đầu mà gate đã cắt. Con rồng không mọc lại bằng đường ghi; nó mọc bằng **đường
phục hồi**.

Chủ bắt thêm tầng thứ hai: `rebuild_version` chỉ coi `SKIPPED` là terminal khi
nó nằm đúng version hiện tại. Bump v1→v2 là hàng cũ hết khớp và engine dựng
candidate v2 cho chính cái marker vừa từ chối — quả trứng thứ hai, nằm dưới
rebuild chứ không nằm ở replay.

## Thuốc

**Hai lớp bằng chứng độc lập**, không phải một:

1. **Payload bất biến** (`projection_intents.cognitive_memory.decision =
   "no_projection"`, dưới checksum) — sống sót cả khi bảng outbox mất sạch.
2. **Hàng terminal `SKIPPED`** ghi trong **cùng transaction với event**
   (`event_store.append(skip_types=...)`) — "không nợ gì" là một sự thật được
   ghi, đúng docstring của `JobStatus.SKIPPED`.

Replay đọc bằng chứng, không đọc sự vắng mặt:

```
không có outbox row
   ├── decision = no_projection → SKIP_ROW_LOST  → dựng lại QUYẾT ĐỊNH
   ├── intent đã ghi            → MISSING_OUTBOX → còn nợ thật, enqueue
   └── không ghi gì             → UNKNOWN_INTENT → báo cáo, KHÔNG hành động
```

`rebuild_version` bỏ qua event mang quyết định no-projection, và coi `SKIPPED`
ở **bất kỳ** version nào là terminal: "không đáng thành ký ức" là mệnh đề về
CHÍNH EVENT, không phải về phiên bản logic chiếu. Nó không hết hạn khi builder
lên đời.

## Đo

```
R1  bỏ hàng skip bền              → quyết định trong payload vẫn cứu  CHẾT
R2  đọc sự vắng mặt thành "nợ"    → marker bị materialize             CHẾT
R3  bỏ qua skip khi bump version  → sinh job cho marker               CHẾT
    (tách hai cổng: xoá hàng SKIPPED một mình KHÔNG đủ để hở)
R4  UNKNOWN coi như OWED          → tự dựng từ event chưa ai nói gì   CHẾT
R5  đoán skip theo HÌNH DẠNG CHỮ  → dán "không nợ" lên hàng CÓ ký ức  CHẾT
atomicity   giết process tại after_event_insert / after_outbox_insert
            → chỉ (0 event, 0 quyết định) hoặc (1, 1); không có trạng thái lai
```

Chứng đối đi kèm mỗi dao: đường ghi thật vẫn nợ và vẫn được dựng
(`test_substantive_event_still_owes_and_still_gets_built`), nếu không thì
"marker không được dựng" chỉ chứng minh hệ đã chết.

## Hệ quả ngoài dự kiến — một xfail hết hạn

`tests/test_forget_resurrection.py` có hai test mang `xfail(strict=True)` với
lý do "không có consumer nào vật chất hoá outbox". RC-0 làm chúng XPASS, và
đúng như chính chú thích đó tự hẹn, chúng được viết lại thành phép đo thật:
replay **rồi drain bằng worker của sản phẩm**. Đo được thêm một tính chất:
**bia mộ có HAI cổng độc lập** — tắt cổng ở `projection_engine` thì
`ReconciliationWorker.process` vẫn từ chối dựng. Mutant phải tắt cả hai mới
hồi sinh được, và điều đó giờ là một test riêng.

## Store thật

```
marker cũ (trước RC-0)     5 event → UNKNOWN_INTENT, actionable = 0
                           KHÔNG backfill thành SKIPPED — làm vậy chính là
                           đoán theo hình dạng chữ, đúng mutant R5
historical actionable debt 0
live marker debt           0
migration invariants       9/9 = 0 · closure 307/307 · integrity ok
```

## Re-qualification — sáu test chạy trong một miền đã hết hiệu lực

Full suite sau RC-0 đỏ 6 ca trong `tests/test_projection_engine.py`. Không ca
nào là hồi quy sản phẩm: cả sáu **mã hoá chính luật cũ**, và một trong số đó
mang luật ấy ngay trong tên — `test_an_event_with_no_outbox_row_is_reported_as_owed`.

Điều đáng nói: docstring của chính module đó mở đầu bằng *"what is owed, in
what order, **and never guessed**"*, còn docstring của engine viết *"An event
with no outbox row may be an orphan or a deliberate `observe()` with no
projection. Both are reported as what they are."* Ý định đã đúng từ đầu; phần
cài đặt mới là chỗ đoán. RC-0 làm code khớp với câu mà nó vốn đã tự tuyên bố.

Migrate **theo ý định**, không search-replace:

```
1 ca  luật cũ nằm trong tên      → viết lại theo luật mới, đổi tên
                                   (nửa ý định "phải được BÁO CÁO" giữ nguyên,
                                    chỉ nhãn đổi thành UNKNOWN + non-actionable)
5 ca  ý định là scoping/idempotency/enqueue
                                 → dùng `_owed_event()` có ghi ý định thật,
                                   vì "còn nợ" giờ đọc từ bằng chứng
```

Số xfail của suite giảm 10 → 8: hai ca `xfail(strict=True)` trong
`test_forget_resurrection.py` đã hết tiền đề và được viết lại thành phép đo
thật (xem mục trên).
