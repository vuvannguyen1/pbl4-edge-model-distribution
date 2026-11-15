---
name: v1-demo-validation
description: Kiểm thử nghiệm thu và tái lập demo end-to-end Version 1 của hệ thống phân phối model AI Edge.
---

# Validation Demo Version 1

## Mục tiêu nghiệm thu

Một người có thể khởi động Server, chạy hai Client trong cùng LAN/hotspot, yêu cầu model, quan sát variant và nguồn chunk, xác minh hash, chạy inference Top-5, gửi feedback và chứng minh fallback khi peer dừng. Demo phải chạy lặp lại theo runbook.

## Kịch bản chuẩn

1. Server khởi động và nạp Registry động.
2. Client A register/request/download từ Server, xác minh file, mở peer service.
3. Client B register/request cùng variant; plan dùng ít nhất một chunk từ peer khi mạng cho phép.
4. Client B xác minh chunk và toàn file, chạy inference ảnh mẫu, in Top-5.
5. Client B gửi feedback; Server ghi nhận `keep` hoặc `switch`.
6. Tắt hoặc chặn peer; chạy lại tải chunk và chứng minh fallback về Server.

## Checklist bắt buộc

- Server health/startup rõ ràng; Registry có bốn variant và Selector không hard-code ID.
- Thêm một artifact cùng task/contract chỉ cần đăng ký/refresh, không sửa điều kiện theo tên model.
- Protocol request/response khớp schema; lỗi đúng envelope.
- Register, request, chunk, have và feedback hoạt động.
- SHA-256 từng chunk và toàn file khớp byte thật.
- ONNX Runtime load được model và trả Top-5.
- P2P hoạt động trong môi trường cho phép; nếu không, ghi rõ nguyên nhân và fallback thành công.
- Có số đo download/load/latency/RAM với điều kiện đo; không dùng số minh họa làm kết quả.
- Có script/command, cấu hình IP/port, ảnh demo, nhãn ImageNet, hướng dẫn firewall/AP isolation và kết quả test.

## Cách báo lỗi

Mỗi lỗi phải ghi `request_id`, client/device, variant, chunk index/source, bước thất bại, mã lỗi và log liên quan. Phân biệt lỗi môi trường mạng, lỗi protocol, lỗi integrity, lỗi ONNX và lỗi selector; không che lỗi bằng cách tự động bỏ qua verification.

## Ranh giới Version 1

Không dùng Web UI, SQL, upload quản trị, RBAC hoặc dashboard để đánh giá hoàn thành Version 1. Các hạng mục đó thuộc Version 2 và chỉ kiểm tra khi được yêu cầu riêng.
