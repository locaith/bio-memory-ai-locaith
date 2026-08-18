# H1.2 — ABANDONMENT ATTRIBUTION AUDIT

Câu hỏi: **khi worker chết trước `fail()`, runtime hiện tại có tín hiệu nào để
biết đó là hạ tầng chập chờn hay payload độc?**

Audit thuần. Không sửa product. Ống kính: *control flow kể chuyện, persistent
state mới ký biên bản* — worker chết thì mọi biến trong bộ nhớ chết theo, nên
chỉ thứ đã ghi xuống **trước** khi chết mới dùng để quy trách nhiệm được.

## Đo, không đọc

Ba worker khác nhau lần lượt giữ rồi mất cùng một job:

```
ban dau                     status=pending      attempts=0  locked_by=None    last_error=None
A giu, chet TRUOC handler   status=in_progress  attempts=1  locked_by=worker-A  last_error=None
B thu hoi                   status=in_progress  attempts=2  locked_by=worker-B  last_error=None
C thu hoi                   status=in_progress  attempts=3  locked_by=worker-C  last_error=None
```

Sau ba lần, bảng chỉ còn biết `worker-C`. Không bảng nào khác trong cả hai
connection ghi lại chuyện đã xảy ra.

## Mười một tín hiệu, đối chiếu với durable state

| tín hiệu | cột | bền? | dùng được để quy trách nhiệm? |
|---|---|---|---|
| worker_id | `locked_by` | **bị ghi đè mỗi lần claim** | không — chỉ biết worker cuối |
| claim_id / lease owner | không có `claim_id` | — | không |
| **handler_started?** | **không tồn tại** | — | **không — đây là khoảng trống quyết định** |
| last heartbeat | không có cột | — | không — job chạy lâu và worker chết trông giống hệt nhau |
| process exit reason/code | `last_error` | chỉ `fail()` ghi | không — worker chết thì không bao giờ gọi `fail()` |
| job/event identity | `event_id`, `projection_key` | có | có, nhưng không phân biệt nguyên nhân |
| payload / projection type | `projection_type`, `projection_version` | có | có, nhưng không phân biệt nguyên nhân |
| crash stage | không tồn tại | — | không |
| same job across different workers | `locked_by` bị ghi đè | — | không |
| same crash signature across retries | `last_error` một giá trị, không lịch sử | — | không |
| time-to-death after claim | `locked_at` = lúc claim | bị ghi đè | không — chỉ có cận dưới cho lần cuối |

**2/11 bền, và cả hai đều là định danh công việc — không cái nào nói được nguyên nhân.**

## Vì sao quarantine mù là mù

`attempts` là bộ đếm duy nhất sống sót, và nó gộp cả ba ngữ nghĩa:

```
delivery_attempts     claim() tăng nó
processing_failures   fail() đọc nó
abandoned_leases      không gì ghi nó
```

Bake-off cho `false quarantine 3/8`. Audit này giải thích tại sao con số đó
không hạ được bằng cách chỉnh ngưỡng: **runtime không ghi lại sự khác biệt.**
Hạ tầng chập chờn và payload độc để lại **cùng một dấu vết** — `in_progress`,
`attempts` tăng, `last_error` rỗng, `locked_by` là worker gần nhất.

## `claim_lifecycle.py` KHÔNG phải nhân chứng

`memory_lifecycle_transitions` là vòng đời **claim ngữ nghĩa** của ký ức
(CTO = An → Bình). Trùng tên "claim", khác hẳn việc. Không liên quan tới lease
của job.

`projection_ledger` là sổ **thành công** (`projection_key` PRIMARY KEY,
`target_id`, `worker_id`), ghi ai làm xong — không ghi ai đã thử.

## Phát hiện phụ: `locked_at` cũng thô

Trong probe, A và B có `locked_at` **giống hệt nhau** — cùng một tick 15.625 ms.
Cùng đúng cái resolution đã làm hỏng ma trận H1. Kể cả nếu dựng attribution dựa
trên thời điểm, độ phân giải hiện tại không đủ tách hai lần claim liền nhau.

## Kết luận

**Không có discriminator.** Không phải "yếu", mà là **không tồn tại**.

Theo đúng nhánh đã định trước cho trường hợp này: contract phải ưu tiên không
mất dữ liệu — fairness/backoff để việc lành tiến được — cộng observability,
thay vì tự động cách ly dựa trên một bộ đếm mù.

> Nếu hệ thống không biết vì sao worker chết, nó không nên giả vờ biết job có
> độc hay không.

Đối chiếu bake-off, nhánh này trùng với **A**: `18/18` healthy không cần
operator, `0` mất mát, `0` cách ly nhầm, `0` phụ thuộc người trực. Điểm yếu của
A — poison chạy mãi, drain không hội tụ — là bài toán **hội tụ và quan sát**,
không phải bài toán **đúng đắn hay mất dữ liệu**.

## Chưa được kết luận từ audit này

Audit nói các tín hiệu **hiện không tồn tại**. Nó **không** nói không thể thêm.
Thêm `handler_started` và một bảng lịch sử lần-thử sẽ làm attribution khả thi —
nhưng đó là thay đổi schema, là product change, và là quyết định của anh chứ
không phải hệ quả của audit.

Cũng chưa đo: nhiều worker chạy song song thật (probe dùng một tiến trình), và
liệu "nhiều job không liên quan cùng chết một lúc" có nhận ra được không — tín
hiệu đó cần quan sát ở mức đội worker, không ở mức một hàng.

`Activation Gate` **BLOCKED**. Store thật **UNTOUCHED**.
