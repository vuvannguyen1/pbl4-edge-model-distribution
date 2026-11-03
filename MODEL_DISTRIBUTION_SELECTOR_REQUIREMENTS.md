# Đặc tả yêu cầu: Model Selector phân phối model theo tác vụ và feedback

## 1. Mục tiêu

Xây dựng một model selector duy nhất cho backend. Selector không trực tiếp nhận diện vật
thể hoặc phân loại ảnh; selector quyết định **model artifact nào cần gửi cho client** dựa
trên:

- Tác vụ cần chạy.
- Thông số thiết bị client.
- Điều kiện mạng và deadline.
- Metadata/benchmark của các model đã được chuẩn bị sẵn.
- Kết quả vận hành thực tế và feedback của người dùng.

Các checkpoint/model nguồn được chuẩn bị trước và lưu trong model store theo nhóm tác vụ.
Database lưu catalog, metadata, benchmark, trạng thái và dữ liệu dùng để train selector.
Client chỉ nhận artifact phân phối đã được kiểm định, không tải checkpoint nguồn từ
Hugging Face/PyTorch trong lúc request.

Phạm vi ban đầu:

- Model phân phối cho tác vụ nhận diện vật thể (`object_detection`).
- Model source có thể lấy từ Hugging Face hoặc PyTorch.
- Model artifact phân phối ưu tiên ONNX để tương thích với ONNX Runtime hiện tại.
- Model selector được train từ dữ liệu synthetic hiện có, sau đó cập nhật theo feedback.
- Bình luận người dùng được phân loại bằng pipeline NLP PhoBERT 5 nhãn theo tài liệu
  [PhoBERT_5Class_Vietnamese_Sentiment_Pipeline.md](./PhoBERT_5Class_Vietnamese_Sentiment_Pipeline.md).

## 2. Nguyên tắc kiến trúc

### 2.1. Tách model selector và model inference

Hệ thống có hai loại model:

1. **Model selector:** nhận feature của client và catalog model, trả về `variant_id`.
2. **Model inference:** model object detection được gửi tới client và chạy trên ảnh/video.

Feedback cảm xúc không được dùng trực tiếp như nhãn object detection. Feedback chỉ tạo
ra tín hiệu thưởng/phạt cho selector. Nếu người dùng báo nhận diện sai, đó là tín hiệu
để đánh giá lại model/variant và selector; không tự động biến bình luận thành nhãn ảnh
khi chưa có ảnh và ground-truth label.

### 2.2. Database là nguồn sự thật của catalog

Database là nguồn sự thật runtime cho:

- Model family.
- Model variant.
- Artifact và checksum.
- Chunk metadata.
- Trạng thái phát hành.
- Benchmark.
- Feedback và lịch sử chọn model.
- Trọng số/utility của selector.

Registry JSON hiện tại có thể được giữ để tương thích hoặc export, nhưng không được là
nguồn quyết định thứ hai khi selector đã chuyển sang database. Một variant bị
`disabled`, `retired` hoặc chưa `ready` trong database không được chọn hoặc phân phối.

### 2.3. Binary nằm trong filesystem, metadata nằm trong database

Không lưu file model lớn dạng BLOB trong SQLite/PostgreSQL cho luồng stream chính.
Binary được lưu tại model store; database lưu đường dẫn tương đối, kích thước, hash và
chunk hash. Cách này giữ được stream chunk, kiểm tra toàn vẹn và P2P hiện tại.

## 3. Model store và cấu trúc thư mục

### 3.1. Quy tắc lưu trữ

Model được lưu theo **task group/domain**, không theo tên task cụ thể:

```text
models\
├── CV\
│   ├── source\
│   │   ├── yolo_nano\
│   │   │   └── model.safetensors
│   │   └── yolo_small\
│   │       └── model.pt
│   ├── artifacts\
│   │   ├── yolo_nano_int8.onnx
│   │   └── yolo_small_fp16.onnx
│   └── staging\
├── NLP\
├── Audio\
└── TimeSeries\
```

Tác vụ được lưu trong database, ví dụ:

