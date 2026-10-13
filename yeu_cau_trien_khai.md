# Yêu cầu triển khai hệ thống phân phối mô hình AI Edge – Phiên bản 1 và Phiên bản 2
 
**Ngày:** 13/10/2026  
**Mục tiêu:** Chốt phạm vi, yêu cầu kỹ thuật, sản phẩm bàn giao và tiêu chí nghiệm thu cho hai lần phát triển.

---

## 1. Tầm nhìn và nguyên tắc phân phiên bản

Hệ thống được phát triển thành hai phiên bản có thể trình diễn và nâng cấp độc lập:

- **Phiên bản 1 – Functional Demo:** Hoàn thiện luồng Server–Client, chọn model, tải artifact ONNX, xác minh file, chạy suy luận AI và gửi feedback. Ưu tiên chạy được end-to-end, không phụ thuộc vào web UI hoặc SQL.
- **Phiên bản 2 – Web Platform:** Xây dựng giao diện web/UI/UX, backend quản trị hoàn chỉnh và SQL làm nguồn dữ liệu chuẩn cho danh mục model, phiên bản, thiết bị, lịch sử phân phối và feedback. Tiếp tục dùng cùng kiến trúc phân phối, đồng thời nâng cấp bảo mật, vận hành và khả năng mở rộng.

Nguyên tắc chung:

1. `models/` được phân chia theo **tác vụ/nhóm tác vụ**, không chia thư mục theo tên họ model. Ví dụ: `models/CV/ResNet50_FP32.onnx`.
2. Danh mục artifact là động. Không hard-code danh sách 4 `variant_id` trong logic của Server hoặc Selector.
3. Bốn artifact hiện có là danh mục khởi tạo: MobileNetV2 FP32, MobileNetV2 INT8, ResNet-50 FP32 và ResNet-50 INT8. Khi bổ sung artifact mới, phải thông qua quy trình nhập, kiểm định, metadata và đăng ký.
4. Tách **model tác vụ** (ONNX được tải về để chạy AI) khỏi **Model Selector** (thành phần quyết định phân phối variant nào).
5. Giữ Protocol 1.0 cho demo hiện tại; không tự ý thêm trường JSON trái `protocol.schema.json`.
6. Các khả năng ML Selector nâng cao phải được đánh giá bằng dữ liệu thực nghiệm. Không tuyên bố mô hình học được chiến lược tốt hơn nếu chưa so sánh với baseline.

## 2. Ranh giới giữa hai phiên bản

| Hạng mục | Phiên bản 1 – Functional Demo | Phiên bản 2 – Web Platform |
|---|---|---|
| Mục tiêu | Chứng minh luồng phân phối và suy luận AI chạy thật | Quản trị bằng web, dữ liệu bền vững bằng SQL và vận hành thuận tiện |
| Backend | REST API tối thiểu | Backend đầy đủ, phân lớp API/dịch vụ/repository |
| Giao diện | CLI/log/JSON; không yêu cầu UI được thiết kế hoàn chỉnh | Web UI/UX cho quản trị và quan sát hệ thống |
| Danh mục model | Model Registry dạng JSON/file | SQL là nguồn chuẩn cho metadata và vòng đời model |
| Binary ONNX | Lưu trên filesystem theo thư mục tác vụ | Ưu tiên filesystem/object storage; SQL lưu bản ghi và đường dẫn/hash. Chỉ lưu binary trực tiếp thành BLOB nếu có yêu cầu cụ thể |
| Model Selector | Selector interface + rule-based baseline, lựa chọn từ Registry động | Quản lý cấu hình/phiên bản Selector, dashboard đánh giá; tích hợp ML ranker khi đủ dữ liệu |
| Client | Agent demo, hỗ trợ đăng ký/tải/kiểm tra/chạy model/feedback | Client tiếp tục dùng API; bổ sung theo dõi/troubleshooting thông qua UI và endpoint quản trị |
| P2P | P2P trực tiếp và fallback về Server là tiêu chí demo | Quản trị/quan sát peer và tỷ lệ sử dụng P2P tốt hơn |
| Feedback | Ghi vào JSONL/CSV hoặc cơ chế file đơn giản | Lưu có cấu trúc trong SQL; truy vấn, thống kê và xuất dữ liệu |
| Bảo mật | Mạng demo có kiểm soát, giới hạn rõ phạm vi | Đăng nhập, phân quyền, kiểm soát upload, audit log, cấu hình TLS khi triển khai |
| Nghiệm thu | Demo end-to-end lặp lại được | Demo quản trị web và các luồng Version 1 vẫn hoạt động sau khi chuyển sang SQL |

**Phân biệt dữ liệu model và file model:** Version 2 dùng SQL để quản lý bản ghi model/variant, metadata, trạng thái và lịch sử. Các file ONNX có thể lớn tới hàng trăm MB; thiết kế mặc định là lưu byte file ở filesystem hoặc object storage, còn SQL lưu `storage_uri`, kích thước, SHA-256 và quan hệ phiên bản. Nếu yêu cầu dự án bắt buộc byte `.onnx` phải nằm trực tiếp trong SQL, có thể dùng BLOB nhưng cần kiểm thử dung lượng database, backup/restore và throughput.

