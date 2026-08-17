# Store thật không có người nào trong tầng structured slot

**17/08/2026.** Phát hiện khi chạy protocol migration `resolver2-clause-boundary`
ở chế độ dry-run trên clone. Migration **ĐÃ DỪNG**, không áp vào store thật.

Snapshot bất biến (không được xoá):
`.bio-agent-os/snapshots/20260817T075247-pre-resolver2-clause-boundary/`
`sha256 = c52dc95300eeb34f…`, runtime fingerprint `f72693049c955549…`

---

## Số đo

```
183 hàng trong cognitive_memories
    156 (85%)  content chứa "hook="        -> telemetry của chính hệ thống
    163        không có slot
     20        có slot

20 entity đã lưu, phân theo token đầu tiên:
     16  'hook'
      4  'UserPromptSubmit'
     ─────
      0  hình dạng tên người

memory_type của cả 20: episodic
attribute đã gán:  employer 9, city 3, project 3, job_title 2,
                   salary 1, phone 1, birthday 1

backfill sẽ điền thêm 59 hàng chưa có slot, 48 trong đó entity chứa từ khoá hook
```

Hệ thống tin rằng nó biết **nơi làm việc** của một thực thể tên
`hook UserPromptSubmit prompt …`. Chín lần.

## Vì sao migration dừng

Protocol yêu cầu clone phải sạch trước khi chạm store thật. Nó không sạch, và
hai điều kiện vỡ đều đúng chứ không phải cảnh báo thừa:

1. **Identity merge.** Bản vá ranh giới mệnh đề gộp năm surface rác thành một
   surface rác. Vô hại vì cả năm đều không phải người — nhưng bộ dò không phân
   biệt được người với dòng log, và **resolver cũng không**. Đó chính là vấn đề.
2. **Migration tự quyết ambiguity.** `backfill` không chỉ suy lại 16 hàng cũ; nó
   còn điền slot mới cho 59 hàng **chưa từng có slot**. Biến "không có claim cấu
   trúc" thành "có một claim cấu trúc", suy ra từ log. `UNKNOWN` phải tiếp tục
   `UNKNOWN`.

Migration không phải "chưa an toàn". Nó **không có gì để sửa**: không có danh
tính người nào bị hỏng trên store này để phục hồi.

## Khuyết tật thật

`facade.remember` gọi `_structured_slot` trên **mọi** memory, kể cả episodic sinh
ra từ hook. Trích xuất slot chạy trên văn bản do máy sinh, và mọi thứ nó tạo ra ở
đó đều là nhiễu được thăng cấp thành cấu trúc.

Khác với D1. D1 hỏi *"tên nào trong câu là chủ thể của vị từ"*. Đây hỏi
*"câu này có nói về ai không"* — và với 85% store, câu trả lời là **không**.

Trạng thái đúng cho những hàng đó là `UNKNOWN`, không phải một chủ thể trông có
vẻ hợp lệ.

## Chưa quyết — cần chủ sở hữu

Hai câu hỏi, và cả hai là quyết định sản phẩm chứ không phải sửa lỗi:

1. Telemetry hook có nên thành memory không? Nếu có, có nên đi qua trích xuất
   slot không?
2. 20 slot rác đang có nên xoá, hay để nguyên và chỉ chặn đường sinh ra thêm?

Không tự làm. Cả hai đều là **ghi vào bộ nhớ thật**.

## Ảnh hưởng tới P0-B

Không có. `SUBJECT_IDENTITY_READ` mặc định ON vẫn đúng: hàng do resolver cũ ghi
được đọc theo đường cũ, và không có truy vấn nào hỏi những thực thể này như hỏi
về người. Nhưng nó xoá một kỳ vọng: **bật identity read trên store thật hiện
không cải thiện gì cả**, vì chưa có danh tính người nào để tách.
