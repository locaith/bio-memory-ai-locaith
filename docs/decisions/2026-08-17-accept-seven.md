# #7 ACCEPTED — proposition identity ≠ lifecycle event identity

**17/08/2026.** Chấp nhận `1fc3398` sau sáu cửa. Không sửa gì thêm, nên C1 giữ
nguyên hash: nó đã là một commit sạch và không cần chỉnh.

```
B2  494d197   baseline bất biến, đo tại e10e376
C1  1fc3398   = B2 + patch sha256 86F994AF1F38F79254FBB850BA6101077CF972188B96C07C66A67B53F56B4F39
```

## Sáu cửa

| | cửa | kết quả |
|---|---|---|
| 1 | Baseline contract hợp lệ | **PASS** — B2 @ `e10e376`, `dirty=False` |
| 2 | C1 là hậu duệ, đúng phạm vi patch | **PASS** — `merge-base(B2,C1)==B2`, đúng 2 file |
| 3 | Positive-control Phạm Nam | **PASS** — `False → True` |
| 4 | Audit nhân quả từng delta | **PASS** — 2/2 |
| 5 | Replay resurrection qua rebuild | **PASS** — đo lại 17/08, xem §Cửa 5 |
| 6 | Full suite tại C1 | **PASS** — 1184 passed, 3 skipped, 10 xfailed |

## Dấu chân nhân quả

```
NO_DELTA 134    UNEXPECTED_COUPLING 2

ever       40 -> 40   +0
current    40 -> 40   +0
historical 31 -> 32   +1
forgotten  16 -> 17   +1
```

Đúng hai câu, và đúng hai câu đó: `Lê Sơn đang giữ chức vụ gì?` và
`Vào ngày 2025-06-13, Lương của Phạm Nam là bao nhiêu?` Chạy hai lần, tái hiện
giống hệt.

Cơ chế, chứng minh cho cả hai — cùng một hình dạng:

```
t44/t316   ASSERT   'lương của phạm nam là 18 triệu'  /  'lê sơn … trưởng nhóm'
t478/t774  CORRECT  cùng proposition_key
           dưới khoá cũ: gập vào nhau, sự kiện bị nuốt = ['correct']
```

Đính chính mang đúng mệnh đề của khẳng định trước nó, nên khoá-theo-văn-bản cũ
nuốt mất. Nó không mở span; giá trị đúng không có span sống nào; câu trả lời rơi
xuống chỗ khác. #7 tách hai trục, đính chính tồn tại như một sự kiện, và
`STATE_AT` chọn được nó.

## Cửa 5 — ĐÍNH CHÍNH 17/08: NOT_EVALUABLE → PASS

Cửa 5 từng là `NOT_EVALUABLE` vì tôi kết luận **không có consumer nào** vật chất
hoá outbox. **Kết luận đó sai.** `ReconciliationWorker` claim outbox và ghi
`cognitive_memories`, có trong cây từ `d42cc65` ngày 04/08 — mười hai ngày trước
khi #7 được chấp nhận trên tiền đề nó không tồn tại. Grep của tôi trượt vì
consumer **không bao giờ gõ tên bảng** trên đường tiêu thụ; nó đi qua đối tượng
`self.outbox.claim(...)`.

Đường nguy hiểm giờ chạy được, nên câu hỏi trả lời được.

`tests/test_seven_door_five.py`, hai nhánh khác nhau **duy nhất** ở ngữ nghĩa
#7, bật tắt trong cùng một tiến trình — vì HEAD đã chứa cả P0-A và bản vá `:335`,
và so HEAD với commit trước #7 sẽ lẫn ba thay đổi:

| | pre-#7 | #7 |
|---|---|---|
| control, không forget | materialize | materialize |
| bản sạch, sau forget | **chặn** | **chặn** |
| mutant làm mù chốt `buried` | **hồi sinh** | **hồi sinh** |