```text
task_group    = CV
task_id       = object_detection
storage_path  = CV/artifacts/yolo_nano_int8.onnx
```

`storage_path` luôn là đường dẫn tương đối POSIX dùng trong database, dù hệ điều hành
lưu file là Windows. Khi truy cập filesystem, backend phải resolve path an toàn bên
trong `PBL4_MODEL_STORE` và chặn path traversal.

### 3.2. Source checkpoint và artifact phân phối

Checkpoint nguồn từ Hugging Face/PyTorch được lưu để reproducibility/export, ví dụ
`.pt`, `.pth`, `.bin` hoặc `.safetensors`. Client không nhận các file này.

Artifact phân phối là bản đã:

1. Load thành công bằng framework nguồn.
2. Export sang ONNX hoặc runtime format đã được hỗ trợ.
3. Kiểm tra input/output contract của object detection.
4. Benchmark trên validation set.
5. Tính SHA-256 toàn file và từng chunk.
6. Đạt các quality gate.

## 4. Quy trình ingest model

### 4.1. Trạng thái artifact

Artifact phải đi qua state machine:

```text
registered
  -> downloading
  -> downloaded
  -> converting
  -> validating
  -> benchmarking
  -> ready
```

Nhánh lỗi:

```text
downloading/converting/validating/benchmarking -> failed
ready -> disabled -> retired
```

Chỉ `ready` mới là ứng viên selector. `failed` phải có error code/message và không được
được chọn như fallback.

### 4.2. Dữ liệu source cần lưu

Mỗi checkpoint source phải có:

- `source_provider`: `huggingface`, `pytorch`, hoặc provider khác.
- `source_model_id`.
- `source_revision` hoặc commit hash.
- `source_url` nếu có.
- `source_filename`.
- `source_format`.
- License và thông tin provenance.
- Thời điểm tải.
- SHA-256 checkpoint nguồn.

Không tải lại từ external provider trong API request của client. Việc tải source là job
quản trị/offline có retry, timeout, checksum và log riêng.

### 4.3. Artifact object detection

Mỗi variant phân phối phải mô tả:

- `variant_id`.
- `model_id`.
- `task_id = object_detection`.
- `task_group = CV`.
- `version`.
- `precision`: `fp32`, `fp16`, `int8`.
- `format`: `onnx` hoặc format đã được runtime hỗ trợ.
- `input_shape`, `input_dtype`, preprocessing.
- `output_contract`: bounding boxes, class IDs, confidence scores.
- `class_names` hoặc class-map version.
- `mAP50`, `mAP50_95`, precision, recall.
- `size_bytes`.
- `peak_ram_mb`.
- `latency_ms_p50`, `latency_ms_p95`, `fps`.
- `storage_path`.
- `sha256`, `chunk_size`, `chunk_count`.
- `supported_platforms`, `supported_architectures`, `supported_runtimes`.
- `status`.

Không dùng `top1_accuracy` làm metric chính cho object detection. Có thể giữ field
legacy để tương thích, nhưng selector mới phải dùng mAP/latency/RAM/size.

## 5. Mô hình dữ liệu database

### 5.1. Bảng catalog

Các bảng hiện có trong `server/db/models.py` được dùng làm nền:

- `tasks`: `task_id`, `name`, `description`, input/output contract.
- `models`: model family và quan hệ với task.
- `model_variants`: version, precision, status, quality metadata.
- `model_artifacts`: filename, storage path, size, hash, chunk count.
- `model_chunks`: index, offset, size, chunk hash.

Các trường cần bổ sung hoặc biểu diễn trong model card JSON:

```text
task_group
source_provider
source_model_id
source_revision
source_format
distribution_format
input_contract
output_contract
class_map_version
map50
map50_95
precision_score
recall_score
peak_ram_mb
latency_ms_p50
latency_ms_p95
fps
supported_platforms
supported_architectures
supported_runtimes
```

Có thể lưu các trường mở rộng trong JSON metadata ở giai đoạn đầu, nhưng các trường
được selector filter/sort thường xuyên nên được chuẩn hóa thành cột hoặc bảng
benchmark riêng khi triển khai production.

### 5.2. Feature của request selector

