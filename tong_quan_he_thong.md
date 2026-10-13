# Thiết kế tổng quan hệ thống phân phối mô hình AI Edge có khả năng mở rộng
 
**Ngày:** 13/10/2026  
**Nền tảng giao thức hiện có:** PBL4 Protocol 1.0  
**Định hướng:** Từ danh sách 4 biến thể cố định sang hệ thống quản lý danh mục model động, cho phép bổ sung các file `.onnx` mà không phải viết lại bộ chọn phân phối.

---

## 1. Mục tiêu thiết kế

Hệ thống phân phối model không nên phụ thuộc vào một danh sách model cố định trong mã nguồn. Thay vào đó, thư mục `models/` là nơi lưu các artifact có thể được phân phối; một thành phần **Model Registry** tự phát hiện, kiểm định và tạo danh mục; một **Model Selector** dùng thông tin thiết bị, mạng và hiệu năng để chọn artifact phù hợp.

Thiết kế hướng tới các yêu cầu sau:

1. Có thể thêm nhiều file `.onnx` sau này mà không cần sửa các nhánh `if/else` theo tên model.
2. Mỗi model/phiên bản có metadata, hash, kích thước, hợp đồng đầu vào/đầu ra và trạng thái phát hành riêng.
3. Bộ chọn chỉ cân nhắc các artifact tương thích với tác vụ và môi trường chạy của thiết bị.
4. Có thể dùng một mô hình ML chuyên chọn model phân phối; nếu mô hình này chưa sẵn sàng hoặc thiếu dữ liệu, hệ thống vẫn có bộ chọn theo quy tắc làm phương án dự phòng.
5. Tiếp tục hỗ trợ tải theo chunk, P2P, kiểm tra SHA-256, fallback và feedback như giao thức hiện tại.
6. Mở rộng được từ nhiều biến thể của cùng một tác vụ sang nhiều tác vụ khác nhau mà có quy trình nâng cấp giao thức rõ ràng.

**Phạm vi model:** Báo cáo chốt model hiện tại vẫn là nguồn chuẩn cho 4 artifact đang có: MobileNetV2 FP32, MobileNetV2 INT8, ResNet-50 FP32 và ResNet-50 INT8. Thiết kế Registry không giới hạn hệ thống ở bốn artifact này; nó chỉ có nghĩa là bốn artifact hiện tại được đăng ký đầu tiên. Artifact mới phải được kiểm định và đăng ký trước khi đưa vào phân phối.

## 2. Phân biệt các loại model trong hệ thống

Từ “model” có thể chỉ hai nhóm khác nhau. Cần tách chúng ngay trong kiến trúc.

### 2.1. Model tác vụ – model được phân phối

Đây là các file `.onnx` bên trong Model Store, ví dụ các biến thể MobileNetV2 và ResNet-50 đang dùng để phân loại ảnh. Thiết bị biên tải một artifact trong nhóm này và dùng nó để thực hiện tác vụ.

### 2.2. Model điều phối – model chọn artifact để phân phối

Đây là mô hình ML riêng, nếu nhóm huấn luyện để dự đoán artifact nào phù hợp nhất với một yêu cầu. Nó không phải là một trong các model phân loại ảnh được phân phối cho client.

Đầu vào phù hợp cho Model Selector thường là **metadata và số đo của các artifact ứng viên**, kết hợp với ngữ cảnh yêu cầu, chẳng hạn:

- Tác vụ mà client cần thực hiện.
- Nền tảng, kiến trúc CPU, RAM, runtime và khả năng tăng tốc của client.
- Băng thông hiện tại, dung lượng lưu trữ và thời hạn tải.
- Kích thước file, precision, input/output contract và thông số benchmark của từng artifact.
- Lịch sử thời gian tải, thời gian nạp, độ trễ suy luận, RAM và tỷ lệ hoàn thành theo thiết bị/runtime.

Không nên mặc định đưa toàn bộ byte trọng số ONNX vào đầu vào của bộ chọn. File ONNX là artifact cần quản lý; metadata và benchmark của nó mới là đặc trưng thực dụng để ra quyết định phân phối. Nếu sau này có nghiên cứu học biểu diễn trực tiếp từ cấu trúc graph ONNX, đó là một nhánh nghiên cứu riêng, không phải điều kiện cần để hệ thống mở rộng danh mục.

## 3. Kiến trúc tổng thể

```text
                  ┌────────────────────────────────────────────┐
                  │                  SERVER                    │
                  │                                            │
                  │  ┌──────────────────────────────────────┐  │
  Thêm .onnx ────►│  │ Model Ingestion + Model Registry     │  │
                  │  │ Scan / Validate / Hash / Manifest    │  │
                  │  └──────────────────┬───────────────────┘  │
                  │                     │ Candidates + metadata│
                  │  ┌──────────────────▼───────────────────┐  │
  Yêu cầu client ─► │ Request Context + Candidate Filtering │  │
                  │  └──────────────────┬───────────────────┘  │
                  │                     ▼                      │
                  │  ┌──────────────────────────────────────┐  │
                  │  │ Model Selector                       │  │
                  │  │ Rule-based baseline / ML ranker      │  │
                  │  └──────────────────┬───────────────────┘  │
                  │                     ▼                      │
                  │  ┌──────────────────────────────────────┐  │
                  │  │ Download Planner + Peer Registry     │  │
                  │  └──────────────────┬───────────────────┘  │
                  │                     │                      │
                  │  REST API + Manifest + Model Store + Logs  │
                  └──────────────┬──────┴───────────────────────┘
                                 │ JSON metadata / binary chunks
                     ┌───────────┴───────────┐
                     ▼                       ▼
               ┌───────────┐  P2P       ┌───────────┐
               │  Edge A   │◄──────────►│  Edge B   │
               │ Có chunk  │            │ Cần model│
               └─────┬─────┘            └─────┬─────┘
                     └──────────┬─────────────┘
                                │ Feedback / telemetry
                                ▼
                    ┌────────────────────────┐
                    │ Feedback & Benchmark   │
                    │ Store / Training Data  │
                    └────────────┬───────────┘
                                 │ dữ liệu đã kiểm định
                                 ▼
                       Huấn luyện/đánh giá
                       Model Selector mới
```

### 3.1. Các thành phần chính

| Thành phần | Trách nhiệm |
|---|---|
| **Model Store** | Lưu file ONNX theo ID/version; không chứa logic chọn model |
| **Model Ingestion** | Phát hiện file mới, kiểm tra định dạng, đọc metadata, tính hash và tạo bản ghi nháp |
| **Model Registry** | Danh mục chuẩn của tất cả artifact; quản lý metadata, version, trạng thái và khả năng tương thích |
| **Manifest Generator** | Sinh manifest và hash từng chunk từ artifact thực tế |
| **Candidate Filter** | Loại các artifact không phù hợp với task, runtime, input/output contract, dung lượng và deadline |
| **Model Selector** | Xếp hạng các artifact còn lại bằng quy tắc hoặc mô hình ML |
| **Download Planner** | Chia chunk và chọn nguồn `server` hoặc `peer` cho từng chunk |
| **Peer Registry** | Theo dõi client nào có artifact/chunk đã được xác minh và có thể phục vụ |
| **Feedback Store** | Lưu kết quả phân phối, benchmark, sự cố và số đo theo context |
| **Selector Training Pipeline** | Làm sạch dữ liệu feedback, tạo nhãn/reward, huấn luyện và đánh giá phiên bản selector mới |
| **Client Agent** | Đăng ký, xin artifact, tải, kiểm tra, chạy và gửi feedback |

