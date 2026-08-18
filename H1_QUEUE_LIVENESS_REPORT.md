# H1 — QUEUE LIVENESS

Trạng thái hồ sơ, theo đúng thứ tự thời gian. Không sửa entry cũ.

| # | Kết luận | Bằng chứng | Số phận |
|---|---|---|---|
| 1 | `FAIL` | một probe rời, 4 vòng, healthy đứng yên | **RÚT LẠI** — không lặp lại được trong harness |
| 2 | `NOT ESTABLISHED` | harness bất định: cùng code, cùng số vòng, kết quả đảo chiều | thay thế bởi #3 |
| 3 | `FAIL` | harness tất định, 9/9 lần chạy giống hệt, ca P4 quyết định | **hiện hành** |

## Correction

Entry #1 sai về **loại phát biểu**. `FAIL` là khẳng định về sản phẩm; thứ em có
lúc đó chỉ là một quan sát chưa lặp lại được. Entry #2 sửa loại phát biểu.
Entry #3 mới là `FAIL` có quyền tồn tại, vì nó đứng trên một thước đã tự chứng
minh là tất định trước.

Entry #2 còn sai thêm một lần nữa về **vị trí** của bất định: em báo rằng ca
`starvation` là ca chập chờn. Không phải. Ca đó ổn định 11/11. Hai ca chập chờn
là `p4` và `six_round_divergence`.

## First divergence

`ProjectionOutbox` đặt `created_at = time.time()`. Trên máy này, ĐO trực tiếp
(không lấy thông số công bố làm nguồn sự thật):

```
reported_resolution          0.015625      # con số công bố — KHÔNG phải biên nhân quả
observed_min_positive_delta  ~0.000510     # bước nhảy dương nhỏ nhất quan sát được
repeated_equal_timestamps    199986/200000 # hai lần đọc liên tiếp trùng nhau
```

*(Correction 18/08: đoạn này từng dẫn `15.625ms` như thể nó là độ hạt thật của
đồng hồ. Con số thật nhỏ hơn ~30 lần. Kết luận của mục này KHÔNG đổi — nó đứng
trên việc timestamp TRÙNG NHAU, đo trực tiếp, không đứng trên con số ấy.)*

Hai lần `observe()` liên tiếp mất ~1 ms, nên **5/8 lần hai job nhận cùng một
`created_at`** — đo trực tiếp. `claim()` sắp bằng `ORDER BY created_at`, không tiebreaker. Với
hai hàng bằng nhau, thứ tự trả về không được định nghĩa.

Thí nghiệm tách đôi, mỗi nhánh 6 lần:

```
ép TRÙNG created_at   → vòng 1 chọn POISON → job lành XONG        6/6
ép TÁCH  created_at   → vòng 1 chọn POISON → job lành ĐỨNG YÊN    6/6
```

Cả hai nhánh đều chọn POISON ở vòng 1. Khác biệt sinh từ vòng 2 trở đi: khi
trùng, SQLite được tự do trả job lành lên trước ở một vòng nào đó, và job lành
*tình cờ* có lượt.

> Cái cứu job lành khỏi starvation là một tai nạn của đồng hồ, không phải một
> cơ chế.

## Ba guard đã thêm vào `_store`

1. fixture định vị job qua `event_id` của nội dung, không qua vị trí ngầm
2. khẳng định trạng thái đầu: đúng số hàng, tất cả `pending`, `attempts=0`
3. `created_at` được ghim thành các mốc cách nhau 1 s theo thứ tự nạp

Ghim `created_at` không làm test dễ hơn. Nó biến "poison vào hàng trước" thành
sự thật hiển ngôn thay vì đúng một cách tình cờ. Trong production hai lần ghi
cách nhau > 15.6 ms là trường hợp thường; trùng tick mới là ngoại lệ.

## Repeatability sau khi sửa thước

```
6 lần trong cùng process   → 8 passed, 1 xfailed  (giống hệt)
3 lần process mới hoàn toàn → 8 passed, 1 xfailed  (giống hệt)
```

## Ca quyết định P4 — đo được, không suy ra

12 vòng, `batch_size=1`, handler chết mỗi lần chạm job độc:

```
POISON      in_progress  attempts=12   ← nhận 12/12 lượt giao
HEALTHY_A   pending      attempts=0    ← chưa từng được giao
HEALTHY_B   pending      attempts=0    ← chưa từng được giao
```

Job lành không chậm. Nó chưa từng được giao lần nào.

## PROVEN

- `attempts` tăng theo delivery/claim, không theo failure
- worker-loss và processing-failure dùng chung một bộ đếm `attempts`
- đường `fail()` tường minh có chặn trên: `DEAD_LETTER` đúng lần thứ 5, backoff tăng dần
- job bị bỏ rơi thu hồi được
- starvation: 12/12 lượt giao về một job, hai job lành không tiến triển

## NOT PROVEN