Mỗi request `/api/request` cần có hoặc suy ra các feature:

```text
task_id
platform
cpu_arch
ram_mb
bandwidth_kbps
deadline_s
free_storage_mb
runtime
```

Các feature tối thiểu bắt buộc cho selector:

- `platform`
- `cpu_arch`
- `ram_mb`
- `bandwidth_kbps`
- `deadline_s`
- `free_storage_mb`

Feature có thể mở rộng:

- camera resolution.
- expected FPS.
- power/battery mode.
- accelerator availability.
- accuracy requirement.
- network type.

### 5.3. Bảng dữ liệu huấn luyện và trọng số

Nên có các khái niệm dữ liệu sau:

1. `selector_training_samples`: snapshot feature request, các variant đã đánh giá,
   utility từng variant, nhãn variant tốt nhất, nguồn dữ liệu và version dataset.
2. `selector_feedback_events`: feedback đã chuẩn hóa, sentiment score, variant,
   request và tín hiệu thưởng/phạt.
3. `selector_weights`: trọng số phiên bản hiện hành, scope áp dụng, version, status,
   created_at và lý do thay đổi.
4. `selector_training_runs`: dataset version, model artifact, metrics, seed, thời điểm,
   trạng thái approval.

Không cập nhật trực tiếp file model selector production sau từng feedback. Feedback
được ghi nhận trước, aggregate theo batch, sau đó tạo version trọng số/model mới.

## 6. Dữ liệu train ban đầu từ synthetic dataset

### 6.1. Nguồn hiện tại

Dữ liệu synthetic hiện tại được sinh bởi
[selector/generate_dataset.py](./selector/generate_dataset.py) và lưu tại
[selector/training_data/selector_synthetic_dataset.csv](./selector/training_data/selector_synthetic_dataset.csv).

Pipeline hiện tại mô phỏng:

- platform.
- cpu_arch.
- RAM.
- bandwidth.
- deadline.
- free storage.
- thông số model cố định.

Sau đó áp hard constraints và tính utility để chọn `selected_variant`. Đây là baseline
phù hợp để khởi động nhưng không được coi là ground truth production.

### 6.2. Chuyển synthetic sang object detection

Synthetic generator mới phải lấy catalog `status=ready` từ database hoặc một snapshot
catalog đã version hóa, không hard-code bốn model classification. Mỗi record phải:

1. Sinh một cấu hình client hợp lệ.
2. Lấy benchmark của toàn bộ variant cùng `task_id`.
3. Loại variant vi phạm RAM, storage, runtime, deadline hoặc platform.
4. Tính download time:

```text
download_time_s = size_bytes * 8 / (bandwidth_kbps * 1000)
```

5. Tính tổng thời gian gồm download, load và warm-up nếu cần.
6. Tính utility theo policy hiện hành.
7. Chọn variant có utility cao nhất.
8. Ghi cả candidate scores và rejection reasons, không chỉ ghi nhãn cuối.

Schema CSV/Parquet tối thiểu:

```text
sample_id
task_id
platform
cpu_arch
ram_mb
bandwidth_kbps
deadline_s
free_storage_mb
variant_id
model_size_bytes
map50
map50_95
latency_ms_p50
peak_ram_mb
estimated_download_s
utility_score
eligible
selected_variant
label_source
dataset_version
```

Một dòng có thể là một cặp request-variant; khi train classifier cần group theo
`sample_id` để không làm leakage giữa candidates của cùng một request.

### 6.3. Utility baseline

Policy ban đầu cần có hard constraints trước soft score:

```text
eligible =
    free_storage_mb đủ
    AND ram_mb * ram_safety_ratio >= peak_ram_mb
    AND estimated_download_s + load_time_s <= deadline_s
    AND runtime/platform/architecture tương thích
```

Ví dụ utility:

```text
utility =
    w_map * normalized_map50_95
  - w_latency * normalized_latency
  - w_download * normalized_download_time
  - w_memory * normalized_peak_ram
  - w_size * normalized_model_size
```

Các `w_*` phải được lưu theo policy version. Khi không có candidate eligible, selector
trả `NO_VARIANT_FITS`, không tạo nhãn giả.

