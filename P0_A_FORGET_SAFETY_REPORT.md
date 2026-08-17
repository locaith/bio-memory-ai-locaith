# P0-A — QUEUED / IN-PROGRESS FORGET SAFETY

**VERIFIED.** Commit `99218ae`.

```
queued/in-progress forget safety   VERIFIED
global replay-safe forgetting      PARTIALLY VERIFIED
```

Hai dòng đó không được gộp. Cái thứ hai còn chờ `:335` và Projection Safety
Gate.

---

## 1. ROOT CAUSE — chứng minh bằng cả đọc lẫn chạy

`forget_derived` resolve từ **tầng phục vụ**: `_resolve` đọc
`cognitive_memories`, rồi `_event_ids_of` suy event *từ chính những memory đó*.
Một ký ức logic chỉ tồn tại dưới dạng việc xếp hàng không có dòng nào ở đó, nên
hàm **thoát sớm**:

```python
targets = _resolve(conn, memory_id, subject)
if not targets:
    report.note = "không tìm thấy ký ức nào khớp"
    return report
```

Ghi chú đó chính là bug phát biểu bằng tiếng Việt — *"không tìm thấy ký ức nào
khớp"*, về một ký ức mà worker sắp ghi ra.

Tái hiện tất định, trước khi sửa một dòng nào:

```
observe()          outbox pending,  memories 0,  tombstones 0
forget_derived()   verified_clean=False,  tombstones 0,  note="không tìm thấy"
worker.run_once()  completed=1,     memories 1        <- quay lại, nguyên văn
```

**Verifier đã nói thật suốt quá trình.** Nó báo `verified_clean=False` vì nội
dung vẫn tìm thấy được. Thứ thiếu không phải hiểu biết — mà là **tầm với**. Đó
là lý do bug này sống lâu mà không thành sự cố: hệ thống chưa bao giờ nói dối
về nó.

### Insight ở cấp trừu tượng

`forget` từng định nghĩa *"target tồn tại"* theo materialized memory. Nhưng một
**pending projection là future state đã được cam kết**, dù serving state chưa
tồn tại.

---

## 2. DANH TÍNH LOGIC — ánh xạ chính xác

Bất biến kiến trúc được khoá lại:

> **FORGET applies to logical memory identity, not only currently materialized
> representation.**

```
    event
    pending projection
    in-progress projection
    materialized memory
    replayed projection
```

là **năm representation của MỘT logical forget target**. Forget chỉ nhìn một
representation thì race sẽ quay lại dưới hình thức khác.

Khoá nối cả năm là `event_id`, và nó có mặt **trước**, **trong** và **sau** khi
materialize:

| tầng | mang `event_id` ở đâu |
|---|---|
| event log | `cognitive_events.event_id` |
| outbox job | `projection_outbox.event_id` |
| memory | `cognitive_memories.source_event_ids_json` |
| tombstone | `memory_tombstones.event_id`, `memory_id` **nullable** |

### Bia mộ đi trước ký ức

```
(event_id, memory_id = NULL)
```

Cột `memory_id` vốn nullable, nên bia mộ cho một ký ức **chưa từng tồn tại** là
hình dạng schema đã đỡ sẵn. Không phát minh gì, và **không có architecture
mismatch** — đây là chỗ chỉ thị yêu cầu dừng lại nếu có.

Ý nghĩa đáng ghi: trước đây tombstone là **hậu quả của một cái chết**; giờ nó
cũng là **một lệnh cấm sinh**.

### Vì sao không xoá dòng khỏi hàng đợi

Một job biến mất khỏi outbox là một quyết định **không audit nào nhìn thấy**, và
bất kỳ producer nào enqueue lại sẽ mang nội dung trở về. Bia mộ là sự thật bền,
và **cả hai chốt sẵn có** — tại enqueue và tại apply — đều đã biết đọc nó.

---

## 3. MA TRẬN NĂM CA — 10/10

`tests/test_queued_forget_safety.py`

| | ca | kết quả |
|---|---|---|
| **E** | control, không forget | materialize bình thường — **ma trận hợp lệ** |
| A | enqueue → forget → drain | không materialize |
| A′ | bia mộ mang `memory_id = NULL` | xác nhận |
| B | forget → replay/enqueue | không materialize |
| C | worker giữ lease → forget → chạy tiếp | chốt trước-khi-ghi chặn |
| D | hai job trùng → forget | không cái nào materialize |

Chứng **E là bắt buộc**: một ma trận an toàn mà đường bình thường cũng không
chạy được thì mọi ô đều xanh vì cùng một lý do sai. `_assert_probe_valid` chặn
điều kiện tiên quyết ở mọi ca.