## 4. Luồng tiếp nhận một file ONNX mới

Thêm file vào thư mục không đồng nghĩa file đó được phân phối ngay. Nên dùng luồng tiếp nhận có kiểm soát:

1. **Phát hiện:** Ingestion service phát hiện file `.onnx` mới trong vùng nhập liệu.
2. **Định danh:** Gán `model_id` cho họ model và `variant_id` duy nhất cho artifact cụ thể. Không dùng tên file làm định danh duy nhất nếu có khả năng cùng tên ở nhiều phiên bản.
3. **Đọc và kiểm định ONNX:** Kiểm tra file có mở được bằng công cụ/runtime đã hỗ trợ; đọc input/output tensor, opset, kiểu dữ liệu và các thuộc tính kỹ thuật có thể trích xuất.
4. **Khai báo metadata còn thiếu:** Thông tin như task, độ chính xác trên tập đánh giá, tiền xử lý, giấy phép, yêu cầu runtime và phần cứng có thể không xác định chắc chắn từ file ONNX. Các thông tin này phải được cung cấp trong model card/sidecar và được xác thực.
5. **Đo/đính kèm benchmark:** Đo thời gian nạp, latency, RAM và độ ổn định trên các nhóm thiết bị/runtime cần hỗ trợ. Nếu chưa benchmark trên một lớp thiết bị, đánh dấu chưa biết thay vì giả định.
6. **Tạo hash:** Tính SHA-256 toàn file và từng chunk từ chính byte của artifact.
7. **Kiểm tra chính sách:** Xác thực schema metadata, kích thước, đầu vào/đầu ra, khả năng tương thích, license và tiêu chí triển khai.
8. **Đưa vào trạng thái `ready`:** Chỉ artifact đã qua kiểm định mới được Candidate Filter xem xét.
9. **Cập nhật cache và danh mục:** Cập nhật Registry/manifest theo cách nguyên tử để request đang chạy không đọc phải danh mục dở dang.

Trạng thái nên hỗ trợ tối thiểu: `draft`, `validating`, `ready`, `disabled`, `retired`, `validation_failed`. Mặc định file mới là `draft` hoặc `validating`, không được phân phối tự động trước khi kiểm định xong.

## 5. Cấu trúc thư mục: phân chia theo tác vụ

Thư mục `models/` được chia **theo nhóm tác vụ**, không chia thành thư mục riêng cho từng họ model hoặc từng model. Mỗi file ONNX nằm trực tiếp trong thư mục tác vụ phù hợp. Ví dụ `models/CV/ResNet50.onnx`.

```text
project/
├── models/                          # Chỉ chứa artifact model đã được phát hành, nhóm theo tác vụ
│   ├── CV/                          # Computer Vision
│   │   ├── MobileNetV2_FP32.onnx
│   │   ├── MobileNetV2_INT8.onnx
│   │   ├── ResNet50_FP32.onnx
│   │   └── ResNet50_INT8.onnx
│   ├── NLP/                         # Dành cho tác vụ NLP trong tương lai
│   ├── Audio/                       # Dành cho tác vụ âm thanh trong tương lai
│   └── TimeSeries/                  # Dành cho dữ liệu chuỗi thời gian trong tương lai
├── staging/
│   └── incoming/                    # File mới chờ kiểm định; không được phân phối trực tiếp
├── metadata/
│   └── model_cards/                 # Mỗi variant có một Model Card riêng, đặt tên theo variant_id
├── registry/
│   └── model_registry.json          # Registry dạng file cho phiên bản demo; V2 chuyển sang SQL
├── benchmarks/
│   ├── raw/
│   └── aggregated/
├── selector/
│   ├── rules/                       # Bộ chọn quy tắc dự phòng
│   ├── training_data/
│   ├── artifacts/                   # Artifact của Model Selector, tách khỏi models tác vụ
│   └── evaluations/
├── server/
├── client/
├── tests/
└── docs/
    ├── system_overview.md
    ├── protocol.md
    ├── protocol.schema.json
    └── examples/
```

### 5.0.1. Quy ước đường dẫn và định danh

- `CV`, `NLP`, `Audio`, `TimeSeries` là **nhóm thư mục/domain**; trường `task.id` mới là ID tác vụ cụ thể. Ví dụ, `CV` có thể chứa `image_classification` trong phiên bản đầu tiên.
- Tên file nên thể hiện họ model và precision, ví dụ `ResNet50_FP32.onnx`. Không dùng tên file làm định danh duy nhất; mỗi artifact vẫn cần `variant_id` ổn định, ví dụ `resnet50_fp32`.
- Registry giữ `storage_path` tương đối với gốc Model Store, chẳng hạn `CV/ResNet50_FP32.onnx`. Server phải ngăn path traversal và chỉ cho đọc file bên trong vùng lưu trữ được phép.
- Với API Protocol 1.0, trường `variant.file` trả về tên file ONNX để client lưu cục bộ, không tiết lộ đường dẫn nội bộ của server. Trường `storage_path` là metadata nội bộ và không được thêm tùy tiện vào JSON 1.0.
- Model Card được lưu trong `metadata/model_cards/` theo `variant_id`, không tạo thư mục con cho từng họ model trong `models/`.
- File mới được tải lên vùng `staging/incoming/`; chỉ sau khi xác thực, hash và metadata hợp lệ mới được chuyển nguyên tử sang `models/<task_group>/` và đánh dấu `ready`.
- Hai artifact không được ghi đè lên nhau chỉ vì trùng tên file. Cần kiểm tra xung đột `variant_id`, phiên bản và đường dẫn.

Cấu trúc này giúp thêm một artifact vào tác vụ đang có chỉ cần thêm file và bản ghi Registry. Khi triển khai tác vụ mới, tạo thư mục cấp tác vụ mới; không cần tái cấu trúc thư mục theo tên model.

### 5.1. Ví dụ `model_card.json`

Ví dụ sau minh họa cấu trúc đề xuất cho một artifact. Những giá trị về model hiện tại phải lấy từ báo cáo/model thật; ví dụ này không thay thế hash thực tế.

```json
{
  "schema_version": "1.0",
  "model_id": "mobilenetv2",
  "variant_id": "mobilenetv2_int8_v1",
  "version": "1",
  "artifact": {
    "file": "CV/MobileNetV2_INT8.onnx",
    "format": "onnx",
    "size_bytes": null,
    "sha256": null
  },
  "task": {
    "id": "image_classification",
    "labels": "imagenet_1k"
  },
  "model": {
    "family": "MobileNetV2",
    "precision": "int8",
    "compression_level": 1
  },
  "contract": {
    "input_shape": [1, 3, 224, 224],
    "input_dtype": "float",
    "preprocessing": {
      "resize_shorter_side": 256,
      "center_crop": [224, 224],
      "normalize": {
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225]
      }
    },
    "output_contract": "imagenet_logits"
  },
  "quality": {
    "top1_accuracy": null,
    "top5_accuracy": null,
    "evaluation_dataset": null
  },
  "requirements": {
    "supported_runtimes": ["onnxruntime"],
    "minimum_ram_mb": null,
    "supported_platforms": ["android", "ios_browser", "windows", "linux"]
  },
  "benchmark_profile_ref": "benchmarks/aggregated/mobilenetv2_int8_v1.json",
  "lifecycle": {
    "status": "draft"
  }
}
```