## 7. Train selector

### 7.1. Baseline

Model baseline vẫn có thể là pipeline tabular hiện tại:

- One-hot encode `platform`, `cpu_arch`, runtime.
- Numeric features gồm RAM, bandwidth, deadline, storage và candidate metrics.
- Classifier/ranker trả về `variant_id`.

Tuy nhiên, khi catalog có nhiều model và mỗi request có tập candidate khác nhau, nên
ưu tiên **learning-to-rank hoặc utility regression**:

1. Dự đoán utility của từng candidate.
2. Loại candidate không hợp lệ trước.
3. Chọn candidate có utility dự đoán cao nhất.

Cách này scale tốt hơn classifier cố định theo danh sách variant.

### 7.2. Fallback và safety

ML selector phải được bọc bởi rule-based selector:

```text
hard filters
    -> ML ranker
    -> validate variant_id thuộc candidate set
    -> fallback RuleBasedSelector nếu lỗi/không hợp lệ
```

ML selector không được:

- Chọn variant không có trong database.
- Chọn variant chưa `ready`.
- Bỏ qua hard constraints.
- Tự tải model source từ external provider.
- Trả success-shaped response khi không có candidate.

### 7.3. Metrics selector

Đánh giá trên request-level split, không split tùy tiện các candidate cùng request:

- Top-1 selection accuracy so với utility oracle.
- Regret: utility oracle trừ utility model chọn.
- Constraint violation rate, bắt buộc bằng 0 sau hard filter.
- NO_VARIANT_FITS precision/recall.
- Download deadline success rate.
- P95 selection latency.
- Performance theo platform, architecture, RAM và bandwidth.

Chỉ promote model selector nếu vượt rule baseline trên test cố định và không vi phạm
quality/safety gate.

## 8. Pipeline feedback và NLP 5 nhãn

### 8.1. Nguồn feedback

Feedback người dùng hiện có thể đến từ:

- `POST /api/feedback` với manual feedback.
- `POST /api/user-feedback`.
- Bảng `user_feedbacks`.
- JSONL tương thích ngược trong `data/`.

Feedback cần lưu raw text, không ghi đè nội dung gốc sau normalization. Các trường
liên quan selector:

```text
feedback_id
request_id
device_id
variant_id
task_id
raw_comment
rating
is_correct
actual_label
sentiment_score
sentiment_label
sentiment_confidence
annotation_source
model_version
created_at
```

### 8.2. NLP classifier

Pipeline NLP dùng `vinai/phobert-base-v2` fine-tune 5 lớp theo tài liệu
[PhoBERT_5Class_Vietnamese_Sentiment_Pipeline.md](./PhoBERT_5Class_Vietnamese_Sentiment_Pipeline.md).

Nhãn chuẩn:

| Score | Nhãn | Ý nghĩa |
|---:|---|---|
| 1 | Rất tiêu cực | Không dùng được, lỗi nặng, cực kỳ thất vọng |
| 2 | Tiêu cực | Chậm, sai, kém, không hài lòng |
| 3 | Trung tính | Bình thường, tạm ổn, không rõ tích cực/tiêu cực |
| 4 | Tích cực | Hài lòng, chạy tốt, đáp ứng yêu cầu |
| 5 | Rất tích cực | Xuất sắc, vượt kỳ vọng, cực kỳ hài lòng |

Inference phải trả về theo kiến trúc response hiện tại, không phá vỡ protocol:

```json
{
  "score": 4,
  "label": "Tích cực",
  "confidence": 0.87,
  "probabilities": {
    "1": 0.01,
    "2": 0.04,
    "3": 0.08,
    "4": 0.87,
    "5": 0.00
  },
  "expected_score": 3.93,
  "engine": "phobert-v1"
}
```

Các field cũ `score` và `label` phải tiếp tục tồn tại. Field mới là additive. Nếu model
NLP không khả dụng, hệ thống dùng lexicon fallback hiện tại và ghi rõ `engine`/confidence;
không giả mạo kết quả PhoBERT.

### 8.3. Tiền xử lý tiếng Việt

