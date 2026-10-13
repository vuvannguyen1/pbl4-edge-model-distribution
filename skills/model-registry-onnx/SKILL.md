---
name: model-registry-onnx
description: Quản lý Model Store, Model Card, Registry động và kiểm định artifact ONNX cho Version 1 của hệ thống phân phối AI Edge.
---

# Model Registry và ONNX

## Mục tiêu

Xây dựng quy trình đưa artifact ONNX từ `staging/incoming/` vào Model Store và Registry JSON một cách có kiểm soát. Artifact chỉ được phân phối khi đã có metadata hợp lệ, đọc được bằng ONNX Runtime và ở trạng thái `ready`.

## Phạm vi bắt buộc

- Tổ chức file theo task, ví dụ `models/CV/<file>.onnx`; không tạo logic hoặc thư mục phụ thuộc riêng vào tên model.
- Dùng `registry/model_registry.json` và `metadata/model_cards/` làm nguồn danh mục Version 1.
- Hỗ trợ bốn artifact khởi tạo: MobileNetV2 FP32/INT8 và ResNet-50 FP32/INT8.
- Mỗi variant phải có tối thiểu `model_id`, `variant_id`, `version`, `task_id`, filename, `storage_path`, `size_bytes`, SHA-256, precision, input/output contract, preprocessing, accuracy đã xác thực và lifecycle status.
- Tự tính kích thước, SHA-256 toàn file và SHA-256 từng chunk từ byte thật; không dùng hash minh họa.
- Kiểm tra JSON/schema, extension và nội dung ONNX, tensor input/output, opset/dtype khi có thể, metadata bắt buộc và tính tương thích với contract.
- Bảo vệ `storage_path` để không vượt ra ngoài Model Store; không cho path traversal.
- Artifact mới phải bắt đầu ở `draft`/`validating`; chỉ artifact hợp lệ mới chuyển sang `ready`. Artifact lỗi hoặc thiếu metadata phải giữ lý do trong kết quả validation.
- Cập nhật Registry theo cách nguyên tử để request đang chạy không đọc danh mục dở dang.

## Nguyên tắc thiết kế

- Không hard-code danh sách bốn `variant_id` trong Server hoặc Selector.
- Phân biệt model tác vụ được tải về với Model Selector điều phối việc chọn artifact.
- Metadata không thể suy ra chắc chắn từ ONNX, như task, preprocessing, license, accuracy và yêu cầu phần cứng, phải lấy từ Model Card/sidecar và được kiểm tra.
- Hash trong Registry phải khớp artifact thực tế trước khi phát hành.

## Kết quả cần bàn giao

Registry mẫu, Model Cards, công cụ scan/refresh/validate, công cụ sinh hash chunk và test cho artifact hợp lệ, thiếu metadata, ONNX hỏng, path không an toàn và artifact không ở trạng thái `ready`.

## Không thuộc skill này

Không xây upload web, SQL registry, API quản trị, RBAC hoặc quy trình Version 2.