---

# PHẦN A — PHIÊN BẢN 1: FUNCTIONAL DEMO

## 3. Mục tiêu nghiệm thu của Version 1

Một người có thể khởi động Server, chạy hai Client trong cùng LAN/hotspot, yêu cầu tải model, quan sát việc chọn phiên bản và nguồn tải, chạy phân loại ảnh bằng ONNX Runtime và xem feedback. Demo phải có thể chạy lặp lại bằng hướng dẫn trong repository.

Kịch bản trình diễn tối thiểu:

1. Server khởi động và phát hiện các model đã đăng ký.
2. Client A đăng ký, yêu cầu và tải một variant từ Server.
3. Client A xác minh SHA-256 toàn file thành công, sau đó chia sẻ chunk qua peer service.
4. Client B đăng ký và yêu cầu cùng variant.
5. Server lập plan trong đó ít nhất một chunk có `source: "peer"` nếu kết nối P2P khả dụng; các chunk còn lại có thể lấy từ Server.
6. Client B tải, xác minh chunk, ghép file và xác minh hash toàn file.
7. Client B chạy suy luận trên ảnh mẫu và in Top-5 nhãn/xác suất.
8. Client gửi `/api/feedback`; Server ghi nhận và trả `keep` hoặc `switch`.
9. Demo một tình huống peer ngừng hoạt động/không truy cập được để chứng minh fallback về Server.

Nếu môi trường mạng không cho phép peer-to-peer, cần trình diễn được lý do/failure rõ ràng và fallback thành công; kiểm thử P2P thực tế nên được thực hiện thêm trên LAN/hotspot không bật AP/client isolation.

## 4. Phạm vi tính năng Version 1

### 4.1. Model Store và Registry động

**Yêu cầu bắt buộc:**

- Thư mục model được tổ chức theo tác vụ:

```text
models/
└── CV/
    ├── MobileNetV2_FP32.onnx
    ├── MobileNetV2_INT8.onnx
    ├── ResNet50_FP32.onnx
    └── ResNet50_INT8.onnx
```

- Dùng `registry/model_registry.json` để mô tả các variant đã đăng ký; Model Card đặt trong `metadata/model_cards/`.
- Mỗi variant có tối thiểu: `model_id`, `variant_id`, `version`, `task_id`, tên file, `storage_path`, `size_bytes`, SHA-256, precision, input/output contract, preprocessing, thông tin accuracy đã xác thực và lifecycle status.
- Tự tính kích thước và SHA-256 từ file thật. Tính SHA-256 từng chunk từ byte thật, không dùng hash giả trong JSON minh họa.
- Server duyệt Registry theo cấu hình/danh mục, không có logic bắt buộc kiểu `if model == "resnet50_int8"`.
- Chỉ artifact ở trạng thái `ready` mới được phân phối.
- Có lệnh/tool nhập hoặc refresh Registry và kiểm tra tính hợp lệ của JSON. Bổ sung file mới cần thao tác đăng ký/kiểm định, không cần sửa mã nguồn Model Selector nếu artifact dùng contract đã hỗ trợ.
- Bảo vệ đường dẫn: `storage_path` phải nằm bên trong Model Store, không cho path traversal.
- Nếu thiếu metadata quan trọng, file hỏng hoặc ONNX không hợp lệ, không đánh dấu artifact `ready`.

**Không thuộc phạm vi Version 1:** trang web upload/duyệt model, SQL registry, quản lý quyền nâng cao.

### 4.2. Server REST API

Theo hợp đồng hiện tại trong `protocol.md`, Server sử dụng HTTP/1.1 và giữ port mặc định `8000`.

Endpoint bắt buộc:

| Endpoint | Yêu cầu nghiệm thu |
|---|---|
| `POST /api/register` | Kiểm tra schema, đăng ký/refresh client, trả `device_class`, `chunk_size`, `peer_enabled` |
| `GET /api/probe?size_kb=256` | Trả đúng số byte nhị phân theo quy định và cho client đo thông lượng |
| `POST /api/request` | Lọc candidate, chọn variant, tạo `request_id`, trả metadata và plan chunk |
| `GET /api/chunks/{variant_id}/{index}` | Trả đúng byte của chunk qua `application/octet-stream` |
| `POST /api/have` | Ghi nhận chunk hợp lệ/lỗi và trạng thái hoàn tất; trả `continue` hoặc `switch` theo chính sách |
| `POST /api/feedback` | Ghi số đo thực tế; trả `keep` hoặc `switch` |
| Handler lỗi | Trả cấu trúc `{"error":{"code":"...","message":"..."}}` theo schema |

Yêu cầu kỹ thuật:

- JSON UTF-8, tên trường `snake_case`.
- Server kiểm tra `protocol_version`, kiểu trường, ID, index và trạng thái yêu cầu.
- Không trả đường dẫn file hệ thống, stack trace hoặc thông tin nội bộ nhạy cảm cho client.
- Không nạp toàn bộ model vào RAM để phục vụ mỗi request. Stream file theo chunk.
- Chỉ đưa peer vào plan khi Server có bản ghi rằng peer đã có artifact/chunk cần thiết và peer được phép phục vụ.
- `preferred_variant` phải được kiểm tra với Registry; không nhận ID không tồn tại.
- `have_variants` chỉ được tin sau khi đối chiếu trạng thái đã xác minh trong hệ thống hoặc có cơ chế xác thực tương ứng; tránh dùng khai báo client một cách mù quáng.
- Có log theo `request_id` cho đăng ký, chọn model, chunk plan, fallback, kết thúc tải và feedback.