Cộng hai ca giữ nửa phải: memory đã materialize vẫn bị chạm; store không có hàng
đợi không đổi hành vi.

### Hai mutant — cả hai THỰC SỰ đi qua đường nguy hiểm

```
IGNORE_RECONCILIATION_WORKER_BURIED_CHECK  -> hồi sinh nội dung, test ĐỎ
FORGET_IGNORES_PENDING_PROJECTION          -> hồi sinh nội dung, test ĐỎ
```

Nếu mutant không hồi sinh được gì thì bốn ca kia chưa chạm tới cơ chế chúng định
đo, và `resurrection = 0` chưa chứng minh gì.

```
queued_forget_resurrection_rate        0
in_progress_forget_resurrection_rate   0
duplicate_projection_resurrection_rate 0
control_materialization_rate           1
```

---

## 4. FULL SUITE + GATE

```
full suite   1196 passed, 3 skipped, 5 deselected, 11 xfailed

Gate, baseline B2 @ e10e376, candidate 99218ae, dirty=False
    NO_DELTA 134   UNEXPECTED_COUPLING 2
    ever 40→40   current 40→40   historical 31→32   forgotten 16→17
```

Hai câu đổi là **của #7**, đã được giải thích nhân quả ở vòng trước. **P0-A tạo
0 delta Lifetime** — safety semantics không được phép mua điểm, và nó không mua.

---

## 5. STORE THẬT KHÔNG BỊ ĐỤNG — đo bằng hash

```
sha256              b0ed1a9ca3b1fc665b3f77639f9b56de…
bytes               2 846 720
projection_ledger   KHÔNG CÓ        <- chưa worker nào từng được dựng ở đây
projection_outbox   0
memory_tombstones   KHÔNG CÓ BẢNG   <- chưa lệnh quên nào từng chạy ở đây
cognitive_events    236
cognitive_memories  209
```

Containment nguyên vẹn. Không tạo `projection_ledger`, không chạy worker, không
đổi khỏi `legacy`, không drain/tạo outbox.

---

## 6. BA LỖI CỦA CHÍNH TÔI — ghi vào comment tại chỗ, không chỉ ở đây

1. **Lần tái hiện đầu báo "không tái hiện được".** Tôi gọi `remember()`, nó
   materialize ngay (double-write), nên `forget` thấy memory và worker từ chối
   đúng. Dừng ở đó là kết luận ngược hoàn toàn.
2. **Bản vá đầu đặt SAU chỗ thoát sớm ba chục dòng** — chạy mà vẫn đỏ.
3. **Đọc `cognitive_events` qua connection của memories** (nó nằm trên conn
   riêng). Trả về rỗng, và **trông y hệt "không event nào khớp"**.

Cùng một họ:

> **An empty result is not evidence of absence until the observation path
> itself is proven valid.**

Đây không phải luật thứ bảy — nó là **dạng tổng quát của luật số 1**
(`NO EXECUTION != ZERO PERFORMANCE`), nói ở tầng quan sát thay vì tầng thực thi.

---

## 7. GIỚI HẠN CÒN LẠI — chưa được tuyên bố "replay safe"

**`reconciliation_worker.py:335`** vẫn hỏng, và nó hiện ra ngay trong trace của
P0-A: job ghim `in_progress` mãi sau khi bị chặn. `outbox.skip(job.job_id,
"<lý do>")` gọi vào chữ ký `skip(event_id, projection_type, *, reason)` → khớp 0
dòng. Hệ quả: `drain()` không bao giờ hoàn tất trên store đã từng xoá gì, và
`ROLLBACK_RUNBOOK.md:71` dùng drain-về-0 làm cổng hoàn tất.

**Chưa chạm:** forget after materialization + rebuild, worker crash/restart,
buried terminal state, rollback drain-to-zero end-to-end.

Nên:

```
queued/in-progress forget safety   VERIFIED
global replay-safe forgetting      PARTIALLY VERIFIED
```

**Không được viết "Bio-Memory replay safe ✓".**

---

## 8. VIỆC TIẾP THEO

1. `:335` — **commit riêng**, mini-matrix, mutant `SKIP_USING_JOB_ID`, và chạy
   **đúng completion check của `ROLLBACK_RUNBOOK.md`**, không chỉ unit-test
   `skip()`.
2. Đo lại **#7 Door 5** bằng worker thật (B2 vs C1) — giờ đường nguy hiểm chạy
   được nên nó có thể chuyển từ `NOT_EVALUABLE` thành kết luận thật.
3. Projection Safety Gate đầy đủ 11 ca.
4. Chỉ sau đó mới có **Projection Worker Activation Gate** cho store thật.
