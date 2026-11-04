# File mẫu giao thức 1.0 (bài toán phát hiện vật thể)

Kịch bản: `phoneB` (Android, 6 GB RAM) vào mạng sau `phoneA`. Hai máy cùng lớp `android_arm64_4-8gb`, `phoneA` đã có đủ `yolox_nano` (4 miếng), nên `phoneB` lấy 2 miếng từ `phoneA` và 2 miếng từ server.

| File | Tin nhắn |
|---|---|
| 01, 02 | Đăng ký: `POST /api/register` |
| 03, 04 | Xin model: `POST /api/request` |
| 05, 06 | Báo tiến độ: `POST /api/have` |
| 07, 08 | Phản hồi: `POST /api/feedback` |
| 09 | Lỗi |
| 10, 11 | Đánh giá của người dùng: `POST /api/feedback` có `feedback_id` |

Lưu ý:

- Mã SHA-256 **cả file** trong 04 là mã thật của `yolox_nano.onnx`. Mã của **từng miếng** (`aaaa…`, `bbbb…`) là giá trị giả cho dễ đọc; khi chạy thật, server lấy từ `registry/model_registry.json`.
- Khối `variant` trong 04 có thêm các trường tuỳ chọn cho phát hiện vật thể (`task`, `map50_95`, `gflops`, `runtimes`, `detection`). Client 1.0 cũ bỏ qua được; `top1_accuracy` = 0 vì model phát hiện vật thể không có top-1.
- Trình duyệt (trang `/app`) còn gửi thêm `capabilities` khi đăng ký và `mode` khi xin model; peer của trình duyệt có URL dạng `webrtc://<device_id>/chunks/...` (xem `docs/protocol.md`, mục Phát hiện vật thể).
- Địa chỉ IP theo hotspot của laptop Windows (`192.168.137.1`). Khi dùng Wi-Fi khác, IP sẽ khác.
- Các số đo trong 07 là số minh hoạ.