### 4.3. Client Agent

Version 1 cần Client Agent đủ dùng cho demo kỹ thuật; chưa yêu cầu giao diện web hoàn chỉnh.

**Lệnh/chức năng tối thiểu:**

- Khởi tạo cấu hình Server URL, `device_id`, platform, runtime và peer port.
- Đăng ký với `/api/register`.
- Đo băng thông tùy chọn.
- Gửi yêu cầu task và điều kiện mạng/tài nguyên.
- Nhận `variant` và `plan`.
- Tải tối đa 4 chunk đồng thời theo giới hạn Protocol 1.0.
- Xác minh SHA-256 từng chunk trước khi ghi nhận.
- Ghi chunk theo `offset`, hỗ trợ tải lại chunk lỗi và fallback về Server.
- Sau khi đủ chunk, xác minh SHA-256 toàn file.
- Gửi `/api/have` và chỉ đặt model vào trạng thái local `complete` khi hash toàn file trùng khớp.
- Chạy inference bằng ONNX Runtime trên ảnh được truyền vào.
- Đo `download_ms`, `load_ms`, `latency_ms_median`, `runs`, `peak_ram_mb` nếu đo được, số chunk theo nguồn.
- Gửi `/api/feedback`.
- In log rõ ràng: variant được chọn, lý do, nguồn của từng chunk, hash verification, thời gian tải/nạp/suy luận và kết quả Top-5.

**Phạm vi client đề xuất:** Python CLI cho demo Windows/Linux là đường đi đơn giản nhất; có thể bổ sung client trình duyệt/điện thoại theo nền tảng nhóm đã thử nghiệm. Nếu chạy trong trình duyệt không thể mở cổng peer, client gửi `peer_port: null` và vẫn tải/chạy model bình thường nhưng không làm nguồn P2P.

### 4.4. Peer service

- Client có khả năng phục vụ peer mở HTTP listener ở port mặc định `9000`.
- Endpoint `GET /chunks/{variant_id}/{index}` chỉ trả chunk mà client thật sự có và đã xác minh.
- Trả `404` khi chưa có chunk.
- Không phục vụ file đang tải dở hoặc file chưa qua kiểm tra hash toàn file nếu chính sách demo yêu cầu chỉ chia sẻ artifact complete.
- Client giữ inventory theo `variant_id` và chunk index; không ghép nhầm chunk giữa hai variant.
- Có timeout và retry có giới hạn. Khi peer lỗi, client tải chunk tương ứng từ `fallback_url`.
- Nếu AP/client isolation hoặc firewall ngăn P2P, việc tải từ Server vẫn phải hoạt động.

### 4.5. Model AI thực hiện tác vụ

Version 1 phải chứng minh rằng model nhận về thực sự chạy được, không chỉ tải thành công.

- Dùng bốn model ONNX trong báo cáo chốt model làm danh mục khởi tạo.
- Task ban đầu: `image_classification`.
- Preprocessing theo Model Card/báo cáo chốt: ảnh màu, resize cạnh ngắn về 256, crop giữa 224×224, normalize theo mean `[0.485, 0.456, 0.406]` và std `[0.229, 0.224, 0.225]`.
- Đầu vào hiện tại là `[1, 3, 224, 224]`, dtype `float`.
- Trả Top-5 nhãn ImageNet và confidence. Phải dùng nhãn mapping đúng phiên bản dataset.
- Đo thời gian nạp và latency nhiều lần rồi lấy trung vị; ghi rõ runtime/hardware.
- Kiểm tra các biến thể đầu tiên bằng ảnh mẫu đã chọn và xác nhận không lỗi runtime.
- Không coi `top1_confidence` là độ chính xác thật. Đánh giá accuracy cần dữ liệu có nhãn chuẩn.

### 4.6. Model Selector Version 1

Selector phải hoạt động với danh sách candidate lấy từ Registry, không hard-code 4 ID.

**Yêu cầu bắt buộc cho demo:**

1. Tạo interface độc lập, ví dụ `IModelSelector`/`ModelSelector`, để sau này thay thuật toán mà không sửa API và Download Planner.
2. Chạy Candidate Filter trước khi xếp hạng: task/contract phù hợp, runtime/platform tương thích, đủ dung lượng và đáp ứng chính sách deadline.
3. Cung cấp rule-based baseline chọn giữa các candidate hợp lệ dựa trên băng thông, size, benchmark theo thiết bị/runtime và yêu cầu người dùng.
4. Trả lý do có thể đọc được và lưu ứng viên/đặc trưng/variant được chọn.
5. Nếu không có candidate hợp lệ, trả `NO_VARIANT_FITS` đúng schema.
6. Có fallback selector nếu thuật toán chính lỗi.

