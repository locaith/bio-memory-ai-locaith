# PROJECTION SAFETY GATE v1 → v1.1

```
v1     PASS 9/11   INVALID 2/11   FAIL 0/11
v1.1   PASS 11/11  INVALID 0/11   FAIL 0/11

false_success_report_rate            0
false-success mutant bị bắt          VERIFIED
```

**v1 giữ nguyên trong tài liệu này, không sửa ngược.** 9/11 là một checkpoint
khoa học có giá trị riêng: nó ghi lại rằng cổng đã từ chối cấp PASS cho chính
người viết ra nó, và lý do từ chối là đúng.

## v1.1 — hai ca INVALID trở thành PASS, và vì sao

Điều tra lifecycle của job đã claim (`claimed-job-lifecycle`, 6 agent) chứng
minh **kết cục A**: reclaim luôn tồn tại và luôn hoạt động. **Không có bug sản
phẩm.** Thứ hỏng là phép đo.

```sql
-- outbox.py:209-223, và guard lặp lại trong UPDATE ở :226-237
stale_before = now - lease_seconds
WHERE available_at <= now
  AND (status='pending'
       OR (status='in_progress' AND (locked_at IS NULL OR locked_at <= stale_before)))
```

**Không có cột hạn lease.** `locked_at` lưu thời điểm *bắt đầu*; hạn không bao
giờ được ghi xuống. `claim()` tính lại `stale_before` mỗi lần gọi.

> **`lease_seconds` là thuộc tính của NGƯỜI ĐỌC, không phải của job.**

Fixture v1 truyền `lease_seconds=0` vào lệnh **lấy** lease — nơi nó không có tác
dụng gì lên hàng, readback giống hệt từng byte — rồi drain bằng worker mặc định
300 giây. `locked_at <= now-300` sai, hàng vô hình.

```
fixture v1     drain lease 300  →  claimed=0 completed=0 ×5, hàng đóng băng
fixture v1.1   drain lease 0    →  claimed=1 completed=1, hàng completed
```

Và câu comment trong fixture v1 — *"lease hết hạn ngay nên vòng sau claim lại
được"* — chính là niềm tin đã làm phép đo mù hai lần liên tiếp.

**Lease không tự hết hạn.** Không có gì chạy vào lúc hết hạn; hàng không đổi.
Hết hạn chỉ tồn tại trong mắt người claim tiếp theo.

Sửa fixture **không phải làm yếu cổng**: nó làm đường nguy hiểm **chạm tới
được**, đúng thứ cổng đòi hỏi. Bằng chứng: `state == PASS` đòi
`MUTANT_TRIGGERED_PROHIBITED_STATE`, và mutant giờ thực sự hồi sinh nội dung ở
cả hai ca.

Bốn kết cục còn lại đều bị bác bằng đo đạc: **B** không có thao tác production
thứ hai; **C** job đã claim rõ ràng reclaim được; **D** restart recovery hoàn
chỉnh — `ProjectionOutbox` mới trên connection mới thu hồi được hàng của
`dead-worker`; **E** `stale()` chỉ đọc, không đổi gì.

**Không có điểm tổng.** Cổng này báo từng năng lực, và ba trong bốn nhãn không
phải "hỏng".

---

## 1. MƯỜI MỘT NĂNG LỰC

| | ca | trạng thái |
|---|---|---|
| 01 | forget trước khi enqueue | **PASS** |
| 02 | enqueue rồi mới forget | **PASS** |
| 03 | worker đang giữ lease thì forget | v1 **INVALID** → v1.1 **PASS** |
| 04 | materialize rồi mới forget | **PASS** |
| 05 | forget rồi replay | **PASS** |
| 06 | forget rồi dựng lại nhiều vòng | **PASS** |
| 07 | hai job trùng cho cùng một sự kiện | **PASS** |
| 08 | worker thử lại nhiều vòng | **PASS** |
| 09 | worker chết khi giữ lease rồi khởi động lại | v1 **INVALID** → v1.1 **PASS** |
| 10 | job bị bia mộ → trạng thái kết thúc | **PASS** |
| 11 | rollback drain-to-zero | **PASS** |

Mỗi ca PASS đòi **cả bốn** điều kiện:

```
CONTROL_EXECUTED                   đường bình thường có chạy
DANGEROUS_PATH_EXECUTED            kịch bản nguy hiểm thật sự diễn ra
MUTANT_TRIGGERED_PROHIBITED_STATE  mutant tạo được trạng thái bị cấm
CLEAN_PREVENTED_PROHIBITED_STATE   bản sạch ngăn được nó
```

Mỗi ca chạy trên **ba store riêng** — control, bản sạch, mutant — vì một store
đã bị mutant chạm không còn nói được gì về bản sạch.

Trạng thái bị cấm phát biểu bằng **persistent state**, đọc lại từ store, không
phải bằng `expected=True/False`. Control flow kể chuyện; persistent state ký
biên bản.

---

## 2. HAI CA TỪNG INVALID — hồ sơ, giữ nguyên

Ghi lại vì đây là lần cổng từ chối cấp PASS cho chính người viết ra nó, và lý do
từ chối đã đúng.

**v1: 03 và 09 INVALID vì mutant không chạm tới được trạng thái bị cấm.** Đo
được, năm lần worker chạy:

```
claim -> 1 job
vòng 1..5:  tombstoned=0   completed=0   holding=0
```

Worker **không xử lý job đã bị claim lần nào**. Nó không từ chối job — nó không
bao giờ nhận job đó. **Không được viết "worker safely refuses".**

Lúc đó cả hai được đọc là: *hành vi an toàn có bằng chứng ở P0-A, nhưng cổng
chưa có mutation witness cho chính execution shape đó.* Không phải bug sản phẩm,
không phải PASS, không phải FAIL.

**v1.1: đúng như thế thật** — không phải bug sản phẩm, mà là phép đo hỏng. Xem
phần đầu tài liệu này. Tripwire cũ (assert INVALID) đã hoàn thành việc của nó và
được thay bằng assert PASS.

## 3. BẤT BIẾN CẤP CỔNG — `false_success_report_rate`

Lớp lỗi `:335`, đo thành một tỉ lệ:

```
reported_success_without_confirmed_transition / reported_success_attempts
target: 0        đo được: 0
```

`requested` / `attempted` / `confirmed` là **ba quan sát khác nhau**, và
`confirmed` chỉ được tính khi **đọc lại được từ store**. Một hàm trả về truthy
là `attempted`, không phải `confirmed`.

Mutant `IGNORE_CALLEE_FALSE_AND_INCREMENT_SUCCESS_METRIC` tái tạo đúng chuỗi đã
làm `:335` sống lâu:

```
callee báo FAIL → caller bỏ qua → caller tăng metric thành công
                → hệ thống báo thành công
```

Sổ bắt được nó. Nếu cổng vẫn xanh dưới mutant này thì tầng metric vẫn mù.

---

## 4. ARTIFACT IDENTITY

`bio_agent_os/evals/projection_safety_gate.py` — `GateArtifact.identity` gồm
`gate_version`, `git_sha`, `git_tree_hash`, `git_dirty`, `runtime_fingerprint`,
`schema_version`, `worker_version`, `fixture_hash`, `mutant_set_hash`,
`case_ids`.

Lệch identity → `INVALID`, **không cảnh báo**. Một cảnh báo in phía trên một kết
luận an toàn sẽ được đọc như chú thích cho một kết luận vốn đã được tin.

Artifact bất biến: `write()` ném khi file đã tồn tại. Một cổng an toàn bị ghi đè
là một cổng mất lịch sử, và lịch sử là phần chứng minh được điều gì đã đổi.

---

## 5. STORE THẬT — không chạm

Bất biến containment, theo **subsystem**, không theo hash toàn file:

```
projection_ledger   KHÔNG TỒN TẠI
memory_tombstones   KHÔNG TỒN TẠI
projection_outbox   0 dòng
```

`cognitive_events` và `cognitive_memories` **được hook phiên làm việc ghi hợp
lệ**, nên không được dùng làm bằng chứng containment. Hash toàn DB trả lời "file
có đổi byte không"; câu cần trả lời là "worker đã từng chạy ở đây chưa".

---

## 5b. HAI HIỂM HOẠ NGOÀI 11 CA — chưa sửa

Điều tra lifecycle moi ra hai thứ mà **mười một ca không phủ**, vì chúng thuộc
trục khác. Cả hai đo được, chưa đụng.

