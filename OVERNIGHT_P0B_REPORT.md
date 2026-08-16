# P0-B CLOSED

`SUBJECT_IDENTITY_READ` mặc định **ON**. Integration Gate **PASS** ở
40/36/31/13, `NO_DELTA 136/136`. Ba temporal test được sửa bằng root cause,
không bằng đổi kỳ vọng. Full suite `1116 passed, 1 skipped, 5 deselected,
8 xfailed` ở đúng mặc định mới.

Một điều kiện của tiêu chí thành công **không** đạt theo nghĩa đen và tôi ghi rõ
ở §7 thay vì làm tròn: bộ test identity chỉ có 5/13 ca thực sự là bằng chứng cho
tính năng. Con số đó đã được ghi vào chính file test.

---

## 1. ROOT CAUSE — ĐÃ CHỨNG MINH

Ba test đỏ **không** do read path chọn sai, và **không** do thứ tự hàng. Chúng đỏ
vì danh tính lưu lúc ingest đã hỏng.

Điểm rẽ đầu tiên trong lần chạy hỏng: `temporal_operator.py:537`, trong
`_by_subject`. Mọi thứ phía trên — tham số, hàng đọc từ DB, `slot_key`, `probe`,
thứ tự hàng — giống nhau từng byte giữa hai nhánh.

Điểm rẽ đầu tiên trong **chuỗi nhân quả** thì sớm hơn nhiều, ở
`aspect_resolver.py:214` và `:231-243`:

- `_names` tách từ bằng `re.findall(r"[\w]+", …)`, tức **xoá sạch dấu câu**.
- `joins_a_name` cho một token thường nhập vào token viết hoa liền sau.

Từ cuối của mệnh đề *trước* tên bị nuốt vào tên:

```
"Đính chính: thông tin trước là sai, Bùi Cường sinh năm 1991."
    entity đã lưu = 'sai Bùi Cường'
"Nhắc lại, Bùi Cường làm việc tại công ty Locaith."
    entity đã lưu = 'Nhắc lại Bùi Cường'
```

`slot_backfill.py:135` ghi span đó thành `structured_json["entity"]`
(`facade.py:296-303`). Lúc đọc, `identify('sai Bùi Cường')` cho
`subject_id='sai bùi cường'` ≠ `'bùi cường'`, nên `:537-542` coi là **definite
mismatch** và loại thẳng — nhánh cứu bằng text ở `:543` không với tới hàng nào đã
có entity. Còn một hàng thì `:703` không còn span phía trước để đánh dấu
`corrected`, `:696-699` không còn gì để cộng `confirmations`.

**Một nguyên nhân, cả ba lỗi.**

### Hình dạng đáng giá hơn cả nguyên nhân

`_core` (`:347-356`) strip marker `_RESTATEMENT` và `_CORRECTION` trước khi so
hai claim. Resolver lúc ingest không có bước tương đương. Nên chính những khung
câu làm nên nghĩa vòng đời — "Đính chính,", "Nhắc lại," — là những khung câu phá
danh tính của câu đó.

**Identity read không xoá hàng ngẫu nhiên. Nó xoá đúng những hàng mang ngữ nghĩa
vòng đời.**

### Giả thuyết bị bác

| Giả thuyết | Kết luận | Bằng chứng |
|---|---|---|
| Thứ tự hàng | BÁC | `_by_subject` giữ nguyên `ORDER BY observed_at, rowid`, vẫn đỏ |
| `_by_subject` trả **nhiều** hàng hơn `_mentions` | **BÁC** | `identity_only = ∅` cả ba test; 2→1 và 5→4. Mọi phân kỳ đều là **mất hàng** |
| `slot_key=None` → `_by_aspect` thấy tập khác | BÁC | `elif probe:` chạy ở cả bốn nhánh; trên phần giao, output `_by_aspect` giống hệt |
| "entity mention ≠ claim subject" gây ba lỗi này | BÁC — nhưng khuyết tật có thật, §9 D1 | Trong cả ba fixture, mọi hàng đều nói về đúng Bùi Cường |
| Trích xuất entity khác nhau giữa các dạng câu | **ĐÚNG** — và tôi đã bác nó bằng phép đo hỏng, §2 | |

---

## 2. ĐÍNH CHÍNH — một giả thuyết bị bác bằng phép đo hỏng

Tôi từng ghi giả thuyết "trích xuất entity khác nhau" là DISPROVEN. **Kết luận đó
sai, và sai vì phép đo, không vì lý lẽ.**

Probe cũ dựng tập `known` từ chính các entity đã trích ra. `'sai Bùi Cường'` nằm
trong `known` nên khớp chính nó, nhận một `subject_id` hợp lệ, và tôi đọc "nhất
quán" thành "đúng". Nó nhất quán thật — nhất quán sai.