**Đối với ML Selector được huấn luyện:** kiến trúc cần cho phép thêm ranker đã huấn luyện, nhưng không được tạo nhãn giả chỉ để tuyên bố mô hình đã học tốt. Để đánh giá ML Selector, cần log context/candidate/outcome và định nghĩa reward hoặc nhãn (ví dụ tải kịp deadline, runtime thành công, latency/RAM trong ngưỡng, accuracy được đo đúng). Nếu nhóm yêu cầu bắt buộc có mô hình selector đã huấn luyện ngay trong demo đầu, đó là một hạng mục bổ sung riêng: chuẩn bị benchmark có kiểm soát, huấn luyện ranker đơn giản, so sánh với baseline và chỉ bật khi đạt tiêu chí. Không để việc này chặn demo phân phối/inference cơ bản.

### 4.7. Lưu log và feedback

- Version 1 không cần SQL; có thể ghi feedback dưới dạng JSONL/CSV và lưu log theo ngày.
- Mỗi feedback phải gắn `request_id`, `device_id`, `variant_id`, thời điểm và thông tin platform/runtime.
- Ghi số chunk từ peer/server, thời gian tải/nạp/suy luận và RAM nếu đo được.
- Ghi lý do quyết định của Selector và trạng thái fallback.
- Dữ liệu ví dụ phải được đánh dấu `sample/demo`, không trộn với benchmark thật.
- Có thể dùng SQLite cho tiện ích kiểm thử nội bộ nhưng không coi đó là yêu cầu bắt buộc; nguồn danh mục Version 1 là Registry JSON và Model Store filesystem.

## 5. Công nghệ và cấu trúc mã nguồn đề xuất cho Version 1

Tận dụng giao thức đã chốt:

- **Server:** ASP.NET Core REST API.
- **Client demo:** Python CLI và ONNX Runtime; client/browser khác là triển khai bổ sung nếu có thời gian.
- **Peer service:** HTTP trên TCP, port mặc định `9000`.
- **Model Store:** filesystem, nhóm theo tác vụ.
- **Registry:** JSON.
- **Data Plane:** binary chunk 1 MiB, `application/octet-stream`.
- **Integrity:** SHA-256 toàn file và từng chunk.
- **Validation:** `protocol.schema.json` và bộ kiểm thử các ví dụ JSON.

Cấu trúc đề xuất:

```text
project/
├── models/
│   └── CV/
│       ├── MobileNetV2_FP32.onnx
│       ├── MobileNetV2_INT8.onnx
│       ├── ResNet50_FP32.onnx
│       └── ResNet50_INT8.onnx
├── staging/incoming/
├── metadata/model_cards/
├── registry/model_registry.json
├── benchmarks/raw/
├── benchmarks/aggregated/
├── selector/
│   ├── rules/
│   ├── training_data/
│   ├── artifacts/
│   └── evaluations/
├── server/
├── client/
├── tests/
└── docs/
    ├── protocol.md
    ├── protocol.schema.json
    ├── system_overview.md
    └── run_demo.md
```

## 6. Tiêu chí nghiệm thu Version 1

Version 1 chỉ được xem là hoàn thành khi tối thiểu các kiểm thử sau đều chạy được:

- [ ] Server khởi động theo hướng dẫn, health/status endpoint hoặc log khởi động xác nhận sẵn sàng.
- [ ] Registry nạp được bốn variant đầu tiên từ metadata; không hard-code ID trong Selector.
- [ ] Có thể thêm một artifact thử nghiệm cùng task/contract, đăng ký nó trong Registry và thấy nó trở thành candidate mà không sửa điều kiện theo tên model.
- [ ] Model ONNX hợp lệ được nạp bằng ONNX Runtime và trả Top-5 cho ảnh mẫu.
- [ ] Client đăng ký và nhận đúng `device_class`, `chunk_size`, `peer_enabled`.
- [ ] `/api/request` trả variant hợp lệ, `request_id`, metadata và đầy đủ chunk plan.
- [ ] Tất cả chunk tải về đều xác minh SHA-256; sau ghép file, hash toàn file đúng.
- [ ] Peer phục vụ được chunk đã xác minh trong kịch bản LAN khả dụng.
- [ ] Khi peer dừng/không kết nối được, fallback về Server thành công.
- [ ] `/api/have` và `/api/feedback` được nhận, xác thực và ghi log.
- [ ] JSON request/response khớp schema; lỗi dùng đúng error envelope.
- [ ] Demo từ đầu tới cuối có thể lặp lại theo `docs/run_demo.md`.
- [ ] Có báo cáo đo tải, load time, latency và RAM; ghi rõ điều kiện đo và không trình bày số minh họa như kết quả thực.

### Bàn giao Version 1

1. Source Server.
2. Source Client Agent và Peer Service.
3. Bốn file ONNX theo danh mục đã chốt, hoặc hướng dẫn đặt file nếu không thể phân phối kèm artifact.
4. Model Cards, Registry và công cụ sinh hash/refresh registry.
5. `protocol.md`, schema và các JSON ví dụ đã được kiểm thử.
6. Script/command chạy Server và hai Client.
7. Tập ảnh demo và nhãn ImageNet tương ứng.
8. README/runbook: cài thư viện, cấu hình IP/port, chạy demo, khắc phục AP isolation/firewall và đọc log.
9. Kết quả test end-to-end và bảng số đo.