Quy tắc quan trọng:

- `null` có nghĩa là chưa biết/chưa đo, không có nghĩa là 0.
- `task`, input/output contract và tiền xử lý là dữ liệu có tính quyết định. Hai file ONNX có cùng hình dạng đầu vào vẫn có thể làm hai tác vụ khác nhau.
- `top1_accuracy` chỉ có ý nghĩa khi đi kèm bộ dữ liệu/điều kiện đánh giá phù hợp.
- Metadata khai báo phải được xác minh; không suy ra chắc chắn accuracy, nhãn hoặc preprocessing chỉ từ graph ONNX.
- `size_bytes`, `sha256`, chunk count và hash chunk nên do công cụ nhập artifact tự tính, không nhập tay.

## 6. Thiết kế Model Registry và manifest

`registry/model_registry.json` là danh mục nội bộ chuẩn ở phiên bản demo; khi chuyển sang phiên bản web, SQL trở thành nguồn dữ liệu chuẩn cho metadata và vòng đời artifact. Registry chứa tất cả artifact được quản lý, không chỉ artifact đang hoạt động. File ONNX được lưu theo thư mục tác vụ trong `models/`; Registry lưu `storage_path` tương đối, còn API tương thích với client chỉ trả về trường đã được quy định trong schema giao thức. Manifest API là biểu diễn tương thích với phiên bản giao thức mà client đang sử dụng.

Một bản ghi Registry nên quản lý tối thiểu:

| Nhóm | Trường đề xuất |
|---|---|
| Định danh | `model_id`, `variant_id`, `version`, `task_id` |
| Artifact | đường dẫn lưu, định dạng, `size_bytes`, SHA-256 file, chunk size/count, SHA-256 từng chunk |
| Kiến trúc | họ model, precision, bậc nén, metadata trích xuất từ ONNX |
| Contract | input/output shape, dtype, preprocessing/postprocessing, nhãn và phiên bản contract |
| Chất lượng | benchmark accuracy, dataset, cách đánh giá và nguồn số liệu |
| Tương thích | runtime, nền tảng, kiến trúc, yêu cầu RAM/bộ nhớ, phần cứng đã benchmark |
| Benchmark | thời gian tải/nạp/suy luận, RAM, số mẫu, phiên bản runtime và điều kiện đo |
| Vòng đời | trạng thái, thời điểm thêm/cập nhật, lý do disable/retire |
| An toàn và nguồn gốc | nguồn artifact, checksum, license, người/phê duyệt phát hành |

`registry/model_registry.json` là cấu trúc nội bộ có thể phát triển độc lập. Khi trả về client, Server cần tạo đúng cấu trúc `variant` theo schema giao thức đang dùng. Không đưa trường tự phát vào thông điệp Protocol 1.0 vì schema hiện tại không cho phép trường dư.

## 7. Thiết kế Model Selector có khả năng mở rộng

### 7.1. Pipeline chọn model

```text
Yêu cầu từ client
    │
    ▼
Xác định task + đặc trưng thiết bị + điều kiện mạng
    │
    ▼
Lấy danh sách artifact status=ready từ Registry
    │
    ▼
Hard filters:
- đúng task và input/output contract
- runtime/nền tảng tương thích
- đủ dung lượng lưu trữ
- đáp ứng chính sách tài nguyên và deadline
    │
    ▼
Tập ứng viên khả thi
    │
    ▼
Model Selector:
- Rule-based baseline
- hoặc ML ranking / contextual selection
    │
    ▼
Chọn variant_id + giải thích + kế hoạch tải
    │
    ▼
Download Planner: chọn chunk/peer/server
```

### 7.2. Đầu vào của Model Selector

Đầu vào nên là một tập ứng viên động, mỗi ứng viên đại diện cho một `variant_id`; các đặc trưng của ứng viên được nối với ngữ cảnh thiết bị/yêu cầu.

**Đặc trưng ngữ cảnh** có thể gồm:

- task/client yêu cầu;
- platform, CPU architecture, RAM và runtime;
- băng thông đo được, deadline, storage trống;
- lịch sử hiệu năng của thiết bị hoặc `device_class`.

**Đặc trưng của từng artifact** có thể gồm:

- size bytes, số chunk, precision và bậc nén;
- task, input/output contract và preprocessing;
- accuracy benchmark đã được xác thực;
- latency, load time, peak RAM theo từng nhóm thiết bị/runtime;
- độ ổn định, tỷ lệ tải thành công và độ tin cậy của số liệu.

**Đặc trưng tương tác** có thể gồm:

- thời gian tải ước tính với băng thông hiện tại;
- artifact đã có trên thiết bị hay chưa;
- có peer khả dụng đang giữ artifact/chunk hay không;
- dự báo latency/RAM của artifact trên nhóm thiết bị tương ứng.

Không phải tất cả trường đều bắt buộc có ngay ngày đầu. Registry phải biểu diễn được dữ liệu thiếu và Candidate Filter phải tránh chọn artifact nếu thiếu thông tin quan trọng để xác nhận tính tương thích.

### 7.3. Đầu ra

Model Selector nên trả về tối thiểu:

- `variant_id` được chọn;
- điểm/ranking nội bộ và các ứng viên bị loại;
- lý do có thể giải thích được;
- ước tính thời gian tải/sử dụng nếu có dữ liệu;
- phiên bản của chính sách hoặc Model Selector đã ra quyết định.

Giao thức 1.0 hiện có trường `reason`, `variant`, `estimated_download_s` và `plan`. Các trường phục vụ vận hành nội bộ không nhất thiết phải công khai cho client nếu chưa có trong schema.

### 7.4. Huấn luyện bộ chọn từ feedback

Một bản ghi dữ liệu huấn luyện có thể có dạng khái niệm:

```json
{
  "context": {
    "task_id": "image_classification",
    "device_class": "android_arm64_4-8gb",
    "runtime": "onnxruntime",
    "bandwidth_kbps": 2000,
    "deadline_s": 60,
    "free_storage_mb": 2048
  },
  "candidate_variant_id": "mobilenetv2_int8_v1",
  "candidate_features": {
    "size_bytes": 3655033,
    "precision": "int8",
    "benchmark_latency_ms_median": 18.5,
    "benchmark_peak_ram_mb": 140.2
  },
  "observed_outcome": {
    "download_ms": 6840,
    "load_ms": 210.4,
    "latency_ms_median": 18.5,
    "deadline_met": true,
    "runtime_success": true
  }
}
```

Số liệu trên minh họa cấu trúc, không khẳng định đó là benchmark chuẩn cho mọi thiết bị. Trong thực tế cần thêm `selector_version`, thời điểm đo, phiên bản runtime, nguồn chunk và chất lượng/độ tin cậy của bản ghi.

Để huấn luyện có giám sát hoặc ranking, cần tạo nhãn/reward có định nghĩa rõ, chẳng hạn:

- artifact có vượt qua hard constraints hay không;
- hoàn thành tải đúng deadline;
- runtime có nạp và chạy thành công không;
- latency và RAM có đạt SLO không;
- accuracy được đo trên dữ liệu có nhãn chuẩn;
- tổng chi phí hoặc utility theo trọng số đã được nhóm thống nhất.

**Lưu ý:** `top1_label` và `top1_confidence` trong feedback hiện tại không đủ để suy ra accuracy thật nếu không có nhãn chuẩn. Feedback hiệu năng cũng không tự động trở thành nhãn “model nào tốt nhất”; cần chính sách reward/label rõ ràng và kiểm soát độ lệch dữ liệu.

### 7.5. Triển khai theo giai đoạn

1. **Giai đoạn đầu – Rule-based:** áp dụng hard filters và quy tắc minh bạch trên danh mục động. Đây là baseline dễ kiểm thử.
2. **Thu thập số đo:** lưu feedback theo task, variant, platform, runtime và điều kiện mạng; kiểm tra chất lượng dữ liệu.
3. **Huấn luyện thử Selector:** dùng dữ liệu lịch sử để xây ranking/scoring model; so sánh với baseline theo cùng tập test và các SLO.
4. **Shadow mode:** Selector dự đoán nhưng chưa điều khiển phân phối thật; ghi nhận khi nó khác baseline và đánh giá kết quả.
5. **Canary:** cho một phần client/lớp thiết bị dùng selector mới, có khả năng rollback.
6. **Phát hành:** chỉ promote khi đạt tiêu chí; lưu version, artifact hash, cấu hình và kết quả đánh giá của selector.
7. **Fallback:** nếu selector lỗi, thiếu dữ liệu hoặc không trả ứng viên hợp lệ, quay về rule-based selector.

Model Selector không nên được tự động huấn luyện và thay thế trong đường request trực tiếp. Huấn luyện, đánh giá và triển khai là pipeline riêng có versioning và phê duyệt.

## 8. Download Planner và P2P

Sau khi Selector chọn `variant_id`, Download Planner thực hiện truyền tải. Trách nhiệm phân phối phải độc lập với thuật toán chọn model để hai phần có thể phát triển riêng.

1. Đọc size, chunk size/count và hash từ Registry/manifest.
2. Xác định các chunk đã có hợp lệ trên client.
3. Tìm peer tương thích có artifact/chunk đã xác minh.
4. Lập `plan` cho mỗi chunk với `source`, `url` và `fallback_url`.
5. Cho phép client tải song song theo giới hạn cấu hình; Protocol 1.0 hiện quy định tối đa 4 chunk đồng thời.
6. Kiểm tra SHA-256 từng chunk và toàn file.
7. Khi peer lỗi, dùng `fallback_url` từ Server; không retry vô hạn.
8. Chỉ đăng ký peer là nguồn phục vụ sau khi artifact/chunk đã được xác minh.

P2P không phụ thuộc danh sách model cố định: peer inventory cần khóa theo `variant_id` và `chunk_index`. Khi Registry có thêm variant mới, hệ thống không được nhầm chunk của hai variant khác nhau chỉ vì chúng có tên file hoặc kích thước giống nhau.

## 9. Tương thích giao thức và phiên bản

Protocol 1.0 hiện tại được thiết kế cho tác vụ `image_classification`, có tập trường `variant` cụ thể và schema nghiêm ngặt (`additionalProperties: false`). Vì vậy cần phân biệt hai kiểu mở rộng:

### 9.1. Thêm model/variant mới cho cùng contract

Có thể mở rộng danh mục nội bộ mà không hard-code ID model mới, miễn artifact mới:

- vẫn sử dụng task và input/output contract mà client hiện tại hỗ trợ;
- biểu diễn được bằng các trường `variant` của Protocol 1.0;
- có đầy đủ metadata bắt buộc, hash và chunk plan hợp lệ;
- vượt qua kiểm định và được Registry đặt trạng thái `ready`.

Mặc dù protocol không yêu cầu một danh sách ID cố định, implementation Server/client không được hard-code danh sách bốn ID hiện tại.

### 9.2. Thêm task hoặc thay đổi contract

Nếu muốn phân phối model cho tác vụ khác (ví dụ ngoài phân loại ảnh), thay đổi cấu trúc input/output, cần trường metadata mới, thêm precision mới, hoặc mở rộng miền giá trị `task`, nên phát hành phiên bản giao thức mới. Các thay đổi cần đồng bộ:

1. `protocol.md`;
2. `protocol.schema.json`;
3. ví dụ request/response;
4. code serializer/deserializer và validation;
5. Model Registry/manifest adapter;
6. client capabilities/contract validation;
7. kiểm thử tương thích ngược và xử lý `VERSION_MISMATCH`.

Không tự tiện thêm trường vào JSON 1.0. Có thể dùng Registry nội bộ mới ngay trong khi API 1.0 vẫn chỉ trả schema cũ cho client tương thích.

## 10. API và chu trình vận hành

Các endpoint trong Protocol 1.0 vẫn giữ vai trò chính:

| Endpoint | Trách nhiệm trong kiến trúc mở rộng |
|---|---|
| `POST /api/register` | Đăng ký thiết bị và thông tin runtime/tài nguyên |
| `GET /api/probe` | Đo thông lượng tới Server |
| `POST /api/request` | Lọc ứng viên, gọi Selector, rồi lập kế hoạch tải |
| `GET /api/chunks/{variant_id}/{index}` | Phục vụ chunk từ Server |
| `GET /chunks/{variant_id}/{index}` | Phục vụ chunk từ peer |
| `POST /api/have` | Báo chunk xác thực, chunk lỗi và trạng thái hoàn tất |
| `POST /api/feedback` | Gửi kết quả tải/nạp/suy luận để phân tích hiệu năng |

Chu trình hệ thống:

1. Artifact mới được nhập và kiểm định bởi Model Ingestion.
2. Registry chỉ công bố artifact đã ở trạng thái `ready`.
3. Client đăng ký, gửi task và điều kiện mạng/tài nguyên.
4. Candidate Filter loại artifact không tương thích.
5. Selector chọn variant; Download Planner lập kế hoạch nguồn/chunk.
6. Client tải, xác thực, ghép file và gửi tiến độ.
7. Client chạy model, gửi feedback.
8. Feedback Store cập nhật hồ sơ benchmark và tập dữ liệu đánh giá/huấn luyện Selector.
9. Selector được cập nhật qua quy trình đánh giá riêng, không thay nóng một cách không kiểm soát.

## 11. Bảo đảm chất lượng, tính toàn vẹn và vận hành

- Tính hash SHA-256 từ file/chunk thật; không dùng hash giả của ví dụ.
- Tách file nhập mới khỏi thư mục artifact đang được phục vụ.
- Validate Model Card bằng schema; validate ONNX bằng checker/runtime; benchmark trước khi đánh dấu `ready`.
- Phát hành artifact theo cách nguyên tử và bất biến: cập nhật version mới thay vì ghi đè file đang được client tải.
- Dùng ID rõ ràng, tránh path traversal; mọi đường dẫn artifact phải được resolve bên trong Model Store.
- Không cho client tự tuyên bố một artifact là hợp lệ chỉ dựa trên tên file.
- Ghi log quyết định: candidate set, hard filters, selector version, chosen variant, reason, download plan và outcome.
- Quản lý dữ liệu feedback có timestamp, runtime/version, lớp thiết bị và mức tin cậy; loại bản ghi lỗi/méo trước khi huấn luyện.
- P2P dùng fallback khi peer không truy cập được, không có chunk hoặc hash sai.
- Prototype HTTP nội bộ không thay thế bảo mật khi triển khai thực tế. Mạng không tin cậy cần TLS, xác thực, phân quyền và chính sách kiểm soát nguồn artifact.
- Có khả năng disable/retire một model lỗi mà không xóa dấu vết lịch sử hoặc làm hỏng các request đã bắt đầu.