Cùng cái bẫy nằm trong code sản phẩm, `temporal_operator.py:518`:

```python
known = {e for e in (_entity_of_row(s) for *_, s in rows) if e}
```

Rác được nhận vào tập "người đã biết" chỉ vì đã được lưu, rồi dùng chính tư cách
đó để chứng minh mình là *người khác*. Tự hợp thức hoá.

---

## 3. BẢN VÁ

Sửa ở **producer**. `_by_subject` không union, không rescue bằng text, không danh
sách tên đặc biệt, không đổi một giá trị kỳ vọng nào.

`aspect_resolver.py` — hai ràng buộc, cả hai chỉ phát biểu lại luật hàm đã tự mô
tả trong docstring: tên không chạy qua ranh giới mệnh đề; cú cứu chữ thường chỉ
nổ ở đầu mệnh đề. Thêm: một run viết hoa đang mở đóng lại tại ranh giới.

| | trước `63ca264` | sau |
|---|---|---|
| entity sai / 9 dạng câu | **5** | **1** |
| eval world, đối chiếu ledger, 334 câu | — | **0 chủ thể sai** |

Cái còn lại là **giá đã biết**, ghi thành `xfail(strict=True)` chứ không giấu:
`"Ngày 21 phạm Vy sinh năm 1990."` → `'Vy'`. Họ viết thường mà không ở đầu mệnh
đề. Đó là **split**, không phải merge.

### Ngữ nghĩa đọc — thứ làm cho việc bật cờ sống được

`backfill()` **không được gọi ở đâu trong `bio_agent_os/`**; nó là pass thủ công.
Nên bật mặc định sẽ đập vào store thật với entity còn hỏng.

Nhưng đó là hỏi sai câu. Một hàng do resolver cũ ghi ra không phải "người khác",
nó là **"chưa biết"** — đúng hai trạng thái Constitution cấm gộp, mà `_by_subject`
đã gộp: `STRUCTURED_GAP` bị `MISMATCH` nuốt.

```python
if entity and not _current_resolver(structured_json):
    EXECUTION["identity_stale_resolver"] += 1
    entity = None          # rơi về đúng đường text bounded đang ship
```

Hàng cũ đọc y như hôm nay. Hàng mới tách danh tính đầy đủ. Migration từ **điều
kiện tiên quyết** thành **cải thiện**.

### Phương án bị loại — bằng phép đo, không bằng lý lẽ

Coi một id đã lưu kết thúc bằng `" " + wanted_id` là "cùng người, trích xuất
nhiễu". **Xanh cả 1086 test** và vẫn rò:

```
store:  "Vũ An đang giữ chức trưởng nhóm."
        "Trần Vũ An đang giữ chức giám đốc kỹ thuật."
hỏi "Vũ An":
  _by_subject đang ship  -> 1 hàng, không rò
  subsumption            -> 2 hàng, TRẢ VỀ HỒ SƠ CỦA TRẦN VŨ AN
```

Tôi tự dựng lại và đo, không nhận từ agent. **Một bản vá có thể xanh toàn tập và
vẫn là rò rỉ xuyên người.** Ca đó giờ là test.

---

## 4. TEST ĐÃ THÊM

- `tests/test_name_boundaries.py` — mutant `PREFIX_JOINS_THE_NAME`, **chính là
  code đang ship trước đêm nay**, chết **tại producer**. 8 khung câu cùng một
  claim về cùng một người; chủ thể phải không đổi.
- `tests/test_resolver_rederivation.py` — chứng minh migration sửa được store
  **đã ghi**, hàng cũ **không bị mất** khi chưa migrate, và nhân chứng không phải
  code chết.
- `tests/test_identity_read_path.py` — thêm ca tên lồng nhau và ca ranh giới
  mệnh đề.

Bộ test cũ chỉ giết mutant này qua `['asserted']` và `assert 0 == 1` cách đó ba
tầng — lý do hai vòng giả thuyết đi tìm ở thứ tự hàng và case folding.

---

## 5. SỐ ĐO

| | |
|---|---|
| Full suite, mặc định mới (ON) | **1116 passed, 1 skipped, 5 deselected, 8 xfailed** |
| Baseline trước, cờ OFF | 1086 passed, 1 skipped, 5 deselected, 7 xfailed |
| Ba test đỏ | `corrected` có mặt; `confirmations` 0→1; belief/world tách lại |
| `identity_excluded_mentioned` sau migration | 0 |
| eval world, 334 câu, đối chiếu ledger | 0 chủ thể sai |

### Trước/sau trên ba test

```
test_a_correction_is_marked_as_such_in_the_history
    trước: kinds = ['asserted']              sau: ['corrected', 'asserted']
test_repeating_a_fact_does_not_move_the_boundary
    trước: confirmations = 0                 sau: 1
test_world_truth_never_returns_the_corrected_value
    trước: "…sinh năm 1990." cho cả hai câu  sau: "…sinh năm 1991."
```