---

# PHẦN B — PHIÊN BẢN 2: WEB UI/UX + BACKEND + SQL

## 7. Mục tiêu Version 2

Biến prototype thành nền tảng có giao diện web để quản lý danh mục model, upload và duyệt artifact, theo dõi thiết bị, quan sát tải/P2P, xem feedback và điều chỉnh chính sách phân phối. Dữ liệu quản trị phải bền vững trong SQL; luồng tải chunk và API client đã hoàn thành trong Version 1 phải tiếp tục hoạt động.

Version 2 không được viết lại hệ thống thành một ứng dụng web đơn khối khiến logic phân phối dính chặt vào UI. Backend cần cung cấp API; frontend chỉ gọi API đó.

## 8. Giao diện web/UI/UX

### 8.1. Trang tổng quan Dashboard

Hiển thị tối thiểu:

- số model/variant `ready`, `draft`, `disabled`, `retired`;
- số thiết bị online/đã đăng ký và số peer đang phục vụ;
- số request theo thời gian, tỷ lệ tải hoàn thành/thất bại;
- tỷ lệ chunk từ Server so với P2P;
- thời gian tải trung bình/trung vị, load time, latency theo task/model/device class;
- cảnh báo hash sai, peer lỗi hoặc artifact kiểm định thất bại.

Mỗi biểu đồ phải có khoảng thời gian lọc; dữ liệu chưa đủ phải hiển thị trạng thái rỗng, không hiển thị 0 như thể đã có phép đo.

### 8.2. Trang danh mục model

- Bảng model group theo task/domain, ví dụ CV.
- Tìm kiếm/lọc theo task, họ model, precision, trạng thái, phiên bản và runtime.
- Xem chi tiết một variant: Model Card, kích thước, hash, contract, benchmark, lịch sử phát hành.
- Tách rõ `model_id` (họ model), `variant_id` (artifact/biến thể) và `version`.
- Không phụ thuộc danh sách 4 variant ban đầu; bảng đọc từ SQL/Registry service.

### 8.3. Luồng upload và đăng ký artifact

- Chọn task/domain hoặc tạo task theo quyền được cấp.
- Upload `.onnx` vào vùng staging; không phát hành trực tiếp sau upload.
- Nhập/đính kèm Model Card và các thông tin bắt buộc.
- Backend kiểm tra file, kích thước, checksum, graph/metadata và contract đã khai báo.
- Trình bày kết quả validation/benchmark: pass, warning, failed.
- Người có quyền phê duyệt chuyển artifact từ `draft/validating` sang `ready`.
- Sau khi phát hành, variant được đưa vào Candidate Filter mà không cần deploy lại code Server.
- Có nút disable/retire, nhưng mọi thay đổi phải tạo audit event và không phá các request đang tải.

### 8.4. Trang thiết bị và P2P

- Danh sách client: `device_id`, platform, architecture, RAM, runtime, device class, peer capability, last seen.
- Xem variant/chunk đã có và trạng thái chia sẻ theo thông tin Server ghi nhận.
- Xem các request và nguồn chunk đã dùng.
- Hiển thị peer không truy cập được, fallback count và lỗi hash.
- Không đưa thông tin mạng hoặc định danh nhạy cảm lên dashboard cho mọi role nếu không cần.

### 8.5. Trang feedback và hiệu năng

- Lọc theo task, variant, version, device class, platform/runtime, thời gian.
- So sánh kích thước, download time, load time, latency median, peak RAM và tỷ lệ thành công.
- Phân biệt số liệu đo, số liệu ước tính và số liệu minh họa.
- Cho phép export CSV/JSON phục vụ phân tích/học Selector.
- Đánh dấu nguồn và độ tin cậy của record; không dùng confidence của một ảnh làm accuracy.

### 8.6. UX và khả năng sử dụng

- Hiển thị trạng thái xử lý bất đồng bộ (uploading, validating, ready, failed).
- Upload file lớn phải có tiến độ, thông báo lỗi hữu ích và khả năng retry có kiểm soát.
- Form metadata có validation phía client và phía server.
- Mọi hành động phát hành, disable, retire yêu cầu xác nhận và hiển thị hậu quả.
- Hỗ trợ phân trang, tìm kiếm server-side và filter; không tải toàn bộ lịch sử vào browser.
- Có empty state, loading state, error state và thông báo thành công/thất bại.
- Giao diện responsive cơ bản cho màn hình laptop/tablet.
- Có tiếng Việt nhất quán; thuật ngữ kỹ thuật có thể kèm tên trường tiếng Anh.
- Không dùng màu sắc làm dấu hiệu duy nhất cho trạng thái; cần kèm chữ/icon.

## 9. Backend Version 2

### 9.1. Yêu cầu kiến trúc