## 12. Kiểm thử nghiệm thu cho thiết kế mở rộng

### Danh mục động

- [ ] Thêm một file `.onnx` mới với Model Card hợp lệ thì Registry phát hiện mà không cần sửa bộ chọn theo tên file.
- [ ] File thiếu metadata bắt buộc vẫn ở trạng thái chưa phát hành.
- [ ] File ONNX hỏng, hash sai hoặc contract không hợp lệ bị từ chối.
- [ ] Hai phiên bản cùng một model được định danh khác nhau và không dùng lẫn cache/chunk.
- [ ] Artifact ở trạng thái `disabled` hoặc `retired` không được chọn cho request mới.

### Bộ chọn

- [ ] Chỉ các artifact tương thích task/contract/runtime đi vào tập ứng viên.
- [ ] Artifact vượt dung lượng hoặc không đáp ứng deadline bị loại theo chính sách.
- [ ] Selector có thể chọn artifact chưa nằm trong danh sách ID hard-code.
- [ ] Khi ML Selector lỗi hoặc không có ứng viên hợp lệ, rule-based fallback hoạt động.
- [ ] Mỗi quyết định có log giải thích được và ghi rõ phiên bản Selector.

### Giao thức và P2P

- [ ] JSON trả cho client vẫn khớp `protocol.schema.json`.
- [ ] Tải chunk từ Server và peer đều xác minh hash.
- [ ] Peer lỗi dẫn tới fallback; chỉ chunk hợp lệ mới được ghi nhận.
- [ ] Không đánh dấu hoàn tất trước khi hash toàn file khớp.
- [ ] Artifact mới của cùng contract hoạt động với client 1.0 mà không yêu cầu thay đổi client.
- [ ] Artifact cho task mới bị chặn nếu client/giao thức chưa hỗ trợ task/contract đó.

### Feedback và ML lifecycle

- [ ] Feedback liên kết được với `request_id`, `variant_id`, device/runtime và selector version.
- [ ] `top1_confidence` không bị dùng thay cho ground-truth accuracy.
- [ ] Tập dữ liệu train/validation/test được tách theo thời gian hoặc nhóm thiết bị phù hợp, tránh đánh giá lạc quan do rò rỉ.
- [ ] Selector mới được đánh giá và rollback được về bản trước.

## 13. Lộ trình triển khai đề xuất

**Giai đoạn A – Bỏ hard-code danh sách model**
- Chuyển danh sách bốn model sang Registry/manifest.
- Server đọc candidates từ Registry thay vì viết cố định bốn ID trong code.
- Sinh hash và chunk metadata tự động.
- Giữ API Protocol 1.0 và bài toán image classification.

**Giai đoạn B – Tiếp nhận artifact mới**
- Xây Ingestion, Model Card, validation và trạng thái vòng đời.
- Thử thêm một artifact khác cùng task/contract để xác nhận không phải sửa selector theo tên model.
- Có quy trình disable/retire và cập nhật manifest an toàn.

**Giai đoạn C – Benchmark và feedback có cấu trúc**
- Tổ chức benchmark theo platform/runtime/device class.
- Lưu telemetry có version và điều kiện đo rõ ràng.
- Xây dashboard phân tích tỷ lệ tải thành công, tải P2P, latency và RAM.

**Giai đoạn D – ML Selector**
- Tạo baseline rule-based.
- Xây tập dữ liệu chọn model có nhãn/reward rõ ràng.
- Huấn luyện ranking/scoring model và chạy shadow mode.
- Canary, đo KPI và rollback trước khi áp dụng rộng.

**Giai đoạn E – Nhiều tác vụ**
- Thiết kế Protocol 2.0 hoặc phiên bản sau cho task/contract tổng quát.
- Bổ sung khả năng client khai báo các task/input/output contract được hỗ trợ.
- Duy trì adapter để client cũ chỉ nhận thông điệp 1.0 đúng schema.

## 14. Kết luận

Kiến trúc mở rộng được xây dựng quanh **Model Registry + Model Selector + Download Planner**, thay vì gắn bộ chọn với bốn tên model cố định.

- Thêm `.onnx` mới là một quy trình nhập artifact, kiểm định, gắn Model Card, tạo hash/manifest và phát hành.
- Bộ chọn xem Registry như tập ứng viên động; nó dùng metadata và benchmark của model cùng ngữ cảnh thiết bị/mạng để chọn variant phù hợp.
- Feedback tạo dữ liệu vận hành và có thể dùng để huấn luyện Selector, nhưng cần định nghĩa reward/nhãn đúng, đặc biệt không coi confidence là accuracy.
- P2P/chunk hoạt động theo `variant_id` và hash nên không cần biết danh sách model cố định.
- Protocol 1.0 có thể tiếp tục phục vụ các artifact mới cùng task/contract nếu chúng vừa với schema; thêm tác vụ hoặc thay đổi hợp đồng cần phiên bản giao thức mới.

Bộ bốn model trong báo cáo chốt model là **danh mục khởi tạo**, không phải giới hạn kiến trúc.

---

# PHỤ LỤC A — CÁC GÓI TIN JSON THEO PROTOCOL 1.0

Phụ lục này nhúng trực tiếp chín file JSON mẫu mà nhóm đã cung cấp. Các payload là ví dụ hoàn chỉnh để người đọc đối chiếu với các endpoint trong tài liệu giao thức. Trong triển khai, client/server phải serialize dữ liệu động theo trạng thái thực tế; không gửi nguyên các giá trị minh họa dưới đây một cách cố định.

## A.1. Bản đồ gói tin trong một lượt phân phối

| File mẫu | Hướng truyền | Endpoint / thời điểm | Vai trò |
|---|---|---|---|
| `01_register_request.json` | Client → Server | `POST /api/register` | Client tự giới thiệu nền tảng, CPU, RAM, runtime và khả năng chia sẻ P2P |
| `02_register_response.json` | Server → Client | Phản hồi đăng ký | Server xác nhận đăng ký và thông báo lớp thiết bị, kích thước chunk, khả năng P2P |
| `03_request_request.json` | Client → Server | `POST /api/request` | Client yêu cầu model cho một tác vụ, kèm điều kiện tải và danh sách model đã có |
| `04_request_response.json` | Server → Client | Phản hồi yêu cầu model | Server chọn variant, trả metadata và kế hoạch tải theo từng chunk/source |
| `05_have_request.json` | Client → Server | `POST /api/have` | Client báo chunk mới đã kiểm tra, lỗi chunk và trạng thái hoàn tất |
| `06_have_response.json` | Server → Client | Phản hồi tiến độ | Server yêu cầu tiếp tục hoặc đổi variant |
| `07_feedback_request.json` | Client → Server | `POST /api/feedback` | Client gửi số đo thực tế sau khi tải và chạy model |
| `08_feedback_response.json` | Server → Client | Phản hồi feedback | Server quyết định giữ variant hiện tại hoặc đề xuất đổi variant cho lớp thiết bị |
| `09_error.json` | Server → Client | Khi xử lý request thất bại | Cấu trúc lỗi chuẩn dùng chung |