**H1 — `claim()` tăng `attempts` nhưng không bao giờ đọc `max_attempts`.**
Tám lần thu hồi liên tiếp đẩy `attempts` lên 8 với `DEFAULT_MAX_ATTEMPTS = 5`,
hàng vẫn `in_progress`. Chỉ `fail()` mới đưa job vào dead-letter. Một worker
chết **im lặng** mỗi lần — không bao giờ tới `fail()` — sẽ được thu hồi vĩnh
viễn và không bao giờ dừng. Đây là **liveness**, không phải resurrection.

**H2 — `run_once` lọc tenant SAU khi claim** (`reconciliation_worker.py:502-508`).
`claim()` sửa hàng, rồi `jobs = [j for j in jobs if j.tenant_id == …]` vứt nó
đi, và `metrics.claimed` đếm danh sách **sau** khi lọc. Đo được: worker giới hạn
tenant báo `claimed=0 completed=0` mỗi vòng trong khi readback cho thấy
`locked_by` bị chiếm và `attempts` leo 2→3→4.

H2 là `REPORTED SUCCESS != CONFIRMED TRANSITION` **lần thứ hai trong cùng một
file**, và lần này kèm **chiếm khoá xuyên tenant** — mà cô lập tenant là một cam
kết nền. Đây là **isolation**, không phải resurrection.

Ngoài ra: `stale()` (outbox.py:341) dùng `locked_at <= ?` trần, nên hàng
`in_progress` có `locked_at IS NULL` **vô hình với `stale()` nhưng `claim()` vẫn
lấy**. Hai hàm bất đồng về định nghĩa "bỏ rơi".

## 6. TRẠNG THÁI GLOBAL — chưa đổi

```
Queued/in-progress forget safety      VERIFIED
Buried worker enforcement             VERIFIED + mutation-sensitive
Buried terminal transition / drain    VERIFIED
Rollback drain-to-zero                VERIFIED
#7 Door 5                             PASS (non-regression)
Global replay-safe forgetting         xem ba dòng bên dưới
Projection worker on real store       NOT ACTIVATED
Real-store containment                VERIFIED by subsystem invariants
```

**Vẫn chưa ghi chữ VERIFIED**, dù cổng đã 11/11.

Mười một ca đo **resurrection**: một ký ức đã quên có quay lại không. Chúng giờ
đều có nhân chứng thật. Nhưng H1 là **liveness** và H2 là **isolation** — ba
trục khác nhau, và gộp chúng lại để lấy một chữ VERIFIED là đúng kiểu sai mà
cổng này được dựng lên để chặn.

Phát biểu chính xác:

```
replay-safe forgetting (resurrection)   VERIFIED   11/11 có mutation witness
projection queue liveness               OPEN       H1
projection tenant isolation             OPEN       H2
```

Ba dòng đó cộng lại **không** thành "replay safe ✓". Chúng thành "chưa ai chứng
minh được một ký ức đã quên quay lại, và còn hai câu hỏi khác chưa trả lời".

---

## 7. VIỆC TIẾP THEO — điều tra, không vá

Câu hỏi **không phải** "làm sao cho test xanh", mà là:

> Sau khi một job đã được claim, lifecycle thật của lease/retry/reclaim là gì?

```
pending → claimed / in_progress → lease expiry? → retry eligibility?
        → reclaim? → terminal?
```

Năm kết cục có thể, chưa cái nào được chứng minh:

```
A  reclaim tồn tại nhưng fixture gọi sai
B  reclaim cần một thao tác production khác
C  job đã claim CỐ Ý không re-claimable
D  worker restart recovery chưa hoàn chỉnh
E  một cơ chế khác sở hữu việc thu hồi job bị bỏ rơi
```

**Không vá cho tới khi một trong năm được chứng minh.** Và nếu ngữ nghĩa
production làm đường nguy hiểm thực sự không chạm tới được, giữ nguyên
`INVALID`/`NOT_EVALUABLE` và ghi rõ vì sao — đừng làm yếu cổng để lấy PASS.

---

## 8. GHI CHÚ VỀ QUÁ TRÌNH

Tôi đoán sai hai lần liên tiếp ở ca 03 — `lease_seconds=0` rồi lại `lease_seconds=0`
— trước khi chịu in `readback` ra. `readback` là thứ tôi đã dựng sẵn vào
`CaseResult` chính để không phải đoán.

Đó là dấu hiệu đã đến lúc đóng sổ phiên, không phải cố thêm một test nữa.