---

## 6. INTEGRATION GATE — **PASS**

```
nhóm            trước      sau     Δ
ever            40/40    40/40    +0
current         36/40    36/40    +0
historical      31/38    31/38    +0
forgotten       13/18    13/18    +0

NO_DELTA 136/136          không có ô nào không nhãn
privacy DISCOVERY   : 0 kho chưa khai báo
privacy ENFORCEMENT : 0 kho giữ nội dung ngoài verifier
```

Đường chạy, mọi lời gọi `claim_history` trong gate:

```
structured_identity_hit        136      structured_queries_total       133
identity_excluded            42125      identity_excluded_mentioned      3
identity_stale_resolver          0      ambiguous_identity_abstain       0
by_aspect_calls_total            0      fallback_attempted_total         0
unnecessary_fallback             0      unnecessary_fallback_rate      0.0
```

**Identity separation chạy trên mọi truy vấn và không đổi một câu trả lời nào.**
Đó là hình dạng đúng của một thay đổi an toàn: `structured_identity_hit = 136`
chứng minh nó *có* chạy, `NO_DELTA 136/136` chứng minh nó không phá gì. Không
phải "không thấy khác biệt vì không chạy" — lỗi mà `376c4ce` từng mắc.

### Nhân chứng nổ, và nó chỉ ra một lớp rò chưa ai thiết kế để chặn

`identity_excluded_mentioned = 3` — khác 0. Tôi dựng nhân chứng thì phải đọc nó.
Cả ba đều cùng một hình dạng, và không cái nào là mất hàng:

```
hỏi 'Trần Bình'  <- "Trần Thảo làm việc tại công ty Hoà Bình."
hỏi 'Vũ Minh'    <- "Từ hôm nay, vũ An làm việc tại công ty Bình Minh."
```

**Tên tổ chức cho mượn âm tiết còn thiếu của tên người.** `_mentions` đòi mọi âm
tiết xuất hiện **như một từ riêng** — luật đó được viết vì "An" từng khớp vào
"đ**an**g" — nhưng một âm tiết có thể vừa là từ riêng vừa thuộc về một công ty.

Đo cả hai nhánh, không suy luận:

```
hỏi 'Trần Bình' (employer)
  cờ=off  2 hàng   RÒ: "Trần Thảo làm việc tại công ty Hoà Bình."
  cờ=on   1 hàng   không rò
hỏi 'Vũ Minh' (employer)
  cờ=off  2 hàng   RÒ: "…vũ An làm việc tại công ty Bình Minh."
  cờ=on   1 hàng   không rò
```

**Đang rò, không phải sẽ rò.** Đây là bằng chứng khẳng định đầu tiên rằng
identity read làm việc thật — và nó do chính thiết bị đo mới tìm ra, không phải
do tôi đoán trước. Đã thành hai test parametrized, cả hai đều chết khi tắt cờ.

---

## 7. KẾT LUẬN P0-B

**CLOSED**, với một điều kiện ghi rõ chứ không làm tròn.

Đạt: root cause chứng minh được và sửa ở đúng chỗ; ba test xanh nhờ nguyên nhân
chứ không nhờ sửa kỳ vọng; mutant chết tại producer; cờ mặc định ON; suite xanh.

**Không đạt theo nghĩa đen:** "identity metrics 0" từng được coi là đã đạt ở
`63ca264`. Không phải. Lật cờ về `off` rồi chạy lại `test_identity_read_path.py`:
**8/15 ca vẫn pass**. Sáu ca họ-chung nằm trong đó, vì `_mentions` vốn đã đòi đủ
mọi âm tiết. Nên `cross_identity_candidate_rate = 0` **chưa từng là bằng chứng
cho code mới**. Số thật là **7/15**, đã ghi vào docstring của file.

---

## 8. QUYẾT ĐỊNH BASELINE

**Không đổi.** `31/38` vẫn là verified temporal baseline. Kết quả Gate ở §6 được
đọc theo baseline đó, không thay nó.

Một phát hiện làm thay đổi *cách hiểu* baseline mà không thay *con số*:
`lifetime_world.py:341-347` viết thường họ khi sinh câu, đúng thứ chặn
`joins_a_name`. Khảo sát 210 ô: **60 entity sai ở dạng người thật gõ, 0 ở dạng
eval-world sinh ra.** Nên `31/38` không phải "đã kiểm tra và không thấy lỗi này"
— benchmark **không có khả năng** thấy nó. Đó là một khoảng mù của phép đo, phải
ghi lại, và không phải lý do để sửa số cũ.

---

## 9. KHUYẾT TẬT TÁCH RIÊNG — liệt kê, không trộn vào

