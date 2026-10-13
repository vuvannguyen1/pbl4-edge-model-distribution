---
name: distribution-rest-server
description: Xây dựng REST Server Protocol 1.0 cho đăng ký client, chọn variant, lập kế hoạch chunk, phân phối binary và nhận feedback trong Version 1.
---

# Distribution REST Server

## Mục tiêu

Triển khai Server ASP.NET Core HTTP/1.1, mặc định port `8000`, giữ đúng hợp đồng Protocol 1.0 và phục vụ artifact theo chunk mà không nạp toàn bộ model vào RAM cho mỗi request.

## Endpoint bắt buộc

- `POST /api/register`: validate schema, đăng ký/refresh client, trả `device_class`, `chunk_size`, `peer_enabled`.
- `GET /api/probe?size_kb=256`: trả đúng số byte nhị phân để đo throughput.
- `POST /api/request`: lọc candidate, gọi Selector, tạo `request_id`, trả metadata và chunk plan.
- `GET /api/chunks/{variant_id}/{index}`: stream đúng byte chunk với `application/octet-stream`.
- `POST /api/have`: nhận trạng thái chunk/hoàn tất và trả `continue` hoặc `switch` theo policy.
- `POST /api/feedback`: ghi metrics và trả `keep` hoặc `switch`.
- Lỗi dùng envelope `{"error":{"code":"...","message":"..."}}`.

## Quy tắc giao thức và an toàn

- JSON UTF-8, field `snake_case`, validate `protocol_version`, kiểu dữ liệu, ID, chunk index và trạng thái request.
- Không trả filesystem path, stack trace hoặc thông tin nội bộ cho client.
- Chỉ phân phối variant tồn tại trong Registry và ở trạng thái `ready`; kiểm tra `preferred_variant` thay vì tin client.
- Chỉ đưa peer vào plan khi server có trạng thái đã xác minh peer sở hữu artifact/chunk và peer được phép phục vụ.
- `have_variants` không được dùng như sự thật tuyệt đối nếu chưa được đối chiếu/trusted.
- Chunk mặc định 1 MiB; hash và offset phải lấy từ artifact thật.
- Ghi log có `request_id` cho register, candidate/selector decision, plan, chunk failure, fallback, completion và feedback.

## Kiến trúc

Tách controller khỏi Registry, Candidate Filter, Model Selector, Download Planner, Peer Registry và Feedback Store. Controller chỉ điều phối validation và response; không nhúng logic chọn model phức tạp.

## Kiểm thử tối thiểu

Test schema thành công/thất bại, variant không tồn tại, variant không `ready`, index ngoài phạm vi, binary chunk đúng byte, lỗi đúng envelope, stream không giữ toàn file trong RAM và log/correlation theo request.

## Không thuộc skill này

Không xây Web UI, API quản trị, SQL, authentication/RBAC hoặc thay đổi Protocol 1.0 khi chưa có versioning rõ ràng.
