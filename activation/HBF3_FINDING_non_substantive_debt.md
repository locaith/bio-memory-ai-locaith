# FINDING (chưa sửa) — event non-substantive để lại nợ replay vĩnh viễn

Phát hiện trong canary của HBF-3, 19/08/2026. **Không phải do migration sinh
ra** — đây là hành vi có sẵn của pipeline sống. Cần chữ ký trước khi sửa vì
nó là thay đổi hành vi sản phẩm.

## Hình dạng

Dưới `OUTBOX`, một hook non-substantive (`hook=SessionStart`, không có nội
dung sau dấu `|`) đi qua `_projection_debt_allowed(substantive=False) → False`,
nên `observe(enqueue_projection=False)`: **không có outbox row nào được ghi**.

`ProjectionReplayEngine.scan()` thấy một event không có outbox row cho
`cognitive_memory` và xếp nó `MISSING_OUTBOX` — nợ actionable, vĩnh viễn.

```
đo được sau canary HBF-3:
  historical actionable replay debt      0     (migration đã đóng 305 hàng)
  debt từ event sinh sau install         5     toàn bộ là marker SessionStart
```

## Vì sao đáng kể

`replay --repair` sẽ enqueue những job đó, worker sẽ dựng
`hook=SessionStart` **thành một ký ức** — đúng cái đầu mà substantive gate đã
cắt ở L3. Con rồng không mọc lại đầu bằng đường ghi trực tiếp nữa; nó mọc lại
bằng đường *phục hồi*.

Nghĩa là bất biến `substantive gate` hiện chỉ đúng trên đường ghi, **chưa**
đúng trên đường replay.

## Cách sửa tự nhiên (chưa làm)

Hook ghi một outbox row `SKIPPED` với `last_error='non_substantive'` cho các
event này — đúng docstring của `JobStatus.SKIPPED`:

> Recording the decision keeps "nothing is owed" distinguishable from
> "nobody has got to it yet".

Đó cũng chính là điều migration đã làm cho 35 hàng lịch sử (`EVENT_ONLY_SKIP`
→ outbox `SKIPPED`, `historical_event_only`). Sau khi sửa, nợ actionable của
marker mới sẽ về 0 và replay không còn đường dựng lại marker.

## Kiểm phải kèm theo khi sửa

1. hook non-substantive → đúng 1 outbox row `SKIPPED`, 0 memory, 0 ledger
2. `replay --repair` sau đó: 0 enqueued cho event marker
3. mutant: bỏ dòng ghi SKIPPED → replay dựng lại được marker thành ký ức
   (phải làm test đỏ)
4. backfill: 5 event marker đang tồn tại cần một lượt adopt nhỏ cùng cơ chế
   `EVENT_ONLY_SKIP` — offline candidate, không in-place
