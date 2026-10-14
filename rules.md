# Quy tắc phát triển hệ thống

## 1. Ngôn ngữ và nền tảng
da
- Ngôn ngữ được sử dụng trong toàn bộ hệ thống là **Python**.
- Code mới phải tương thích với phiên bản Python đã được dự án quy định trong cấu hình hoặc tài liệu khởi chạy.
- Không tự ý bổ sung ngôn ngữ lập trình, framework hoặc runtime khác nếu chưa có yêu cầu rõ ràng.

## 2. Cấu trúc Python chuẩn

- Tuân thủ cấu trúc chuẩn của một dự án Python: package rõ ràng, module có trách nhiệm cụ thể, dependency được khai báo tập trung và lệnh chạy có thể tái lập.
- Ưu tiên cấu hình bằng `pyproject.toml`; không cài dependency trực tiếp vào mã nguồn.
- Tách code ứng dụng, cấu hình, kiểm thử, dữ liệu mẫu và tài liệu theo các thư mục riêng phù hợp với kiến trúc hiện tại.
- Tuân thủ PEP 8, sử dụng type hints cho interface và các hàm quan trọng, đặt tên nhất quán và tránh module quá lớn.
- Logic nghiệp vụ phải được đặt trong module/service phù hợp; không dồn logic phức tạp vào CLI, API handler hoặc script khởi động.
- Code mới phải có test tương ứng khi bổ sung hoặc thay đổi hành vi.

## 3. Bảo toàn kiến trúc hiện tại

- Các code mới **không được làm thay đổi kiến trúc ban đầu của hệ thống**.
- Phải tận dụng các interface, module, protocol, thư mục và luồng dữ liệu hiện có trước khi tạo thành phần mới.
- Không tự ý đổi tên, di chuyển hoặc xóa module; không thay đổi API, schema Protocol 1.0, định dạng Registry hoặc contract hiện tại nếu chưa được phê duyệt.
- Khi cần mở rộng, ưu tiên adapter, implementation mới hoặc extension point để giữ tương thích ngược.
- Mọi thay đổi phải giới hạn trong phạm vi tác vụ và không làm ảnh hưởng các chức năng đang hoạt động.

## 4. Tác vụ lớn và thay đổi kiến trúc

- Nếu một tác vụ lớn yêu cầu cập nhật nhiều thành phần hoặc thay đổi nguyên hệ thống, phải dừng việc triển khai ngay tại điểm phát hiện.
- Trước khi thực hiện, cần phân tích rõ:
  - kiến trúc hiện tại và các thành phần bị ảnh hưởng;
  - lý do không thể giải quyết bằng mở rộng cục bộ;
  - phương án thay đổi và các phương án ít rủi ro hơn;
  - ảnh hưởng đến API, dữ liệu, Protocol 1.0, client, server, test và khả năng rollback.
- Phải hỏi lại và nhận xác nhận rõ ràng trước khi triển khai thay đổi lớn hoặc thay đổi kiến trúc.
- Không suy đoán rằng yêu cầu sửa một tính năng riêng lẻ đồng nghĩa với việc được phép refactor toàn hệ thống.

## 5. Kiểm tra trước khi bàn giao

- Chạy formatter/linter và test phù hợp với phạm vi thay đổi.
- Kiểm tra tương thích với các luồng hiện có, đặc biệt là Server–Client, Model Registry, Model Selector, tải chunk, P2P/fallback và inference.
- Ghi rõ các file đã thay đổi, hành vi mới, test đã chạy và mọi giới hạn còn lại.