Mutant hồi sinh ở **cả hai** nhánh: hai nhánh tương đương về an toàn, và ca sạch
phía trên có tải. Cộng một ca chứng minh fixture thật sự bật tắt được #7 — nếu
không, ba ca kia đang so hai bản sao của cùng một thứ.

**Đây là chứng minh KHÔNG-HỒI-QUY, không phải chứng nhận an toàn toàn cục.**
`#7 Door 5 = PASS` trả lời "#7 có làm yếu tombstone safety không". Nó không trả
lời "Bio-Memory đã replay-safe chưa" — câu đó vẫn `PARTIALLY VERIFIED`.

## GIỚI HẠN — bắt buộc đọc kèm mọi phát biểu về replay

> Replay-resurrection qua projection materialization vẫn **NOT_EVALUABLE**, vì
> chưa xác định được consumer nào của `projection_outbox`.

**Không được viết "replay safe ✓" dựa trên probe hiện tại.**

`ProjectionReplayEngine.replay(dry_run=False)` chỉ **xếp hàng**
(`enqueued=5, skipped_tombstoned=0`); không có gì trong `bio_agent_os/` tiêu thụ
outbox đó để dựng lại memory. Nên mutant `IGNORE_TOMBSTONE_DURING_REBUILD`
không giết được gì — đường nguy hiểm chạm không tới.

    UNREACHABLE SAFE PATH != VERIFIED SAFETY

Cửa 5 được phép để mở **không phải vì sốt ruột**, mà vì một phép đo:
`test_the_patch_does_not_touch_forget_or_replay_infrastructure` chứng minh
`B2..C1` đổi đúng hai file và **không một dòng nào** trong diff nhắc tới
tombstone / forget / erase / replay / outbox / projection. Không có đường nhân
quả nào từ #7 tới resurrection. Ngày nào #7 chạm vào một dòng của hệ đó, test
này đỏ và cửa 5 lập tức đổi từ *khoảng trống hạ tầng* thành *rủi ro chưa kiểm
chứng*.

Hai điều **đã** đo được thật về forget: scope đúng (A mất, chứng B và C sống), và
sự kiện #7 cứu **không phải** sự kiện bị quên — cái bị quên là
`nhân viên kinh doanh`, cái được cứu là đính chính về `trưởng nhóm`.

## Việc mở ngay sau đây — P0 riêng

**Projection Materialization Audit.**

```
event log → replay() → projection_outbox → ??? → cognitive memory
```

Chưa viết consumer. Trả lời trước:

1. Có consumer nào ngoài `bio_agent_os/` không?
2. Có runtime/service/script nào drain `projection_outbox` không?
3. **Các test replay-resurrection từng xanh trước đây đã chứng minh tombstone
   bằng đường nào?** — câu quan trọng nhất. Nếu chúng chỉ kiểm enqueue thì
   Bio-Memory đã mang một claim mạnh hơn bằng chứng của nó một thời gian.
4. `projection_outbox` là kiến trúc chưa hoàn thiện, hay producer của một worker
   ở repo khác?
5. Production hiện có dựa vào replay để rebuild không?

## Ghi chú trung thực về bất biến CONFLICT

Ba mutant `SINGLE_CLAIM_CONFLICT`, `DUPLICATE_VALUE_CONFLICT`,
`COEXIST_AS_CONFLICT` **chết ngay từ code đã có**. `_conflict_state` vốn đã đòi
≥2 claim, đòi các `proposition_key` khác nhau, và đòi predicate đơn trị. Không
thêm logic mới — thêm **nhân chứng** cho logic đã có. Ba tính chất đó trước giờ
đúng một cách tình cờ, theo nghĩa không ai chứng minh được chúng đúng.

## Bốn luật phòng thí nghiệm

```
NO EXECUTION          != ZERO PERFORMANCE
STALE BASELINE        != CAUSAL DELTA
SAME SCORE            != SAME BEHAVIOR      (và BETTER SCORE != PROVEN IMPROVEMENT)
UNREACHABLE SAFE PATH != VERIFIED SAFETY
```