- Tách lớp API/controller, application/service, domain logic và repository/data access.
- Logic Candidate Filter, Model Selector, Download Planner và validation không nằm trong controller/UI.
- Dùng migration có version để thay đổi schema SQL.
- Các tác vụ dài như upload validation, hash, phân tích ONNX và benchmark chạy trong background job; UI lấy tiến độ trạng thái.
- Các thao tác phát hành Registry phải nguyên tử: chỉ công bố `ready` sau khi artifact, metadata và hash đã lưu đầy đủ.
- Client API Protocol 1.0 được giữ qua adapter tương thích; API quản trị web được thiết kế riêng, không làm client tải file qua endpoint UI.
- Có health check, cấu hình qua environment variables/secret store, structured logging và correlation ID.
- Có giới hạn upload, kích thước body, timeout, rate limit và xử lý ngoại lệ chuẩn.

### 9.2. Công nghệ đề xuất

Để tận dụng giao thức đã thiết kế, khuyến nghị:

- **Backend:** ASP.NET Core Web API.
- **Database:** SQL Server hoặc PostgreSQL; chọn một hệ quản trị làm chuẩn cho triển khai.
- **Frontend:** React + TypeScript (hoặc framework web tương đương, chốt một stack).
- **Model artifact storage:** filesystem/object storage; SQL lưu metadata, trạng thái và vị trí file.
- **Background processing:** hosted service/worker trong ASP.NET Core ở giai đoạn đầu; tách worker riêng khi tải công việc tăng.
- **API docs:** OpenAPI/Swagger cho API quản trị.
- **Validation:** dùng schema/version của Model Card và hợp đồng giao thức được kiểm soát trong repository.

Không bắt buộc dùng đồng thời SQL Server và PostgreSQL. Chốt một DB để tránh chi phí vận hành hai hệ quản trị.

### 9.3. Bản ghi và bảng SQL tối thiểu

| Bảng logic | Trường/quan hệ quan trọng | Mục đích |
|---|---|---|
| `Tasks` | `task_id`, tên, contract version, trạng thái | Danh mục tác vụ |
| `Models` | `model_id`, tên họ model, `task_id`, mô tả | Danh tính logic của model |
| `ModelVariants` | `variant_id`, `model_id`, version, precision, status, Model Card | Artifact/biến thể có thể phân phối |
| `ModelArtifacts` | storage URI, filename, bytes, SHA-256, chunk size/count | Vị trí và kiểm tra artifact |
| `ModelChunks` | variant, index, offset, size, SHA-256 | Hash/metadata mỗi chunk |
| `ModelBenchmarks` | variant, device class, platform/runtime, latency, load time, RAM, accuracy/dataset | Dữ liệu benchmark có ngữ cảnh |
| `Devices` | device ID, class, platform, arch, RAM, runtime, peer capability, last seen | Quản lý thiết bị |
| `DeviceVariants` | device, variant, trạng thái complete và thời gian xác minh | Ghi nhận model trên thiết bị |
| `Requests` | request ID, device, task, variant, selector version, thời gian, trạng thái | Theo dõi yêu cầu tải |
| `RequestChunks` | request, chunk index, source peer/server, retry, status, duration | Thống kê data plane |
| `Feedback` | request/device/variant, download/load/latency/RAM/runs, timestamp | Dữ liệu vận hành/selector |
| `SelectorVersions` | version, artifact URI/hash, cấu hình, evaluation result, active flag | Versioning bộ chọn |
| `AuditEvents` | actor, action, entity, timestamp, result | Truy vết thay đổi quản trị |
| `Users/Roles` | user, role, permission | Kiểm soát truy cập web |

Cần định nghĩa khóa chính/ngoại, unique constraint cho `variant_id` và version, index cho task/status/created time, cùng chính sách retention cho telemetry. Không lưu bản ghi benchmark nếu thiếu thông tin nguồn/điều kiện đo mà không đánh dấu rõ.

### 9.4. Quản lý file ONNX

- Backend upload vào `staging` trước; SQL record mang trạng thái `draft` hoặc `validating`.
- Tạo hash file/chunk và metadata trong worker.
- Chuyển artifact đến đường dẫn bất biến, theo task và variant/version; cập nhật storage URI trong SQL.
- Chỉ đánh dấu `ready` khi file tồn tại, đọc được, metadata đạt yêu cầu và checksum hoàn chỉnh.
- Không ghi đè artifact đã phát hành; tạo version mới.
- Với upload gián đoạn, dùng upload session hoặc cơ chế retry; xóa staging mồ côi qua job dọn dẹp.
- Có kế hoạch backup metadata SQL và artifact store cùng nhau; test restore để đảm bảo URI/hash vẫn khớp.
- Nếu lưu byte ONNX dưới dạng SQL BLOB theo yêu cầu đặc biệt, phải xác định giới hạn file, chi phí backup, streaming API và test hiệu năng trước khi chọn làm mặc định.

## 10. API Version 2

### 10.1. API quản trị web

Đề xuất nhóm endpoint logic sau (đường dẫn cuối cùng chốt khi thiết kế API):

