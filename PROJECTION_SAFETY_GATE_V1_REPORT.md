# PROJECTION SAFETY GATE v1

```
PASS      9/11
INVALID   2/11
FAIL      0/11

false_success_report_rate            0
false-success mutant bị bắt          VERIFIED
```

**Không có điểm tổng.** Cổng này báo từng năng lực, và ba trong bốn nhãn không
phải "hỏng".

---

## 1. MƯỜI MỘT NĂNG LỰC

| | ca | trạng thái |
|---|---|---|
| 01 | forget trước khi enqueue | **PASS** |
| 02 | enqueue rồi mới forget | **PASS** |
| 03 | worker đang giữ lease thì forget | **INVALID** |
| 04 | materialize rồi mới forget | **PASS** |
| 05 | forget rồi replay | **PASS** |
| 06 | forget rồi dựng lại nhiều vòng | **PASS** |
| 07 | hai job trùng cho cùng một sự kiện | **PASS** |
| 08 | worker thử lại nhiều vòng | **PASS** |
| 09 | worker chết khi giữ lease rồi khởi động lại | **INVALID** |
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

## 2. HAI CA INVALID — đọc chính xác

**03 và 09 INVALID vì mutant không chạm tới được trạng thái bị cấm.**

Bằng chứng đo được, năm lần worker chạy, kể cả với `lease_seconds=0`:

```
claim -> 1 job
vòng 1..5:  tombstoned=0   completed=0   holding=0
```

Worker **không xử lý job đã bị claim lần nào**. Nó không từ chối job — nó không
bao giờ nhận job đó.

**Không được viết "worker safely refuses".** Nó không refuse. Nó không chạy.

Đọc đúng hai ca này:

> Hành vi an toàn **có bằng chứng** ở P0-A
> (`test_c_forget_while_worker_holds_the_lease`), nhưng Projection Safety Gate
> **chưa có mutation witness** cho chính execution shape đó.

Không phải bug sản phẩm. Không phải PASS. Không phải FAIL.

### Tripwire

Cả hai ca **assert rằng chúng vẫn INVALID**. Ngày nào một refactor làm job
re-claimable, assertion đỏ lên và bắt cập nhật thành PASS — vì execution
contract đã đổi.

Cùng kiểu chuông mà audit đã đặt cho `:335`, và nó vừa reo đúng lúc bản vá đáp
xuống. **Một ca INVALID không có ngày hết hạn sẽ ở lại mãi.**

---

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

## 6. TRẠNG THÁI GLOBAL — chưa đổi

```
Queued/in-progress forget safety      VERIFIED
Buried worker enforcement             VERIFIED + mutation-sensitive
Buried terminal transition / drain    VERIFIED
Rollback drain-to-zero                VERIFIED
#7 Door 5                             PASS (non-regression)
Global replay-safe forgetting         PARTIALLY VERIFIED      <- 9/11, 2 INVALID
Projection worker on real store       NOT ACTIVATED
Real-store containment                VERIFIED by subsystem invariants
```

**Chưa được ghi chữ VERIFIED cho global replay-safe forgetting.** Hai ca chưa có
nhân chứng thì chưa cho phép.

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
