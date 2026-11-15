---
name: onnx-inference-feedback
description: Triển khai inference image classification bằng ONNX Runtime và ghi metrics/feedback có ngữ cảnh cho Version 1.
---

# ONNX Inference và Feedback

## Mục tiêu

Chứng minh artifact tải về thực sự chạy được, trả Top-5 và gửi số đo vận hành để phục vụ đánh giá selector; không coi tải file thành công là inference thành công.

## Contract inference ban đầu

- Task: `image_classification`.
- Input: ảnh màu, tensor `[1, 3, 224, 224]`, dtype float.
- Preprocessing: resize cạnh ngắn về 256, center crop 224×224, normalize mean `[0.485, 0.456, 0.406]`, std `[0.229, 0.224, 0.225]`.
- Output: Top-5 nhãn ImageNet và confidence, dùng mapping đúng phiên bản dataset.
- Kiểm tra input/output thực tế của từng ONNX trước khi chạy; không giả định mọi artifact có cùng graph nếu Model Card không xác nhận.

## Đo lường

Đo `load_ms`, nhiều lần inference để lấy `latency_ms_median`, `runs`, `peak_ram_mb` nếu đo được, cùng runtime/hardware/platform và variant. Ghi rõ điều kiện đo. Phân biệt record `sample/demo` với benchmark thật; không trình bày confidence của ảnh mẫu như accuracy.

## Feedback

Mỗi record JSONL/CSV gắn `request_id`, `device_id`, `variant_id`, timestamp, task, platform/runtime, download/load/latency/RAM, nguồn chunk, trạng thái fallback và selector reason/version. Gửi `/api/feedback` và xử lý response `keep`/`switch` theo schema.

## Kiểm thử tối thiểu

Test preprocessing shape/dtype, label mapping, load từng variant khởi tạo, Top-5 có cấu trúc hợp lệ, đo median, feedback thiếu trường/lỗi schema và phân biệt sample với benchmark.

## Không thuộc skill này

Không thiết kế mô hình phân loại mới, huấn luyện selector, SQL telemetry hoặc UI dashboard.