| Nhóm | Ví dụ endpoint | Chức năng |
|---|---|---|
| Dashboard | `GET /admin-api/dashboard/summary` | KPI tổng quan |
| Task | `GET/POST /admin-api/tasks` | Tra cứu/tạo task |
| Models | `GET /admin-api/models` | Tìm kiếm, lọc, phân trang |
| Variant | `GET /admin-api/variants/{variant_id}` | Chi tiết variant/Model Card |
| Upload | `POST /admin-api/uploads` | Khởi tạo upload |
| Validation | `GET /admin-api/uploads/{upload_id}` | Trạng thái validation/tiến độ |
| Publish | `POST /admin-api/variants/{variant_id}/publish` | Phê duyệt phát hành |
| Lifecycle | `POST /admin-api/variants/{variant_id}/disable` | Ngừng phân phối |
| Benchmark | `GET /admin-api/benchmarks` | Truy vấn số đo |
| Feedback | `GET /admin-api/feedback` | Lọc/xuất feedback |
| Devices | `GET /admin-api/devices` | Danh sách thiết bị/peer |
| Selector | `GET/POST /admin-api/selectors` | Tra cứu/đăng ký phiên bản selector |

Đây là endpoint đề xuất cho API quản trị, không phải endpoint đã được chốt trong Protocol 1.0. Cần chuẩn hóa response envelope, phân trang, validation error và auth trước khi triển khai.

### 10.2. API phân phối cho Client

Các endpoint sau tiếp tục tương thích với giao thức đã chốt:

- `POST /api/register`
- `GET /api/probe`
- `POST /api/request`
- `GET /api/chunks/{variant_id}/{index}`
- `POST /api/have`
- `POST /api/feedback`
- Peer `GET /chunks/{variant_id}/{index}`

Nội bộ Server có thể đọc SQL thay vì Registry JSON, nhưng response JSON gửi client vẫn phải khớp schema theo protocol version đã thương lượng/hỗ trợ. Không để việc xây web UI làm thay đổi hợp đồng client không cần thiết.

## 11. Bảo mật và quyền trong Version 2

Role tối thiểu:

- **Viewer:** xem dashboard, model, benchmark và trạng thái.
- **Operator:** quản lý thiết bị/quan sát vận hành, chạy lại validation hoặc benchmark nếu được cấp quyền.
- **Model Publisher:** upload, sửa metadata bản nháp và đề nghị phát hành.
- **Admin:** quản lý người dùng/role, phê duyệt hành động nhạy cảm và cấu hình hệ thống.

Yêu cầu:

- API quản trị yêu cầu xác thực; mọi thao tác nhạy cảm kiểm tra quyền phía server.
- Phân tách quyền upload khỏi quyền publish nếu triển khai cho nhiều người dùng.
- Kiểm tra file type/content, kích thước, checksum và metadata; không tin extension `.onnx` đơn lẻ.
- Lưu audit log cho upload, publish, disable, retire và thay đổi cấu hình Selector.
- Không đưa credential hoặc secret vào mã nguồn.
- Nếu triển khai ngoài LAN tin cậy, dùng TLS cho client-server/peer theo chính sách phù hợp và cân nhắc chữ ký/kiểm chứng nguồn artifact.
- Giới hạn tốc độ và quota theo người dùng/client để chống cạn tài nguyên.

## 12. Tiêu chí nghiệm thu Version 2

### Quản lý model và SQL

- [ ] Metadata model/task/variant được lưu SQL và vẫn tồn tại sau khi restart.
- [ ] Upload một `.onnx` mới từ web tạo record staging và chạy validation bất đồng bộ.
- [ ] Model hợp lệ có thể publish; model lỗi hoặc thiếu metadata bị giữ lại và hiển thị lý do.
- [ ] File được lưu theo thư mục tác vụ, ví dụ `models/CV/ResNet50_FP32.onnx`; UI nhóm danh mục theo task.
- [ ] Thêm artifact mới cùng contract không yêu cầu sửa code Selector theo tên model.
- [ ] Disable/retire một variant ngăn nó được chọn cho request mới; request đang chạy được xử lý theo chính sách đã định.
- [ ] Hash SQL/Registry khớp artifact thực tế; restore DB + artifact store khôi phục được hệ thống.

### Web UI/UX

- [ ] Dashboard hiển thị KPI từ API/SQL thực, có lọc khoảng thời gian.
- [ ] Trang model hỗ trợ tìm kiếm, lọc, phân trang và chi tiết Model Card.
- [ ] Upload có tiến độ, kết quả validation, lỗi rõ ràng và retry.
- [ ] Trang devices hiển thị khả năng peer, trạng thái thiết bị và thống kê fallback.
- [ ] Trang feedback so sánh download/load/latency/RAM theo task/variant/device class.
- [ ] Loading/empty/error/success state đều được thiết kế; layout dùng được trên laptop.

### Backend, API và bảo mật

- [ ] API quản trị được bảo vệ bằng xác thực/phân quyền.
- [ ] API client Version 1 vẫn hoạt động sau khi thay Registry JSON bằng SQL.
- [ ] Không có controller nào tự chứa logic chọn model/phân phối phức tạp.
- [ ] Background validation không giữ request HTTP mở vô hạn.
- [ ] Có structured logs, correlation IDs và audit events.
- [ ] Có migration, backup/restore và test cho dữ liệu lớn.
- [ ] Client vẫn tải được chunk binary với fallback và xác minh SHA-256.

## 13. Bàn giao Version 2