Luồng cơ bản: **register → request → tải chunk theo plan → have → chạy model → feedback**. Các request tải chunk là HTTP GET trả binary, không phải JSON. Endpoint `/api/probe` cũng trả dữ liệu nhị phân để đo băng thông; do vậy chúng không xuất hiện trong chín JSON mẫu này.

## A.2. Gói 01 — Client đăng ký (`01_register_request.json`)

**HTTP:** `POST /api/register`  
**Hướng:** Client → Server  
**Mục đích:** Khai báo thông tin nền tảng và khả năng chia sẻ file của client trước khi xin model.

```json
{
  "protocol_version": "1.0",
  "device_id": "phoneB",
  "platform": "android",
  "cpu_arch": "aarch64",
  "ram_mb": 6144,
  "runtime": "onnxruntime 1.25.1",
  "peer_port": 9000
}
```

| Trường | Ý nghĩa | Lưu ý |
|---|---|---|
| `protocol_version` | Phiên bản hợp đồng JSON mà client hỗ trợ | Mẫu này dùng `1.0`; server có thể trả `VERSION_MISMATCH` nếu không tương thích |
| `device_id` | ID client do client tự đặt | Phải duy nhất trong phạm vi hệ thống/mạng theo quy ước, tối đa 32 ký tự, chỉ chữ/số/`_`/`-` |
| `platform` | Loại nền tảng | Protocol 1.0 chấp nhận `android`, `android_browser`, `ios_browser`, `windows`, `linux` |
| `cpu_arch` | Kiến trúc CPU | Ví dụ `aarch64` hoặc `x86_64` |
| `ram_mb` | Tổng RAM theo MiB | Trong mẫu, `6144` tương ứng khoảng 6 GiB RAM theo quy ước trường |
| `runtime` | Runtime và phiên bản dùng để nạp ONNX | Ví dụ `onnxruntime 1.25.1`; giúp phân biệt khả năng tương thích/hiệu năng |
| `peer_port` | Cổng client dùng để chia sẻ chunk | `9000` trong ví dụ; gửi `null` nếu client không thể mở peer listener, chẳng hạn trình duyệt |

IP client không nằm trong JSON: server lấy IP từ kết nối mạng đến. Gọi lại endpoint với cùng `device_id` được dùng để cập nhật trạng thái/IP sau khi client khởi động lại.

## A.3. Gói 02 — Server xác nhận đăng ký (`02_register_response.json`)

**HTTP:** response `200` từ `POST /api/register`  
**Hướng:** Server → Client  
**Mục đích:** Xác nhận server đã đăng ký client và thông báo lớp thiết bị do server phân loại.

```json
{
  "protocol_version": "1.0",
  "device_id": "phoneB",
  "device_class": "android_arm64_4-8gb",
  "chunk_size": 1048576,
  "peer_enabled": true
}
```

| Trường | Ý nghĩa | Lưu ý |
|---|---|---|
| `protocol_version` | Phiên bản giao thức được server sử dụng | Phải tương thích với client |
| `device_id` | ID mà server vừa xử lý | Khớp với ID trong request |
| `device_class` | Nhãn lớp cấu hình do server phân loại | Ví dụ `android_arm64_4-8gb`; có thể dùng để gom thiết bị có đặc tính gần nhau khi chọn variant/ghép P2P |
| `chunk_size` | Kích thước chunk chuẩn, tính bằng byte | `1048576` byte = 1 MiB |
| `peer_enabled` | Server có cho phép chỉ định client làm nguồn peer hay không | `true` không bảo đảm peer luôn truy cập được; vẫn cần timeout/fallback khi truyền thực tế |

## A.4. Gói 03 — Client xin model (`03_request_request.json`)

**HTTP:** `POST /api/request`  
**Hướng:** Client → Server  
**Mục đích:** Gửi tác vụ cần chạy cùng các ràng buộc tài nguyên/mạng để server lọc ứng viên và chọn variant.

```json
{
  "device_id": "phoneB",
  "task": "image_classification",
  "bandwidth_kbps": 2000,
  "deadline_s": 60,
  "free_storage_mb": 2048,
  "preferred_variant": null,
  "have_variants": []
}
```

| Trường | Ý nghĩa | Lưu ý |
|---|---|---|
| `device_id` | Client đã đăng ký trước đó | Server xác nhận client tồn tại |
| `task` | Tác vụ client muốn thực hiện | Protocol 1.0 hiện quy định `image_classification` |
| `bandwidth_kbps` | Băng thông đo được đến server, kbps thập phân | `2000` tương ứng 2 Mbps; nếu chưa đo được thì gửi `null` |
| `deadline_s` | Thời gian tối đa mong muốn để tải xong, giây | Trong mẫu là 60 giây; server dùng khi đánh giá candidate |
| `free_storage_mb` | Dung lượng trống, theo quy ước `_mb` của protocol | Có thể là `null` nếu client không đo được |
| `preferred_variant` | Variant được ưu tiên cụ thể | `null` nghĩa là server tự chọn; nếu có giá trị, server vẫn phải xác minh ID và khả năng hợp lệ |
| `have_variants` | Các variant client đã có đầy đủ và đã xác minh | Rỗng trong mẫu nên server lập kế hoạch tải; ID ở đây phải theo Registry/manifest |

Với kiến trúc Registry động, server không được giả định danh sách variant chỉ gồm bốn model ban đầu. Server lấy candidate từ Registry, lọc theo task/contract, runtime, storage và chính sách deadline, rồi mới lựa chọn.

## A.5. Gói 04 — Server trả variant và kế hoạch tải (`04_request_response.json`)

**HTTP:** response `200` từ `POST /api/request`  
**Hướng:** Server → Client  
**Mục đích:** Trả ID yêu cầu, variant được chọn, lý do chọn, thời gian tải ước tính và kế hoạch tải chi tiết cho từng chunk.

