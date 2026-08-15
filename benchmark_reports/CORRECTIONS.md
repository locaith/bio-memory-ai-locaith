# Đính chính kết quả benchmark

Số cũ không bị sửa. Mỗi mục dưới đây nêu con số đã công bố, vì sao nó sai, và
số nào thay thế nó. Sửa số cũ cho khớp số mới sẽ xoá mất chính bằng chứng rằng
phép đo từng hỏng.

---

## 2026-08-15 — Toàn bộ nhóm `forgetting` trong `behaviour_2026_08_14.json` là **VÔ HIỆU**

### Số đã công bố

| Hệ thống | Nhóm | Điểm |
|---|---|---|
| cognitive | forgetting | 2/3 |
| naive-rag | forgetting | 2/3 |

### Vì sao vô hiệu

`scripts/behaviour_benchmark.py` lọc bỏ dữ liệu mà lệnh `[XOÁ]` nhắm tới
**trước khi nạp vào kho**:

```python
kept = [(ws, t) for ws, t in facts if not is_deleted(t)]
```

Nên `forgetting.forget()` **chưa từng được gọi một lần nào**. Ba ca thiết kế để
chứng minh việc xoá hoạt động, không chứng minh gì cả.

Câu trả lời thật cho thấy rõ từng ca hỏng theo kiểu nào:

| Ca | Đáp án | Chấm | Thực tế |
|---|---|---|---|
| forget-001 | "No information available." | ĐẠT | Kho rỗng vì dữ liệu bị lọc bỏ, không phải vì xoá được |
| forget-003 | "No information available." | ĐẠT | Như trên |
| forget-002 | "Bệnh tim." | TRƯỢT | Matcher **không** khớp nên dữ liệu vẫn được nạp; trượt vì chưa từng xoá, không phải vì xoá hỏng |

**Bằng chứng rõ nhất:** `naive-rag` cũng được **2/3**, y hệt. Naive-RAG không có
cơ chế xoá nào. Nếu phép đo thật sự đo việc xoá thì hai hệ không thể bằng điểm.

### Nguyên nhân gốc

Thiếu một bước: **xác nhận dữ liệu truy hồi được TRƯỚC khi xoá**. Không có bước
đó, "không tìm thấy" sau khi xoá không phân biệt được với "chưa từng có". Sự mơ
hồ đó chính là thứ đã biến thành điểm đạt.

### Đã sửa

`run_case` giờ chạy đủ bốn bước: nạp → xác nhận có → `forget_derived()` → xác
nhận mất. Ca nào không xác nhận được bước hai sẽ bị đánh dấu **không kết luận
được** thay vì tính là đạt (`retrievable_before` trong kết quả).

`tests/test_behaviour_benchmark_integrity.py` giữ cả hai đầu: kiểm nguồn của
harness để dòng lọc kia không quay lại, và chạy bốn bước trên kho thật cho từng
ca.

### Còn nợ

`forget-002` được đánh dấu `requires_capability: topic_scoped_delete` và
**xfail(strict)**. Yêu cầu *"xoá mọi thông tin sức khoẻ của anh Hùng"* nêu CHỦ
ĐỀ cộng CHỦ THỂ; ký ức "Anh Hùng có tiền sử bệnh tim" không chứa chữ "sức khoẻ",
nên trùng từ chỉ đạt 1/3 dưới ngưỡng 0.5.

Hạ ngưỡng sẽ làm ca này xanh và không thêm một năng lực nào. Đây là lỗ hổng
thật, ghi lại làm việc phải làm, không phải làm cho điểm đẹp. `strict=True`
nghĩa là khi xoá theo chủ đề có thật, test này *không trượt nữa* sẽ tự báo lỗi —
không ai phải nhớ quay lại bật nó.

### Số thay thế — chạy lại 15/08/2026

`benchmark_reports/behaviour_forgetting_2026_08_15.json`, engine
`openai/gpt-4o-mini`, harness đã sửa.

| Hệ thống | Nhóm | Điểm cũ (vô hiệu) | **Điểm mới** |
|---|---|---|---|
| cognitive | forgetting | 2/3 | **2/3 = 67%** |
| naive-rag | forgetting | 2/3 | **0/3 = 0%** |

Điểm của `cognitive` trùng nhau là ngẫu nhiên, không phải xác nhận. Thứ đổi là
điểm của `naive-rag`: nó **rò cả ba** bí mật.

| Ca | naive-rag trả lời |
|---|---|
| forget-001 | `88888888` |
| forget-002 | `Bệnh tim.` |
| forget-003 | `Số điện thoại: 0912345678.` |

Đúng như phải thế — Naive-RAG không có cơ chế xoá nào. Việc trước đây nó bằng
điểm hệ có `forget()` chính là dấu hiệu phép đo hỏng, và giờ khoảng cách
**67% với 0%** là dấu hiệu nó đang đo đúng thứ.

**Điều kiện tiên quyết đã chứng minh: 3/3.** Mỗi ca đều xác nhận dữ liệu truy
hồi được trong kho *trước khi* xoá (`retrievable_before: true` trong báo cáo),
rồi mới gọi `forget_derived()`, rồi mới hỏi. Không có bước đó thì "không tìm
thấy" vẫn không chứng minh được gì.

`forget-002` trượt vì lỗ hổng `topic_scoped_delete` đã ghi ở trên, không phải
vì việc xoá hỏng.

### Cập nhật cùng ngày — lỗ hổng đã vá, chạy lại lần hai

`benchmark_reports/behaviour_forgetting_2026_08_15b.json`

| Hệ thống | Nhóm | Lần 1 | **Lần 2** |
|---|---|---|---|
| cognitive | forgetting | 2/3 = 67% | **3/3 = 100%** |
| naive-rag | forgetting | 0/3 = 0% | **0/3 = 0%** |

`forget-002` giờ đạt. `bio_agent_os/cognitive/forget_scope.py` tách yêu cầu
thành **chủ thể + chủ đề**, rồi chọn theo độ tương đồng **tương đối trong nhóm
ký ức của chủ thể** thay vì một sàn tuyệt đối.

Sàn tuyệt đối là chỗ sai gốc, đo được: chủ đề *"chức vụ"* so với *"… đang giữ
chức nhân viên kinh doanh"* được 0.329, trong khi sàn truy xuất là 0.5189 —
sàn đó hiệu chuẩn bằng **câu hỏi lạc đề so với cả câu văn**, một phép đo khác
thang. Xếp hạng vốn đã đúng (0.414 / 0.329 cho ký ức chức vụ, so với 0.123 /
−0.008 cho ngày sinh và nơi ở); chỉ có ngưỡng là hỏi sai loại câu hỏi.

Marker `xfail(strict=True)` trên `forget-002` đã gỡ — đúng cơ chế đặt ra khi
tạo nó: khi năng lực có thật, test *không trượt nữa* sẽ tự báo.

Cả `behaviour_benchmark` lẫn adapter của Lifetime Benchmark nay dùng **một**
định nghĩa phạm vi xoá. Hai định nghĩa sẽ bất đồng về phạm vi, và một phép xoá
bất đồng với chính phần kiểm chứng của nó là cách một rò rỉ sống sót qua báo
cáo sạch — thứ mà chính nhóm test này tồn tại để đo.