Tuân thủ pipeline tài liệu PhoBERT:

- Unicode NFC.
- Chuẩn hóa nhẹ.
- Giữ phủ định, emoji và tín hiệu cảm xúc.
- Không xóa tùy tiện teencode/viết tắt.
- Word segmentation bằng RDRSegmenter/VnCoreNLP.
- Dùng cùng preprocessing cho train và inference.

Model NLP phải được version hóa cùng tokenizer, segmenter, label mapping và
normalization config.

## 9. Cơ chế thưởng/phạt selector từ feedback

### 9.1. Quy tắc ba mức

Score sentiment sau khi phân loại:

- `1`, `2`: phạt.
- `3`: không thay đổi.
- `4`, `5`: thưởng.

Rating thủ công, nếu có, là tín hiệu bổ sung. Cần lưu cả sentiment do NLP dự đoán và
rating do người dùng nhập; không âm thầm coi hai giá trị này là một nếu chúng mâu thuẫn.

### 9.2. Trọng số theo variant

Mỗi variant có trọng số/policy score theo scope:

```text
weight(variant_id, task_id, device_context)
```

Update tối thiểu:

```text
sentiment 1/2:
    weight_new = weight_old - penalty

sentiment 3:
    weight_new = weight_old

sentiment 4/5:
    weight_new = weight_old + reward
```

Không cập nhật vô hạn. Bắt buộc:

- Clamp min/max.
- Learning rate nhỏ.
- Có decay theo thời gian nếu cần.
- Ghi audit event cho từng update.
- Dùng batch aggregate thay vì update trực tiếp từng request trong đường nóng.

Ví dụ policy:

```text
reward(4) = +0.25
reward(5) = +0.50
penalty(2) = -0.25
penalty(1) = -0.50
neutral(3) = 0
```

Đây chỉ là giá trị khởi đầu; phải hiệu chỉnh trên validation/offline replay.

### 9.3. Không thưởng/phạt sai nguyên nhân

Feedback phải được liên kết với `variant_id`, `request_id`, task và context. Không phạt
variant nếu bình luận chỉ phàn nàn về:

- Mạng chậm nhưng model inference tốt.
- Ảnh không hợp lệ hoặc ngoài class-map.
- Client hết dung lượng do nguyên nhân khác.
- Lỗi server/peer không thuộc chất lượng model.

Nên trích xuất reason code từ feedback:

```text
model_too_slow
wrong_detection
download_too_slow
high_memory
crash
good_accuracy
good_performance
other
```

Nếu không xác định được reason, chỉ dùng feedback ở mức tín hiệu yếu hoặc đưa vào
review queue. Score 3 tuyệt đối không thay đổi trọng số.

### 9.4. Xử lý feedback mâu thuẫn

Nếu rating và NLP score khác nhau:

- Lưu cả hai.
- Đánh dấu `label_conflict`.
- Không update trọng số tự động nếu chênh lệch vượt ngưỡng, ví dụ từ 2 mức trở lên.
- Đưa vào human review hoặc dùng rating có xác nhận theo policy.

Nếu confidence NLP thấp:

- Không áp dụng thưởng/phạt trực tiếp.
- Đưa vào review queue.
- Có thể dùng chỉ để thống kê drift.

## 10. Cập nhật model selector

### 10.1. Không online-retrain trực tiếp trong request

API request chỉ:

1. Query candidate từ database.
2. Chạy selector version hiện tại.
3. Ghi request/decision.
4. Trả model plan.

API request không được:

- Fine-tune PhoBERT.
- Train lại selector.
- Ghi đè artifact production.
- Thay trọng số toàn cục không có lock/version.

### 10.2. Batch update

Job cập nhật định kỳ:

1. Đọc feedback đã deduplicate.
2. Chạy NLP classifier/version hiện hành.
3. Lọc feedback đủ confidence và reason hợp lệ.
4. Aggregate reward/penalty theo variant, task và context bucket.
5. Cập nhật weight version mới.
6. Replay trên dataset synthetic + telemetry thật.
7. Đánh giá metrics và safety gates.
8. Promote nếu đạt.