```json
{
  "request_id": "r-0002",
  "variant": {
    "id": "mobilenetv2_int8",
    "file": "mobilenetv2-12-int8.onnx",
    "base_model": "mobilenetv2",
    "level": 1,
    "precision": "int8",
    "size_bytes": 3655033,
    "sha256": "cc028fe6cae7bc11a4ff53cfc9b79c920e8be65ce33a904ec3e2a8f66d77f95f",
    "chunk_size": 1048576,
    "chunk_count": 4,
    "top1_accuracy": 0.683,
    "input_shape": [
      1,
      3,
      224,
      224
    ],
    "input_dtype": "float",
    "normalize": {
      "mean": [
        0.485,
        0.456,
        0.406
      ],
      "std": [
        0.229,
        0.224,
        0.225
      ]
    }
  },
  "reason": "Lớp android_arm64_4-8gb đang dùng mobilenetv2_int8. phoneA cùng lớp đã có đủ 4 miếng, nên lấy miếng 0-1 từ phoneA và miếng 2-3 từ server để tải song song.",
  "estimated_download_s": 6.2,
  "plan": [
    {
      "index": 0,
      "offset": 0,
      "size_bytes": 1048576,
      "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "source": "peer",
      "url": "http://192.168.137.12:9000/chunks/mobilenetv2_int8/0",
      "fallback_url": "http://192.168.137.1:8000/api/chunks/mobilenetv2_int8/0"
    },
    {
      "index": 1,
      "offset": 1048576,
      "size_bytes": 1048576,
      "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "source": "peer",
      "url": "http://192.168.137.12:9000/chunks/mobilenetv2_int8/1",
      "fallback_url": "http://192.168.137.1:8000/api/chunks/mobilenetv2_int8/1"
    },
    {
      "index": 2,
      "offset": 2097152,
      "size_bytes": 1048576,
      "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
      "source": "server",
      "url": "http://192.168.137.1:8000/api/chunks/mobilenetv2_int8/2",
      "fallback_url": null
    },
    {
      "index": 3,
      "offset": 3145728,
      "size_bytes": 509305,
      "sha256": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
      "source": "server",
      "url": "http://192.168.137.1:8000/api/chunks/mobilenetv2_int8/3",
      "fallback_url": null
    }
  ]
}
```

### Ý nghĩa của `variant`

| Trường | Ý nghĩa |
|---|---|
| `id` | `variant_id` dùng xuyên suốt request, cache, peer inventory và feedback |
| `file` | Tên artifact ONNX client dùng để lưu cục bộ; với Registry động, tên này không nhất thiết là khóa duy nhất |
| `base_model` | Họ model, ví dụ `mobilenetv2` |
| `level` | Bậc biến thể/nén do Registry quy ước; trong mẫu, `0` thường biểu thị bản gốc và `1` là biến thể nén |
| `precision` | Kiểu số/độ chính xác, ví dụ `fp32` hoặc `int8` |
| `size_bytes` | Kích thước chính xác của toàn file, tính bằng byte |
| `sha256` | SHA-256 của toàn file, kiểm tra sau khi tải và ghép đủ chunk |
| `chunk_size` | Kích thước chunk danh định: 1 MiB |
| `chunk_count` | Tổng số chunk của file |
| `top1_accuracy` | Accuracy Top-1 dưới dạng tỷ lệ từ 0 đến 1; `0.683` tương đương 68.3% theo số liệu mẫu |
| `input_shape` | Hình dạng tensor đầu vào |
| `input_dtype` | Kiểu dữ liệu đầu vào theo hợp đồng inference |
| `normalize.mean`, `normalize.std` | Tham số chuẩn hóa 3 kênh ảnh |

### Ý nghĩa của `plan[]`

| Trường | Ý nghĩa |
|---|---|
| `index` | Số thứ tự chunk bắt đầu từ 0 |
| `offset` | Byte bắt đầu của chunk, bằng `index × chunk_size` |
| `size_bytes` | Số byte phải tải cho chunk này; chunk cuối có thể ngắn hơn |
| `sha256` | Hash chuẩn của chunk do server lấy từ manifest đã sinh từ file thật |
| `source` | Nguồn ưu tiên: `peer` hoặc `server` |
| `url` | URL dùng để lấy chunk lần đầu |
| `fallback_url` | URL Server dùng khi nguồn peer không hoạt động; là `null` nếu nguồn chính đã là Server |

Trong mẫu, chunk 0–1 lấy từ peer, chunk 2–3 lấy từ server. Client có thể tải các chunk song song nhưng phải xác minh SHA-256 trước khi đánh dấu chunk hợp lệ. Offset của chunk cuối là `3145728`, size là `509305` byte.

**Cảnh báo quan trọng về JSON mẫu:** theo `README.md` đi kèm, SHA-256 toàn file trong trường `variant.sha256` là giá trị thật của file mẫu; các giá trị hash chunk bắt đầu bằng `aaaa…`, `bbbb…`, `cccc…`, `dddd…` chỉ là chuỗi giả để minh họa. Không dùng chúng để tải/kiểm tra khi chạy thật. Cần sinh hash từng chunk từ chính file ONNX.

Các địa chỉ `192.168.137.1` và `192.168.137.12` minh họa cho mạng hotspot Windows. Khi chạy thật, Server phải trả URL theo địa chỉ/port thực tế có thể truy cập; không hard-code IP mẫu trong Client.

## A.6. Gói 05 — Client báo tiến độ/chunk (`05_have_request.json`)

**HTTP:** `POST /api/have`  
**Hướng:** Client → Server  
**Mục đích:** Báo cho server biết chunk nào mới được xác minh, có chunk lỗi hay không và toàn bộ artifact đã hoàn tất chưa.

```json
{
  "device_id": "phoneB",
  "request_id": "r-0002",
  "variant_id": "mobilenetv2_int8",
  "chunks": [
    0,
    1,
    2,
    3
  ],
  "complete": true,
  "bandwidth_kbps": 1870,
  "bad_chunks": []
}
```

| Trường | Ý nghĩa | Lưu ý |
|---|---|---|
| `device_id` | Client gửi báo cáo | Phải là client đã đăng ký |
| `request_id` | ID do `/api/request` trả về | Liên kết báo tiến độ với lượt xin model cụ thể |
| `variant_id` | Variant đang tải | Phải khớp variant trong request |
| `chunks` | Các chunk mới được xác minh thành công từ lần báo trước | Server tự hợp nhất trạng thái chunk đã nhận |
| `complete` | Cờ hoàn tất | Chỉ `true` khi đủ tất cả chunk **và** hash toàn file khớp `variant.sha256` |
| `bandwidth_kbps` | Thông lượng thực đo của các chunk lấy từ Server trong khoảng báo cáo | Trong ví dụ là `1870`; gửi `null` nếu các chunk mới đều lấy từ peer |
| `bad_chunks` | Danh sách chunk có hash sai, kèm `index` và `source_url` | Mảng rỗng nghĩa là không báo chunk lỗi trong lần này |

Client không được gửi `complete: true` chỉ vì đủ số chunk theo danh sách; phải xác minh hash toàn file sau khi ghép. Nếu chunk lỗi, không đưa chunk đó vào `chunks` hợp lệ; khai báo trong `bad_chunks` và tải lại từ nguồn fallback.

## A.7. Gói 06 — Server phản hồi tiến độ (`06_have_response.json`)

**HTTP:** response `200` từ `POST /api/have`  
**Hướng:** Server → Client  
**Mục đích:** Báo client tiếp tục kế hoạch hiện tại hay dừng để xin variant khác.

```json
{
  "ok": true,
  "action": "continue",
  "new_variant_id": null,
  "reason": null
}
```

| Trường | Ý nghĩa |
|---|---|
| `ok` | Xác nhận server xử lý báo tiến độ thành công |
| `action` | `continue` để tiếp tục; `switch` để dừng tải và xin variant mới |
| `new_variant_id` | ID variant mới khi `action` là `switch`; nếu `continue`, phải là `null` |
| `reason` | Lý do đề nghị đổi variant; `null` nếu không có yêu cầu đổi |

Ví dụ này yêu cầu tiếp tục. Theo mô tả Protocol 1.0, server có thể luôn trả `continue` trong giai đoạn đầu; logic switch giữa chừng có thể phát triển sau khi luồng tải cơ bản ổn định.