1. Source frontend web và tài liệu khởi chạy.
2. Source backend/API, schema SQL và migrations.
3. Tài liệu thiết kế database/ERD.
4. API spec OpenAPI/Swagger cho API quản trị.
5. Luồng upload, validation, publish, disable và retire model.
6. Dashboard, Model Catalog, Model Detail, Devices/P2P và Feedback/Benchmark.
7. Tích hợp đầy đủ API phân phối Protocol 1.0 hoặc adapter tương thích.
8. Công cụ import Registry JSON từ Version 1 sang SQL và kiểm tra số lượng/hash sau migration.
9. Hướng dẫn cấu hình, secrets, backup/restore, triển khai và rollback.
10. Test tự động, test end-to-end và kịch bản demo.

## 14. Kế hoạch triển khai theo milestone

Không ấn định ngày nếu chưa biết nhân lực và năng lực thực tế. Nên nghiệm thu theo milestone:

| Milestone | Version | Kết quả cần có |
|---|---|---|
| M1 – Model Registry | V1 | Registry động, folder theo task, kiểm tra ONNX và hash |
| M2 – Distribution API | V1 | Register/request/plan/chunk/have/feedback |
| M3 – Client + P2P | V1 | Client tải chunk, chia sẻ peer và fallback |
| M4 – AI inference demo | V1 | Nạp ONNX, chạy ảnh, trả Top-5 và gửi metrics |
| M5 – Reproducible demo | V1 | Hai client, kịch bản P2P/fallback, hướng dẫn chạy và test |
| M6 – SQL + backend admin | V2 | Database schema, migrations, model repository và API quản trị |
| M7 – Web Model Catalog | V2 | Danh mục, chi tiết model, upload/validation/publish |
| M8 – Dashboard/feedback | V2 | Metrics, devices, P2P, feedback và bộ lọc |
| M9 – Security/operations | V2 | Auth/RBAC, audit, backup/restore, observability |
| M10 – Migration + regression | V2 | Nhập dữ liệu V1, API client vẫn chạy, nghiệm thu end-to-end |

## 15. Rủi ro và cách kiểm soát

| Rủi ro | Cách kiểm soát |
|---|---|
| ONNX mới không tương thích input/output client | Model Card + contract validation + test inference trước `ready` |
| Model Selector thiếu dữ liệu để học | Dùng baseline minh bạch, thu thập benchmark thật, đánh giá ranker trước khi kích hoạt |
| Peer không kết nối được | Timeout/retry có giới hạn và fallback Server |
| Hash ví dụ bị dùng nhầm trong môi trường thật | Tạo hash tự động từ file và có test xác minh |
| Thêm model làm vỡ client Protocol 1.0 | Chỉ thêm variant cùng contract qua schema cũ; task/contract mới cần version protocol mới |
| SQL và filesystem mất đồng bộ | Quy trình publish nguyên tử, trạng thái lifecycle, reconciliation job và backup/restore test |
| Ghi ONNX binary trong SQL làm database tăng quá nhanh | Mặc định dùng artifact storage riêng, SQL giữ metadata/hash/URI; chỉ dùng BLOB sau khi đánh giá |
| Feedback gây quyết định sai | Versioning dữ liệu, lọc record lỗi, không nhầm confidence với accuracy, so sánh với baseline |
| Demo không tái lập được | Cấu hình mẫu, runbook, script, test tự động và ghi rõ phiên bản runtime |

## 16. Quyết định cần chốt trước khi bắt đầu

1. **Client demo V1:** chọn Python CLI trên hai máy Windows/Linux làm mục tiêu nghiệm thu chính; client Safari/điện thoại là mục tiêu bổ sung hay bắt buộc?
2. **Model Selector:** V1 có bắt buộc dùng ML ranker đã huấn luyện, hay chấp nhận baseline rule-based cho demo trước rồi thay ranker sau khi có dữ liệu?
3. **SQL V2:** chốt SQL Server hay PostgreSQL. Khuyến nghị chọn một hệ quản trị duy nhất.
4. **Model binary:** xác nhận phương án mặc định SQL lưu metadata và file ONNX lưu ở filesystem/object storage; chỉ chuyển sang BLOB nếu có yêu cầu bắt buộc.
5. **Auth V2:** xác định demo chỉ trong LAN hay cần triển khai cho nhiều người dùng/mạng không tin cậy.

Các quyết định này ảnh hưởng trực tiếp đến effort và tiêu chí nghiệm thu; chúng nên được ghi lại trước khi bắt đầu milestone tương ứng.

## 17. Tóm tắt

**Version 1** tập trung chứng minh hệ thống chạy end-to-end: Server, Client, Registry động, bốn model ONNX ban đầu, inference, chunk, SHA-256, P2P/fallback và feedback. Không cần chờ web UI hoặc SQL mới có thể demo.

**Version 2** xây lớp nền tảng quản trị: web UI/UX, backend phân lớp, SQL cho danh mục/metadata/telemetry, luồng upload–validation–publish, dashboard, phân quyền và migration. API phân phối cho Client cần duy trì tương thích.

Khi hoàn thành cả hai phiên bản, thêm một file ONNX mới cùng tác vụ/contract sẽ là quy trình nhập và phát hành dữ liệu, không phải sửa code chọn model theo từng tên file. Muốn thêm tác vụ mới thì bổ sung task contract, client support và version giao thức phù hợp.
