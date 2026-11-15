---
name: edge-client-p2p
description: Xây dựng Python Client Agent và Peer Service cho tải model theo chunk, xác minh SHA-256, chạy fallback và chia sẻ P2P trong demo Version 1.
---

# Edge Client và P2P

## Mục tiêu

Tạo client CLI chạy được trên Windows/Linux, đăng ký với Server, nhận plan, tải artifact tối đa 4 chunk đồng thời, xác minh integrity, chạy inference và gửi feedback. Peer service dùng HTTP port mặc định `9000`.

## Luồng client

1. Đọc cấu hình Server URL, `device_id`, platform, architecture, RAM, runtime và peer port.
2. Gọi `/api/register`, tùy chọn đo bandwidth qua `/api/probe`.
3. Gửi task/context/resource constraints đến `/api/request`.
4. Tải từng chunk theo `offset`; xác minh SHA-256 trước khi ghi nhận.
5. Retry có giới hạn; khi peer lỗi, timeout hoặc hash sai, dùng `fallback_url` của Server.
6. Ghép file và xác minh SHA-256 toàn file; chỉ khi đúng mới ghi local state `complete` và gửi `/api/have`.
7. Chạy inference, đo metrics và gửi `/api/feedback`.

## Peer Service

- `GET /chunks/{variant_id}/{index}` chỉ trả chunk client thật sự có và đã xác minh.
- Trả `404` khi chưa có chunk.
- Không phục vụ chunk của variant khác, file đang tải dở hoặc artifact chưa đủ điều kiện chia sẻ.
- Inventory phải khóa theo `variant_id` và chunk index; không suy luận từ filename.
- Khi P2P bị chặn bởi firewall/AP isolation, phải báo failure rõ ràng nhưng vẫn tải được từ Server.

## Quan sát và tính đúng

Log variant được chọn, lý do, nguồn từng chunk, retry/fallback, hash verification, `download_ms`, `load_ms`, `latency_ms_median`, `runs`, `peak_ram_mb` nếu đo được và số chunk theo nguồn. Không đánh dấu hoàn tất chỉ vì đủ số chunk; hash toàn file là điều kiện cuối.

## Kiểm thử tối thiểu

Test resume/retry, chunk hash sai, file hash sai, peer 404/timeout, fallback Server, inventory không trộn variant và kịch bản hai client trong LAN/hotspot.

## Không thuộc skill này

Không xây browser/mobile client bắt buộc, Web UI quản trị, SQL hoặc thay đổi schema Protocol 1.0.