## A.8. Gói 07 — Client gửi feedback (`07_feedback_request.json`)

**HTTP:** `POST /api/feedback`  
**Hướng:** Client → Server  
**Mục đích:** Gửi các phép đo thực tế sau khi tải và chạy model, làm căn cứ đánh giá hiệu năng và điều chỉnh chính sách phân phối.

```json
{
  "device_id": "phoneB",
  "request_id": "r-0002",
  "variant_id": "mobilenetv2_int8",
  "download_ms": 6840,
  "chunks_from_peer": 2,
  "chunks_from_server": 2,
  "load_ms": 210.4,
  "latency_ms_median": 18.5,
  "runs": 20,
  "peak_ram_mb": 140.2,
  "top1_label": "backpack",
  "top1_confidence": 0.97
}
```

| Trường | Ý nghĩa | Lưu ý |
|---|---|---|
| `device_id`, `request_id`, `variant_id` | Khóa liên kết feedback với client, lượt request và variant | Server cần kiểm tra các ID này có hợp lệ và nhất quán |
| `download_ms` | Thời gian từ lúc nhận plan đến khi tải hoàn chỉnh | Đơn vị mili giây |
| `chunks_from_peer` | Số chunk đã lấy từ peer | Theo protocol, tính cả chunk phải tải lại nếu thuộc nguồn này |
| `chunks_from_server` | Số chunk đã lấy từ server | Theo protocol, tính cả chunk tải lại từ server |
| `load_ms` | Thời gian nạp/mở model | Mili giây |
| `latency_ms_median` | Trung vị độ trễ suy luận cho một ảnh | Mili giây; nên đo sau warm-up theo quy trình benchmark thống nhất |
| `runs` | Số lượt chạy dùng để đo latency | Trong ví dụ là 20 |
| `peak_ram_mb` | RAM đỉnh ghi nhận khi chạy | Có thể là `null` nếu runtime không cung cấp số đo đáng tin cậy |
| `top1_label` | Nhãn dự đoán đứng đầu trên ảnh thử | Chỉ là kết quả trên một ảnh, không chứng minh accuracy chung |
| `top1_confidence` | Confidence của dự đoán Top-1, khoảng 0–1 | Không được dùng thay cho accuracy có ground truth |

Các giá trị đo trong file mẫu được README ghi rõ là **số minh họa**. Khi vận hành phải ghi số đo thật, kèm ngữ cảnh platform/runtime và thời điểm đo ở hệ thống lưu trữ nội bộ nếu các trường đó chưa có trong Protocol 1.0.

## A.9. Gói 08 — Server phản hồi feedback (`08_feedback_response.json`)

**HTTP:** response `200` từ `POST /api/feedback`  
**Hướng:** Server → Client  
**Mục đích:** Quyết định giữ variant hiện tại hoặc chuyển sang variant khác; quyết định được gắn với một lớp thiết bị.

```json
{
  "action": "keep",
  "new_variant_id": null,
  "applies_to_class": "android_arm64_4-8gb",
  "reason": "Thời gian suy luận 18,5 ms dưới ngưỡng 50 ms của lớp; giữ mobilenetv2_int8."
}
```

| Trường | Ý nghĩa |
|---|---|
| `action` | `keep` giữ phiên bản; `switch` yêu cầu chuyển phiên bản |
| `new_variant_id` | Variant mới khi `action` là `switch`; phải `null` với `keep` |
| `applies_to_class` | Lớp thiết bị được áp dụng quyết định, giúp các thiết bị tương tự dùng chung variant và tăng khả năng P2P |
| `reason` | Giải thích dễ đọc cho dashboard/log |

Trong ví dụ, Server giữ `mobilenetv2_int8` vì latency đo được thấp hơn ngưỡng minh họa 50 ms. Ngưỡng này không phải quy tắc chung bắt buộc; chính sách production phải cấu hình theo task, yêu cầu SLA và benchmark thực tế.

Nếu Server trả `action: "switch"`, client cần gọi lại `/api/request` với `preferred_variant` bằng `new_variant_id` theo hướng dẫn Protocol 1.0.

## A.10. Gói 09 — Cấu trúc lỗi (`09_error.json`)

**Hướng:** Server → Client khi không thể xử lý request  
**Mục đích:** Chuẩn hóa lỗi để client có thể xử lý theo `error.code` mà không phải phân tích câu thông báo.

```json
{
  "error": {
    "code": "NO_VARIANT_FITS",
    "message": "Không có phiên bản nào tải kịp trong 10 giây ở 500 kbps."
  }
}
```

| Trường | Ý nghĩa |
|---|---|
| `error.code` | Mã lỗi máy có thể xử lý bằng logic điều kiện |
| `error.message` | Mô tả dễ đọc cho log/UI; không dùng nội dung này làm khóa logic ổn định |

Các mã lỗi được khai báo trong schema Protocol 1.0 gồm `BAD_REQUEST`, `UNKNOWN_DEVICE`, `UNKNOWN_VARIANT`, `CHUNK_OUT_OF_RANGE`, `VERSION_MISMATCH`, `NO_VARIANT_FITS`, `INTERNAL`. HTTP status tương ứng phải theo bảng trong `protocol.md`; client nên xử lý dựa trên HTTP status và `error.code`.

## A.11. Lưu ý khi dùng các gói JSON mẫu trong hệ thống mở rộng

1. **JSON mẫu là hợp đồng, không phải dữ liệu cấu hình cố định.** Client/server tạo payload theo trạng thái đang chạy, Registry và kết quả đo thực tế.
2. **Giữ `protocol_version` và schema đồng bộ.** Protocol 1.0 khai báo `additionalProperties: false` cho các thông điệp chính. Không tự ý thêm trường mới vào các gói đang dùng; hãy cập nhật schema, ví dụ và version giao thức đồng thời.
3. **Tách Registry nội bộ khỏi wire format.** Registry có thể chứa `task_id`, đường dẫn lưu, vòng đời, Model Card và metadata bổ sung; Server chỉ trả các trường được phép trong response schema Protocol 1.0. Nếu task/contract mới không thể biểu diễn bằng schema cũ, cần phiên bản giao thức mới.
4. **Không dùng hash giả.** Hash toàn file và từng chunk phải được sinh từ artifact thật; mỗi chunk phải được kiểm tra trước khi ghi nhận hợp lệ.
5. **Không coi IP ví dụ là cố định.** URL trong `plan` phải được tạo theo IP/port đang hoạt động. Client không tự suy đoán URL peer.
6. **Feedback là telemetry, chưa tự động là nhãn chất lượng.** Muốn huấn luyện Model Selector phải xác định nhãn/reward, kiểm soát dữ liệu sai và so sánh mô hình mới với baseline.
7. **Binary không nằm trong JSON.** Các endpoint tải chunk và probe trả `application/octet-stream`. JSON chỉ điều khiển metadata, kế hoạch và trạng thái.

Các file gốc `01_register_request.json` đến `09_error.json` vẫn nên được giữ trong `docs/examples/` để tự động kiểm tra schema. Phần phụ lục này giúp người đọc hiểu thông điệp trong tài liệu; không thay thế các file mẫu dùng trong test tự động.

