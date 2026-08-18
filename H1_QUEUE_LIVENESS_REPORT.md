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

`ProjectionOutbox` đặt `created_at = time.time()`. Trên máy này:

```
time.get_clock_info('time').resolution = 0.015625   # 15.625 ms
```

Hai lần `observe()` liên tiếp mất ~1 ms, nên **5/8 lần hai job nhận cùng một
`created_at`**. `claim()` sắp bằng `ORDER BY created_at`, không tiebreaker. Với
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
