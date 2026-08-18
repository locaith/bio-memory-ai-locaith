# H1.4 — MULTI-WORKER CONCURRENCY GATE

Bảy ca, mỗi ca qua admissibility gate: đúng population · treatment đã chạy ·
đúng mục tiêu · interleaving có nhân chứng · đường quan sát hoạt động · trạng
thái cấm chạm được dưới mutant · code sạch chặn nó. Nhân chứng là **sự kiện
đồng bộ** (barrier/Event), không bao giờ là timestamp.

## Ma trận

| ca | mức | kết quả | mutant (giết được) |
|---|---|---|---|
| 01 double reclaim | thread + **process** | PASS — một ack duy nhất | pre-CAS unconditional UPDATE |
| 02 stale yield vs live claim | thread | PASS — lease B nguyên vẹn | YIELD_IGNORES_CURRENT_CLAIM_STATE |
| 03 poison + healthy, 2 worker | thread×2, conn riêng | PASS — cả hai healthy xong, poison RETRYABLE, không độc chiếm | ELIGIBILITY_IGNORES_LEASES |
| 04 mixed tenants đồng thời | thread×2 scoped | PASS — 0 claim xuyên tenant, đo trên sổ giao việc | SKIP_TENANT_SCOPE (bỏ scope → xuyên thật) |
| 05 crash + peer reclaim | 1 crash + 2 peer đua | PASS — đúng-một ack, việc XONG, không lease vĩnh viễn | (CAS đã có nhân chứng ở 01) |
| 07 forget giữa cửa sổ thu hồi | barrier trong claim | PASS — 0 resurrection | IGNORE_..._BURIED_CHECK → hồi sinh thật |
| 06 drain đồng thời | 2 drain thread, MemoryOS riêng | PASS — exactly-once, cả hai kết luận INCOMPLETE_UNRESOLVED (poison lease sống) | máy đếm được kiểm bằng bản sao cấy |

Toàn bộ trong **miền lease hợp lệ** (300s), đồng hồ đẩy qua mốc đủ-điều-kiện
tường minh; `lease_seconds=0` đã bị `validate_lease_seconds` từ chối từ commit
`55cd240`.

## Ba mutant chết non — và chúng dạy về kiến trúc

Ba mutant đầu cho ca 03 KHÔNG giết được ca sạch, và mỗi lần là một sự thật:

1. **Gỡ nhường lượt** không gây starvation với 2 worker: kẻ thua thấy lock
   tươi nên rơi xuống job lành. Starvation no-yield là hiện tượng batch-1
   đơn-worker (mutant đó sống ở fairness contract).
2. **Dịch cả hai đồng hồ** vô hại: dịch đều bảo toàn thứ tự tương đối.
3. **Một worker lệch đồng hồ** phá mutual exclusion (cướp lease sống) nhưng
   không phá tiến triển: MỘT worker đúng đắn là đủ để nuôi job lành.

Cột chịu lực thật của thuộc tính hai-worker: **eligibility tôn trọng lease
sống dưới một đồng hồ chung**. Gỡ đúng cột đó ở cả hai worker thì starvation
mới xảy ra. Hệ quả vận hành đáng ghi: *clock skew giữa các worker là một rủi
ro thật đối với mutual exclusion* — một node lệch +1000s cướp được lease sống.
Chưa phải bug hôm nay (một tiến trình, một đồng hồ), nhưng là ràng buộc phải
nhớ khi có worker phân tán.

## Stale-observation inventory (chỉ liệt kê — không refactor)

```
claim                   UPDATE có vị từ bảo vệ      (chứng minh chịu tải: 01)
_yield_expired_leases   UPDATE có vị từ (CAS)       (chứng minh chịu tải: 02)
skip                    UPDATE có vị từ
complete                UPDATE CHỈ THEO job_id      ← ứng viên
fail                    UPDATE CHỈ THEO job_id      ← ứng viên
```

`complete`/`fail` được gọi bởi kẻ-đang-giữ sau khi xử lý; nếu lease bị nhường
hoặc bị thu hồi giữa chừng, chúng có thể ghi đè trạng thái mới của người khác.
**Chưa có tái hiện** — theo đúng kỷ luật, không vá cho tới khi có. Backlog P2.

## Điều còn lại, nói thẳng

- Ca 02 ở mức **process** không chạy riêng: cơ chế bảo vệ của nó (optimistic
  conditional UPDATE) trùng đúng shape đã chứng minh ở 01 process-level, nhưng
  đó là suy luận đồng dạng, không phải phép đo riêng. Nếu anh coi đây là lỗ,
  nó là việc một buổi.
- Toàn bộ 03–07 chạy thread-với-connection-riêng, không phải process. 01 là ca
  duy nhất có bằng chứng process-level.
- Ca WAL doctor đỏ 1 lần dưới tải, 3/3 cô lập, không lặp — giữ là load-sensitive
  observation, chưa mở lane.

## Kết luận

```
H1-SINGLE    VERIFIED
H1-MULTI     VERIFIED   (bảy ca VALID PASS, các giới hạn nêu trên)
H1 GLOBAL    VERIFIED
Activation   ELIGIBLE TO OPEN — CHƯA MỞ, chưa chạm store thật
```

Mở gate không phải kích hoạt. Store thật UNTOUCHED. Chờ anh duyệt.
