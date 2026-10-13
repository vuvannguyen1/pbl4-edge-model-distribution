---
name: model-selector-v1
description: Thiết kế Model Selector Version 1 với candidate filtering, rule-based baseline, lý do lựa chọn và điểm mở rộng cho ML ranker.
---

# Model Selector Version 1

## Mục tiêu

Chọn artifact từ candidate list lấy động từ Registry, không hard-code bốn variant. Selector phải thay thế được mà không sửa API hoặc Download Planner.

## Pipeline bắt buộc

1. Nhận task, input/output contract, platform/architecture, runtime, RAM/storage, bandwidth/deadline và benchmark có ngữ cảnh.
2. Candidate Filter loại artifact không đúng task/contract, không tương thích runtime/platform, thiếu dung lượng hoặc không đáp ứng deadline/policy.
3. Rule-based baseline xếp hạng candidate còn lại dựa trên size, bandwidth, load/latency/RAM benchmark và yêu cầu người dùng.
4. Trả `variant_id`, score/criteria, danh sách candidate và lý do đọc được; lưu selector version và features dùng cho quyết định.
5. Không còn candidate thì trả `NO_VARIANT_FITS` đúng schema.
6. Nếu selector chính lỗi, dùng fallback selector an toàn và ghi rõ lý do.

## Ràng buộc chất lượng

- Chỉ xem artifact `ready` là candidate.
- Không dùng bytes trọng số ONNX làm input mặc định; dùng metadata và benchmark.
- Không nhầm confidence của một ảnh với accuracy model.
- Không tạo nhãn giả để tuyên bố ML ranker tốt hơn baseline.
- ML ranker chỉ được bật sau khi có context/candidate/outcome, reward hoặc label định nghĩa rõ, benchmark có kiểm soát và so sánh công bằng với baseline.

## Kết quả cần bàn giao

Interface `IModelSelector`/tương đương, rule baseline, Candidate Filter, decision record, test không phụ thuộc tên model, test `NO_VARIANT_FITS`, test fallback khi selector lỗi và cấu trúc mở rộng cho ranker về sau.

## Không thuộc skill này

Không bắt buộc huấn luyện ML Selector trong demo đầu; không quản lý SQL, dashboard hay model artifact.