**D1. `entity` là tạo tác vị trí, không phải sở chỉ.** `aspect_resolver.py:351`
lấy tên **đầu tiên** trong câu, không phải đối số của vị từ.

```
"Công ty Locaith đã tuyển Bùi Cường."           -> entity='Locaith'
"Bạn của Phạm Vy là Bùi Cường, sinh năm 1991."  -> entity='Phạm Vy'
```

**Không** được sửa bởi bản vá này, và đây mới đúng là "mention ≠ claim subject".
Nó cần một luật chủ-ngữ-của-vị-từ thật, không phải một ràng buộc. **Dossier tiếp
theo.** Lưu ý: dưới cờ ON, hàng này rời khỏi lịch sử người đúng — nhưng bây giờ
có nhân chứng đếm nó, và theo luật `UNKNOWN > false identity merge` thì mất hàng
vẫn hơn trả nhầm hồ sơ.

**D3. `claim_history` nhận `context` và không dùng.** `:576` có tham số; SELECT ở
`:587-591` không có vị từ tenant/workspace. Có sẵn ở cả hai nhánh. Mới dưới cờ:
`known` ở `:518` gom entity qua **mọi tenant** trong file, nên *phân giải* danh
tính — không chỉ chọn hàng — phụ thuộc vào dữ liệu tenant khác.

**D4. `identify` tự hợp thức hoá.** Nhánh khớp chính xác (`subject_identity.py:101`)
chạy trước nhánh completion (`:109-114`), và `known` dựng từ chính entity của các
hàng. Docstring (`:17-19`) nói tên gọi trống không bao giờ gộp vào tên đầy đủ,
trong khi `:109-114` làm đúng điều đó khi chỉ có một ứng viên.

**D5. `test_belief_history_can_still_say_what_was_believed` xanh vì lý do sai.**
Nó chỉ assert `"1990" in answer_text`, không assert belief khác world. Bản vá làm
hai đường tách lại, nhưng test vẫn không nhìn thấy sự khác biệt đó.

**D7. `tests/conftest.py:36-39`** — `os.kill(pid, 0)` có thể ném `SystemError`,
không nằm trong tuple `(OSError, PermissionError)`, và giết cả pha collection.
Chỉ xảy ra với PID **tồn tại nhưng không mở được** (ví dụ PID 4). Thêm
`SystemError` vào except.

---

## 10. LANE PRIVACY

Đóng, commit `f8e0b63`, tách hoàn toàn khỏi P0-B.

`rejected_inputs` và `agent_checkpoints` giữ nội dung mà không bộ kiểm chứng xoá
nào quét tới. **Tự xác minh độc lập** bằng SQL thô trên database đã đóng rồi mở
lại, không qua verifier của chính `forgetting.py`:

```
TRƯỚC 63ca264:  erase_history -> verified_clean=True
                bí mật VẪN nằm ở rejected_inputs.content
SAU   f8e0b63:  erase_history -> verified_clean=True
                stores_redacted={'rejected_inputs': 1}   sạch thật
```

Loại lỗi tệ nhất trong nhóm privacy: **báo sạch trong khi chưa sạch.**

**Đính chính một số của lane này:** nó báo `forget_derived` trước khi vá trả
`verified_clean=True`; tôi đo được `False`. Ghi số tôi đo, không sửa số của nó.

**Một phép đo hỏng của chính tôi:** probe đầu dùng `MemoryType.SEMANTIC`, mà
`persistent` chỉ True cho sáu loại bền (`facade.py:395`) và
`quarantine = reasons and persistent`. Probe chưa từng đi vào đường quarantine và
báo "sạch" ở cả hai commit. `NO EXECUTION != ZERO PERFORMANCE`.

---

## 11. COMMIT

| commit | nội dung |
|---|---|
| `f8e0b63` | privacy — hai kho cuối ra khỏi vùng mù của bộ kiểm chứng xoá |
| `f090356` | resolver — một cái tên không chạy qua ranh giới mệnh đề |
| `74b409d` | identity — hàng do resolver cũ ghi là CHƯA BIẾT, không phải người khác |

Chưa push. Cây sạch ngoài chính file này.

---

## 12. BLOCKER CÒN LẠI

1. **D1** — cần luật chủ-ngữ-của-vị-từ. Dossier tiếp theo, không sửa vội.
2. **`backfill()` chưa có ai gọi.** Store thật của anh chưa migrate. Không nguy
   hiểm nữa (hàng cũ đọc theo đường cũ), nhưng identity separation **chưa có hiệu
   lực** với những hàng đó. `identity_stale_resolver > 0` là cách hệ thống nói ra
   điều đó. Chạy migration là một lệnh ghi vào bộ nhớ thật — **cần anh duyệt**.
3. **8/13 ca identity không phân biệt được tính năng.** Đã ghi, chưa sửa.
4. **7 Temporal Truth dossiers** — mở được rồi, chưa mở.
