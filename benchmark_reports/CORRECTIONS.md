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

### Số thay thế

Chưa có. Nhóm `forgetting` phải chạy lại bằng harness đã sửa. Cho tới lúc đó,
**không có số nào về năng lực xoá** được coi là đã đo.