- nguyên nhân gốc của việc không có fairness (mới đo được *hiện tượng*, chưa
  chốt được *hợp đồng* nào đang bị vi phạm — FIFO nghiêm hay eventual delivery)
- rằng ordering không ổn định là một defect sản phẩm chứ chỉ là đặc tính:
  `ORDER BY created_at` không tiebreaker nghĩa là **mọi** job nạp trong cùng một
  tick 15.6 ms đều không có thứ tự xác định. Đây là quan sát riêng, chưa vá.

## Chưa làm, có chủ đích

Chưa sửa một dòng product code nào. Ba hợp đồng khắc phục — fairness scheduling,
abandoned-lease quarantine, hoặc cả hai — vẫn chưa được so sánh trên trục
data-loss / poison isolation / crash recovery / tenant fairness / drain
convergence / operator recovery.

`Activation Gate` vẫn **BLOCKED**. Worker trên store thật vẫn **NOT ACTIVATED**.

---

# Entry #4 — H1.3 FAIRNESS CONTRACT (bản vá)

Không sửa entry nào ở trên. Đây là mục mới.

## Cơ chế

`outbox._yield_expired_leases` — một lease hết hạn được đặt lại `pending` với
`available_at` đẩy tới, **chỉ khi còn việc khác đang đợi**. Ba điều nó không
làm, mỗi điều là một quyết định có bằng chứng:

- **Không dead-letter, không quarantine.** H1.2 đo 11 tín hiệu, 2 bền, cả hai
  chỉ là định danh công việc. Hạ tầng chập chờn và payload độc để lại cùng một
  dấu vết.
- **Không luỹ thừa, mà tuyến tính có trần.** Bake-off đo backoff luỹ thừa tụt
  xuống DƯỚI hành vi cũ đúng hai fixture churn.
- **Không nhường khi không có ai đợi.** Fairness là quan hệ giữa các job. Phụ
  phẩm: bán kính ảnh hưởng lên reclaim đơn-job bằng không.

`drain()` giữ `drained` cho phía gọi cũ, thêm `outcome` =
`DRAIN_COMPLETE` | `DRAIN_INCOMPLETE_UNRESOLVED` và `unresolved`.

## Compatibility experiment — Projection Safety Gate 03/09

Bản vá đầu làm gate tụt `PASS → INVALID` ở hai ca. Điều kiện hỏng, đọc ra:

```
CONTROL_EXECUTED                     True
DANGEROUS_PATH_EXECUTED              True
MUTANT_TRIGGERED_PROHIBITED_STATE    False   ← chỉ mình nó
CLEAN_PREVENTED_PROHIBITED_STATE     True
```

Không phải fairness xoá đường nguy hiểm — nó **dời** đường đó. `_drain` cũ quay
hết trong vài micro giây đồng hồ thật nên không chạm mốc.

Sửa **không** bằng cách tăng số vòng. `_drain_past_eligibility` đọc
`MIN(available_at)` rồi đẩy đồng hồ tới đúng mốc đã đọc. Hai ca giờ khẳng định
cả hai nửa — thiếu vế nào cũng đỏ:

```
yield_waits                    không rỗng   → fairness đang có hiệu lực
reclaimed_after_eligibility    không rỗng   → đường nguy hiểm vẫn tới được
```

Một sai lầm đã mắc trên đường đi: probe đầu tiên chỉ dựng **một** job nên yield
không bắn, và kết luận "cơ chế nhường không chạy ở ca này" là sai. Kịch bản
gate thật có hai job (`clean_outbox: {'pending': 1, 'skipped': 1}`).

## Một cái chuông kêu sai lý do

`test_p4` vẫn `xfail` sau khi fairness vào, nên nó **không** nằm trong danh
sách đỏ và trông như defect còn nguyên. Truy ra: nó xfail vì một TIỀN ĐỀ hỏng
(`deliveries >= 3` không còn đạt), không vì kết luận. Sổ giao việc nói vì sao —
mỗi job đúng **1 lượt**: poison 1, healthy_A 1, healthy_B 1.

> Một cái chuông kêu đúng giờ nhưng sai lý do vẫn là chuông hỏng.

Chỉ đọc danh sách đỏ thì đã kết luận P4 chưa được sửa.

## Kết quả

```
tests/test_fairness_contract.py   13/13   9 thuộc tính + 4 mutant, cả 4 giết được
tests/test_projection_safety_gate 13/13   03 và 09 có nhân chứng mutation trở lại
tests/test_queue_liveness         9/9     bốn ca viết lại theo hợp đồng mới
full suite                        1254 passed, 3 skipped, 5 deselected, 10 xfailed
```

Sổ khớp với baseline `1240/3/5/11`: `+13` file mới, `+1/-1` do p4 đổi vai. Không
ca nào khác dịch chuyển.

## Chưa làm

Chưa đo nhiều worker song song thật (bộ test một tiến trình). `handler_started`,
heartbeat, attempt-history vẫn nằm trong P1 Attribution/Observability RFC.

`Activation Gate` **BLOCKED**. Store thật **UNTOUCHED**.
