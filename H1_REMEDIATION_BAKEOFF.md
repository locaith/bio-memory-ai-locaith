# H1 REMEDIATION BAKE-OFF — thiết kế, chưa vá

Ba hợp đồng dựng thành wrapper quanh `ProjectionOutbox`. **Không một dòng
`bio_agent_os/` nào bị sửa.** Chạy lại bằng
`experiments/h1_remediation_bakeoff.py`.

## Hai lỗi đo bị bắt trước khi vào bảng

**Lần 1 — không có gì chạy.** Bảng đầu tiên cho bốn hợp đồng giống hệt nhau ở
mọi trục. Cột `deliveries/failures/abandons` là `0/0/0` khắp nơi: đồng hồ ảo
bắt đầu ở 2 000 trong khi `available_at` vẫn là `time.time()` ≈ 1.78e9, nên
không job nào từng được claim. `NO EXECUTION != ZERO PERFORMANCE`.

**Lần 2 — trục nói dối.** `operator recovery` báo `có` trong khi chính dòng
`operator retry` chỉ đạt 1/2. Metric viết bằng `all()` trên một tập rỗng nên
trả `True` vô căn cứ. Sửa xong thì trục đảo sang `KHÔNG` — nhưng truy tiếp thì
hoá ra nó vẫn hỏi sai câu: fixture cần 6 lần chết hạ tầng, quarantine bắn ở lần
3, operator khôi phục một lần mà nguyên nhân chưa được sửa. Operator retry thật
là *đã sửa nguyên nhân rồi mới thử lại*.

Trục `false quarantine` cũng phải đổi từ "còn đang bị cách ly" sang **"đã từng
bị cách ly"**: cách ly rút lại được không xoá cái giá của nó.

## Kết quả — 8 trục, không operator, chân trời 600 vòng

| trục | BASELINE | A fairness | B quarantine | C cả hai |
|---|---|---|---|---|
| healthy-job progress | 12/18 | **18/18** | 15/18 | 15/18 |
| data-loss | 0 | 0 | 0 | 0 |
| poison isolation | 0/3 | 0/3 | **3/3** | **3/3** |
| drain convergence | 5/8 | 5/8 | **8/8** | **8/8** |
| tenant fairness | **0/2** | 2/2 | 2/2 | 2/2 |
| crash recovery | 3/3 | 3/3 | 3/3 | 3/3 |
| operator recovery | n/a | n/a | có | có |
| false quarantine | 0/8 | 0/8 | **3/8** | **3/8** |

Chân trời 40 vòng cho A = 16/18; ở 200 và 600 vòng thành 18/18. **A chậm, không
mất.** B/C đứng yên ở 15/18 ở mọi chân trời: ba job lành bị cách ly nằm đó cho
tới khi có người. Đó là khác biệt về *loại*, không phải về *mức*.

## BASELINE hỏng ở đâu, đo được

Ba fixture có poison: `40/0/39` — cả 40 lượt giao về một job, 39 lần lease mất,
job lành `0/2`. Kể cả khi job lành thuộc **tenant khác**: `tenant fairness 0/2`.
Một job độc của `t1` chặn sạch hàng đợi của `t2`.

## Điều bất ngờ nhất: A biến starvation của poison thành starvation của churn

Ở chân trời 40 vòng, A tụt xuống dưới BASELINE đúng hai fixture:

```
worker crash chap chon   BASELINE 4/4 (14 vòng)   A 3/4 (chưa xong)
operator retry           BASELINE 2/2 ( 8 vòng)   A 1/2 (chưa xong)
```

Backoff luỹ thừa trên lease mất phạt luôn cả job chỉ gặp trục trặc hạ tầng —
đúng cảnh anh cảnh báo. BASELINE giao lại thật hung hãn, và với churn tạm thời
thì *đó lại là chiến lược tối ưu*. Việc giữ nguyên hành vi hiện tại có giá trị
thật ở đúng ca này; đừng vứt nó đi khi thiết kế bản vá.

## C không phải "tốt nhất của cả hai"

C bằng B ở **cả tám trục**. Khác biệt duy nhất là độ trễ:

```
1 poison + healthy sau   B 5 vòng   C 3 vòng
nhieu poison + healthy   B 8 vòng   C 6 vòng
hai tenant               B 5 vòng   C 3 vòng
worker crash chap chon   B 22 vòng  C 22 vòng
```

Fairness không thêm khả năng nào lên trên quarantine. Nó chỉ rút ngắn thời gian
job lành phải chờ. Đó là lý do đủ để chọn C, nhưng phải nói đúng lý do đó.

## Câu chưa trả lời được, và nó chặn việc chọn

`false quarantine 3/8` không phải hằng số. Nó là hàm của `QUARANTINE_AFTER = 3`
— một con số em tự đặt. Ngưỡng cao hơn thì ít cách ly nhầm hơn nhưng poison
sống lâu hơn. Bảng này chưa quét ngưỡng, nên **chưa chốt được B/C**.

Sâu hơn: cách ly dựa trên `abandoned_leases` đơn thuần **không phân biệt được**
hạ tầng chập chờn với payload độc. Cả hai đều hiện ra y hệt nhau — worker chết
trước khi kịp `fail()`. Muốn phân biệt thì cần tín hiệu khác (worker id khác
nhau? cùng một stack? crash toàn cục cùng lúc?), và đó là câu thiết kế chưa ai
trả lời.

## Giới hạn của chính thí nghiệm này

- ba hợp đồng là *wrapper*, không phải bản cài thật; bản thật có thể lệch
- một worker, `batch_size=1`; chưa đo nhiều worker tranh nhau
- worker chết được mô phỏng bằng "không gọi `fail()`"; crash thật có thể khác
- `QUARANTINE_AFTER=3`, backoff cơ số 2 cap 300 — đều là số em tự đặt
- operator can thiệp ở một vòng cố định, không mô hình hoá thời gian phản ứng

## Chưa đề xuất chốt

Dữ liệu nghiêng về C, nhưng chưa đủ để ký: chưa quét ngưỡng cách ly, và chưa
biết cách phân biệt churn với poison. Chưa vá một dòng product code nào.

`Activation Gate` **BLOCKED**. Worker trên store thật **NOT ACTIVATED**.