### 10.3. Version và rollback

Mỗi lần update phải tạo:

- `selector_version`.
- `weight_version`.
- `nlp_model_version`.
- `dataset_version`.
- `policy_version`.
- metrics.
- timestamp.
- actor/job id.

Nếu regret, deadline success rate hoặc constraint violation xấu đi, rollback về
weight/model version trước. Không xóa version cũ trong thời gian audit.

## 11. Runtime flow

### 11.1. Nạp catalog

```text
Database
  -> SELECT variants
  WHERE task_id = request.task
    AND status = 'ready'
  -> join artifacts/chunks/benchmark
```

### 11.2. Chọn model

```text
request client
  -> validate device/context
  -> hard filters
  -> load selector model + current weights
  -> score candidates
  -> validate selected variant
  -> build distribution plan
  -> ghi request/decision vào DB
  -> stream artifact từ models\<task_group>
```

### 11.3. Download và inference

Client nhận artifact, kiểm SHA-256/chunk hash, lưu cache và chạy object detection.
Client gửi lại:

- Download/load/inference metrics.
- FPS/latency/RAM.
- Detection feedback nếu có.
- User comment/rating.

Server giữ logic P2P/chunk hiện tại, nhưng metadata chunk phải nhất quán với artifact
được lấy từ database.

## 12. API/contract tương thích hiện tại

Không phá vỡ các field hiện có trong protocol 1.0:

- `variant.id`, `file`, `size_bytes`, `sha256`, chunk fields.
- `request_id`.
- `reason`.
- `estimated_download_s`.
- `plan`.
- Feedback `score`, `label`, `flag_for_retrain`.

Có thể bổ sung:

- `selector_version`.
- `weight_version`.
- `task_id`.
- `model_format`.
- `sentiment_confidence`.
- `feedback_reason_codes`.

Field bổ sung phải là additive và không làm client cũ không parse được response.

## 13. Quality gates

### 13.1. Gate model artifact

- Checkpoint nguồn tải đủ và hash đúng.
- Artifact mở được bằng runtime.
- Input/output đúng object detection contract.
- Class-map version khớp task.
- Benchmark có đủ mAP, latency, RAM, size.
- Hash toàn file/chunk đúng.
- Path nằm trong model store.
- License/provenance được ghi nhận.

### 13.2. Gate NLP

- Macro-F1 trên test cố định đạt ngưỡng đã thống nhất.
- Có F1 từng nhãn.
- Có MAE và QWK.
- Không có leakage.
- Test gồm phủ định, mỉa mai, teencode, viết tắt, emoji.
- Có fallback rõ ràng và `engine` chính xác.

### 13.3. Gate selector

- Constraint violation rate bằng 0 sau filter.
- Regret không xấu hơn rule baseline quá ngưỡng cho phép.
- Deadline success rate không giảm.
- Có test cho `NO_VARIANT_FITS`.
- Model không chọn artifact disabled/retired.
- Feedback score 3 không làm thay đổi weight.
- Feedback 1/2 và 4/5 tạo đúng delta sau clamp.
- Update có thể rollback.

## 14. Test cases bắt buộc

### Catalog và storage

1. Model trong `models\CV\artifacts` được resolve đúng từ `CV/artifacts/...`.
2. Model ở `models\NLP` không xuất hiện trong query `object_detection`.
3. Path traversal bị từ chối.
4. Artifact thiếu hoặc hash sai không ở trạng thái `ready`.
5. Thay đổi status trong DB ảnh hưởng ngay đến candidate query.

### Selector

1. RAM không đủ: loại candidate.
2. Storage không đủ: loại candidate.
3. Deadline không đủ: loại candidate.
4. Chỉ chọn candidate đúng task/runtime.
5. ML trả ID lạ: fallback rule.
6. Không có candidate: `NO_VARIANT_FITS`.
7. Candidate set thay đổi vẫn chọn được theo variant ID động.

### Feedback

