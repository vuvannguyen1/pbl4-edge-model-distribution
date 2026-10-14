# File mẫu giao thức 1.0

Kịch bản: `phoneB` (Android, 6 GB RAM) vào mạng sau `phoneA`. Hai máy cùng lớp `android_arm64_4-8gb`, `phoneA` đã có đủ `mobilenetv2_int8`, nên `phoneB` lấy 2 miếng từ `phoneA` và 2 miếng từ server.

| File | Tin nhắn |
|---|---|
| 01, 02 | Đăng ký: `POST /api/register` |
| 03, 04 | Xin model: `POST /api/request` |
| 05, 06 | Báo tiến độ: `POST /api/have` |
| 07, 08 | Phản hồi: `POST /api/feedback` |
| 09 | Lỗi |

Lưu ý:

- Mã SHA-256 **cả file** trong 04 là mã thật của `mobilenetv2-12-int8.onnx`. Mã của **từng miếng** (`aaaa…`, `bbbb…`) là giá trị giả cho dễ đọc; khi chạy thật, server lấy từ `models/manifest.json`.
- Địa chỉ IP theo hotspot của laptop Windows (`192.168.137.1`). Khi dùng Wi-Fi khác, IP sẽ khác.
- Các số đo trong 07 là số minh hoạ.