1. Score 1 giảm weight đúng mức.
2. Score 2 giảm weight đúng mức.
3. Score 3 không thay đổi weight byte-for-byte hoặc theo decimal chính xác.
4. Score 4 tăng weight đúng mức.
5. Score 5 tăng weight đúng mức.
6. Duplicate `feedback_id` không update hai lần.
7. Confidence thấp không update tự động.
8. Rating/NLP conflict được đánh dấu.
9. Update có audit log và version.
10. Rollback phục hồi đúng weight trước đó.

### NLP

1. Bình luận tiếng Việt có dấu.
2. Bình luận không dấu.
3. Viết tắt/teencode.
4. Emoji và kéo dài ký tự.
5. Phủ định.
6. Mỉa mai hoặc câu hỗn hợp.
7. Input rỗng.
8. PhoBERT lỗi hoặc thiếu artifact: fallback lexicon có engine rõ ràng.

## 15. Lộ trình triển khai

### Phase 1: Catalog và model store

1. Chuẩn hóa `models\CV`, `source`, `artifacts`, `staging`.
2. Đăng ký metadata source/artifact vào database.
3. Tạo object detection model cards.
4. Ingest và validate checkpoint.
5. Lưu chunk/hash và benchmark.

### Phase 2: Selector từ DB

1. Query candidate `ready` từ database.
2. Chuyển DB record sang domain candidate.
3. Giữ hard filters và rule fallback.
4. Chạy selector trên synthetic dataset lấy từ catalog DB.
5. Ghi decision/feature/utility vào DB.

### Phase 3: NLP feedback

1. Fine-tune PhoBERT 5 nhãn theo tài liệu nguồn.
2. Đóng gói tokenizer/segmenter/model version.
3. Tích hợp output additive vào handler feedback hiện tại.
4. Lưu raw comment, score, label, confidence và engine.
5. Thêm dedup/conflict/review queue.

### Phase 4: Reward/penalty

1. Implement policy version có clamp.
2. Chạy batch aggregate từ feedback.
3. Xác minh score 3 không đổi.
4. Replay trên dữ liệu synthetic và telemetry.
5. Promote weight version mới khi đạt gate.

### Phase 5: Production hardening

1. Thêm audit/rollback.
2. Theo dõi drift và phân phối sentiment.
3. So sánh selector ML với rule baseline.
4. Bật ML selector theo feature flag.
5. Chỉ giữ ML làm primary khi safety gate ổn định.

## 16. Tiêu chí hoàn thành

Yêu cầu được xem là hoàn thành khi:

- Checkpoint/model artifact được lưu dưới `models\<task_group>`, hiện tại là `models\CV`.
- Database là nguồn catalog runtime cho model ready và metadata.
- Selector query database, không phụ thuộc registry JSON để quyết định candidate.
- Synthetic data được sinh từ catalog object detection thực tế, không hard-code
  classification model.
- Selector giữ hard constraints và rule fallback.
- Feedback được deduplicate, lưu raw và được phân loại 5 nhãn bằng pipeline NLP.
- Score 1/2 phạt, score 3 không đổi, score 4/5 thưởng; tất cả update có clamp, version,
  audit và rollback.
- Feedback confidence thấp hoặc mâu thuẫn không tự động làm thay đổi trọng số.
- Output NLP giữ `score`/`label` tương thích kiến trúc hiện tại và bổ sung confidence,
  probabilities, engine/version.
- Client vẫn nhận artifact/chunk theo protocol hiện tại.
- Có test và metrics chứng minh artifact, selector, NLP và feedback loop hoạt động đúng.

## 17. Quyết định thiết kế cần chốt trước khi code

Các giá trị sau cần được quyết định bằng cấu hình, không hard-code trong logic:

1. Danh sách detector object detection ban đầu.
2. Runtime client: ONNX Runtime hay runtime khác.
3. Dataset/benchmark dùng để đo mAP.
4. Utility weights và safety ratio.
5. Reward/penalty cụ thể cho score 1, 2, 4, 5.
6. Confidence threshold để tự động update.
7. Batch schedule cho weight update.
8. Database production: SQLite cho single-server hay PostgreSQL cho scale-out.
9. Cách duyệt feedback mâu thuẫn/mỉa mai.
10. License được phép của checkpoint, dataset và PhoBERT.
