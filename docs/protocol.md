# Giao thức PBL4 – phiên bản 1.0

Tài liệu này chốt định dạng các tin nhắn giữa **client** (agent trên điện thoại, trình duyệt) và **server** (Python FastAPI trên laptop), cùng tin nhắn giữa hai client khi chia sẻ P2P. Server và agent phải theo đúng tài liệu này; muốn đổi thì sửa tài liệu trước, tăng `protocol_version`, rồi mới sửa code.

- File mẫu cho từng tin nhắn: `docs/examples/`
- Schema để kiểm tra tự động: `docs/protocol.schema.json`
- Kiểm tra file mẫu khớp schema: `python tools/protocol/validate_examples.py` (script chưa có, bổ sung sau)
- Danh mục phiên bản model (Registry): `registry/model_registry.json`, sinh bằng `python tools/build_registry.py`

## 0. Luồng tổng thể

```
Client                                Server                       Client khác (peer)
  │ 1. POST /api/register  ─────────────▶│
  │◀──────────── device_class ───────────│
  │ (tuỳ chọn) GET /api/probe ──────────▶│  đo băng thông
  │ 2. POST /api/request ───────────────▶│  chọn phiên bản + lập kế hoạch tải
  │◀──────────── variant + plan ─────────│
  │ 3. GET /api/chunks/{id}/{i} ────────▶│  miếng có source = "server"
  │ 3. GET /chunks/{id}/{i} ────────────────────────────────────────▶│  miếng có source = "peer"
  │ 3. POST /api/have ──────────────────▶│  báo miếng đã kiểm tra đúng
  │◀──────────── continue / switch ──────│
  │    (chạy model trên ảnh)             │
  │ 4. POST /api/feedback ──────────────▶│  gửi số đo thực tế
  │◀──────────── keep / switch ──────────│
```

## 1. Quy ước chung

| Mục | Quy ước |
|---|---|
| Giao thức | HTTP/1.1, mạng nội bộ (cùng Wi-Fi hoặc hotspot laptop). Server mặc định cổng **8000**, peer mặc định cổng **9000** |
| Dữ liệu | JSON, mã hoá UTF-8, header `Content-Type: application/json`. Riêng miếng model và `/api/probe` trả dữ liệu nhị phân `application/octet-stream` |
| Tên trường | snake_case. Server Python dùng Pydantic, khai báo tên trường đúng snake_case nên không cần đổi tên khi serialize |
| Đơn vị | Ghi trong tên trường: `_bytes` (byte, số nguyên), `_ms` (mili giây), `_s` (giây), `_mb` (MB = 1.048.576 byte), `_kbps` (**1 kbps = 1.000 bit/giây**; 2 Mbps = 2000 kbps) |
| Tỉ lệ | Số thực từ 0 đến 1 (`top1_accuracy`, `top1_confidence`), không dùng phần trăm |
| `device_id` | Client tự đặt, duy nhất trong mạng, chỉ gồm `A-Z a-z 0-9 _ -`, tối đa 32 ký tự |
| `variant_id` | Đúng trường `variant_id` trong `registry/model_registry.json` (ví dụ `resnet50_int8`) |
| Mã băm | SHA-256, viết thường dạng hex, 64 ký tự |
| Miếng (chunk) | `chunk_size` = **1.048.576 byte**. Miếng thứ `i` bắt đầu ở byte `i × chunk_size`; miếng cuối có thể ngắn hơn |
| IP của client | Client **không** gửi IP. Server lấy từ chính kết nối |
| Trường không có giá trị | Gửi `null`, không bỏ trường (trừ khi bảng ghi "tuỳ chọn") |

## 2. Giai đoạn 1 – Đăng ký

### `POST /api/register`

**Client gửi** (`examples/01_register_request.json`)

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `protocol_version` | string | có | Luôn là `"1.0"` |
| `device_id` | string | có | Tên máy (xem quy ước) |
| `platform` | string | có | Một trong: `android`, `android_browser`, `ios_browser`, `windows`, `linux` |
| `cpu_arch` | string | có | Kết quả `uname -m` (ví dụ `aarch64`) hoặc `x86_64` |
| `ram_mb` | integer | có | Tổng RAM của máy |
| `runtime` | string | có | Thư viện chạy model và phiên bản, ví dụ `onnxruntime 1.25.1` |
| `peer_port` | integer hoặc null | có | Cổng chia sẻ miếng cho máy khác. `null` nếu không chia sẻ được (trình duyệt) |

**Server trả về** – HTTP 200 (`examples/02_register_response.json`)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `protocol_version` | string | `"1.0"` |
| `device_id` | string | Lặp lại `device_id` |
| `device_class` | string | Lớp cấu hình, xem mục 7. Các máy cùng lớp nhận cùng phiên bản và được ghép P2P |
| `chunk_size` | integer | 1048576 |
| `peer_enabled` | boolean | `true` nếu server sẽ chỉ máy khác đến lấy miếng từ máy này |

Gọi lại `/api/register` với cùng `device_id` là hợp lệ (máy khởi động lại); server cập nhật thông tin và IP.

## 3. Giai đoạn 2 – Xin model

### `GET /api/probe?size_kb=256` (tuỳ chọn, đo băng thông)

Trả về `size_kb` × 1024 byte dữ liệu ngẫu nhiên, **chịu cùng giới hạn tốc độ** như khi tải miếng. Client đo thời gian nhận rồi tính `bandwidth_kbps = bytes × 8 / 1000 / giây`. `size_kb` từ 16 đến 1024, mặc định 256.

### `POST /api/request`

**Client gửi** (`examples/03_request_request.json`)

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `device_id` | string | có | Đã đăng ký ở giai đoạn 1 |
| `task` | string | có | Hiện chỉ có `image_classification` |
| `bandwidth_kbps` | number hoặc null | có | Băng thông đo được; `null` nếu chưa đo (server dùng giới hạn tốc độ đang cấu hình) |
| `deadline_s` | number | có | Model phải tải xong trong bao nhiêu giây |
| `free_storage_mb` | number hoặc null | có | Dung lượng trống; `null` nếu không đo được |
| `preferred_variant` | string hoặc null | có | `null`: server tự chọn. Có giá trị: server gửi đúng bản đó (dùng khi "cùng yêu cầu" để ghép P2P, hoặc sau khi server bảo `switch`) |
| `have_variants` | mảng string | có | Các phiên bản máy đã có đầy đủ; có thể rỗng |

**Server trả về** – HTTP 200 (`examples/04_request_response.json`)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `request_id` | string | Mã của lần xin này, client gửi kèm ở giai đoạn 3 và 4 |
| `variant` | object | Thông tin phiên bản được chọn, lấy từ Registry `registry/model_registry.json` (bảng dưới) |
| `reason` | string | Lý do chọn, viết cho người đọc (hiện lên dashboard) |
| `estimated_download_s` | number hoặc null | Thời gian tải ước tính |
| `plan` | mảng object | Kế hoạch tải từng miếng, sắp theo `index`. **Rỗng** nếu phiên bản đã có trong `have_variants` |

Khối `variant`:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `id` | string | `variant_id` |
| `file` | string | Tên file để lưu, ví dụ `MobileNetV2_INT8.onnx` |
| `base_model`, `level`, `precision` | string, integer, string | Họ model, bậc nén (0 = bản gốc), kiểu số (`fp32`, `int8`) |
| `size_bytes` | integer | Kích thước cả file |
| `sha256` | string | Mã băm cả file, kiểm tra lần cuối sau khi ghép |
| `chunk_size`, `chunk_count` | integer | Kích thước miếng và số miếng |
| `top1_accuracy` | number | Độ chính xác công bố |
| `input_shape`, `input_dtype` | mảng integer, string | Dạng đầu vào, ví dụ `[1, 3, 224, 224]`, `float` |
| `normalize` | object | `{"mean": [...], "std": [...]}` để chuẩn bị ảnh |

Mỗi phần tử của `plan`:

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `index` | integer | Số thứ tự miếng, từ 0 |
| `offset` | integer | Vị trí byte bắt đầu = `index × chunk_size` |
| `size_bytes` | integer | Kích thước miếng |
| `sha256` | string | Mã băm của miếng, **lấy từ Registry của server** |
| `source` | string | `server` hoặc `peer` |
| `url` | string | Địa chỉ đầy đủ để tải miếng |
| `fallback_url` | string hoặc null | Với `peer`: địa chỉ tải lại từ server khi peer lỗi. Với `server`: `null` |

## 4. Giai đoạn 3 – Tải miếng và báo tiến độ

### `GET /api/chunks/{variant_id}/{index}` (server) và `GET /chunks/{variant_id}/{index}` (peer)

- Trả về đúng các byte của miếng, `Content-Type: application/octet-stream`, có `Content-Length`.
- Miếng từ server chịu giới hạn tốc độ (token bucket dùng chung cho mọi kết nối). Miếng giữa các peer không giới hạn.
- Peer trả **404** nếu chưa có miếng đó.
- Client **luôn** tự tính SHA-256 của miếng nhận được và so với `plan[].sha256`. Không tin bất kỳ header nào.

### `POST /api/have`

Gửi sau mỗi lần có thêm miếng đã kiểm tra đúng (có thể gộp vài miếng một lần).

**Client gửi** (`examples/05_have_request.json`)

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `device_id` | string | có | |
| `request_id` | string | có | Từ giai đoạn 2 |
| `variant_id` | string | có | |
| `chunks` | mảng integer | có | Các miếng **mới** kiểm tra đúng kể từ lần báo trước. Server tự gộp |
| `complete` | boolean | có | `true` khi đủ mọi miếng **và** mã băm cả file khớp `variant.sha256` |
| `bandwidth_kbps` | number hoặc null | có | Tốc độ tải thực tế từ server trong các miếng vừa rồi; `null` nếu các miếng vừa rồi đều từ peer |
| `bad_chunks` | mảng object | có | Miếng nhận về bị sai mã băm: `{"index": 2, "source_url": "..."}`. Có thể rỗng. Server ngừng chỉ đến nguồn gửi sai |

**Server trả về** – HTTP 200 (`examples/06_have_response.json`)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `ok` | boolean | `true` |
| `action` | string | `continue`: tải tiếp. `switch`: dừng tải, xin phiên bản `new_variant_id` (băng thông tụt, không kịp hạn) |
| `new_variant_id` | string hoặc null | Có giá trị khi `action` = `switch` |
| `reason` | string hoặc null | Lý do khi `switch` |

Phiên bản 1.0: server được phép luôn trả `continue`. Việc đổi phiên bản giữa chừng (hướng 4 trong sheet "Hướng nghiên cứu") làm sau khi luồng cơ bản chạy ổn.

## 5. Giai đoạn 4 – Phản hồi sau khi chạy

### `POST /api/feedback`

**Client gửi** (`examples/07_feedback_request.json`)

| Trường | Kiểu | Bắt buộc | Ý nghĩa |
|---|---|---|---|
| `device_id`, `request_id`, `variant_id` | string | có | |
| `download_ms` | number | có | Từ lúc nhận `plan` đến lúc `complete` |
| `chunks_from_peer`, `chunks_from_server` | integer | có | Số miếng thực tế lấy từ mỗi nguồn (tính cả miếng tải lại) |
| `load_ms` | number | có | Thời gian mở model |
| `latency_ms_median` | number | có | Trung vị thời gian suy luận một ảnh |
| `runs` | integer | có | Số lần chạy để lấy trung vị |
| `peak_ram_mb` | number hoặc null | có | `null` nếu không đo được (trình duyệt) |
| `top1_label` | string hoặc null | có | Kết quả nhận diện của ảnh thử |
| `top1_confidence` | number hoặc null | có | Độ tự tin của kết quả, 0 đến 1 |

**Server trả về** – HTTP 200 (`examples/08_feedback_response.json`)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `action` | string | `keep`: giữ bản đang dùng. `switch`: đổi sang `new_variant_id` |
| `new_variant_id` | string hoặc null | Có giá trị khi `switch` |
| `applies_to_class` | string | Quyết định áp dụng cho **cả lớp thiết bị**, để các máy cùng lớp vẫn dùng chung file (giữ được P2P) |
| `reason` | string | Lý do, viết cho người đọc |

Khi nhận `switch`, client gọi lại `/api/request` với `preferred_variant` = `new_variant_id`.

### `POST /api/feedback` – đánh giá của người dùng

Cùng endpoint, phân biệt bằng trường `feedback_id` (gói số đo ở trên không có trường này nên
client cũ không bị ảnh hưởng). Gửi từ `client.feedback_cli`; không cần `request_id` còn hiệu lực
hay đăng ký lại, vì client có thể gửi bù sau khi server khởi động lại.

**Client gửi** (`examples/10_manual_feedback_request.json`)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `feedback_id` | string | Mã duy nhất do client sinh (`fb-...`), dùng để chống ghi trùng khi gửi lại |
| `device_id`, `variant_id` | string | Thiết bị và model được đánh giá |
| `rating` | integer | Điểm 1 đến 5 |
| `correct` | boolean | Dự đoán có đúng không |
| `prediction`, `confidence` | string/number hoặc null | Dự đoán top-1 của lần detect và độ tự tin 0 đến 1 |
| `expected_label` | string hoặc null | Nhãn thực tế nếu người dùng biết |
| `comment` | string hoặc null | Nhận xét, tối đa 1000 ký tự |
| `request_id`, `detected_at` | string hoặc null | Lần detect liên quan (null nếu detect offline) |
| `created_at` | string | Thời điểm người dùng nhập (ISO 8601) |

**Server trả về** – HTTP 200 (`examples/11_manual_feedback_response.json`)

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `ok` | boolean | `true`: server đã lưu; client chỉ đánh dấu `sent` khi nhận được |
| `feedback_id` | string | Mã vừa nhận |
| `status` | string | `accepted`: ghi mới. `duplicate`: đã nhận trước đó, không ghi lại |
| `received_at` | string | Lúc server nhận lần đầu |
| `evaluation` | object | `verdict` (`positive`/`neutral`/`negative`), `needs_review` (model có ≥ 5 lượt mà đúng < 50 %), thống kê của model và `reason` |

Server ghi mỗi đánh giá một dòng trong `data/feedback/manual-feedback-YYYYMMDD.jsonl` và một
dòng log (thời gian nhận, thiết bị, model, kết quả đánh giá).

## 6. Lỗi

Mọi lỗi trả về cùng một dạng (`examples/09_error.json`):

```json
{"error": {"code": "NO_VARIANT_FITS", "message": "Không có phiên bản nào tải kịp trong 10 giây ở 500 kbps."}}
```

| HTTP | `code` | Khi nào |
|---|---|---|
| 400 | `BAD_REQUEST` | JSON sai dạng, thiếu trường, sai kiểu |
| 404 | `UNKNOWN_DEVICE` | `device_id` chưa đăng ký |
| 404 | `UNKNOWN_VARIANT` | `variant_id` không có trong Registry hoặc chưa ở trạng thái `ready` |
| 404 | `CHUNK_OUT_OF_RANGE` | `index` < 0 hoặc ≥ `chunk_count` |
| 409 | `VERSION_MISMATCH` | `protocol_version` khác `"1.0"` |
| 422 | `NO_VARIANT_FITS` | Không phiên bản nào vừa hạn và dung lượng |
| 500 | `INTERNAL` | Lỗi phía server |

## 7. Lớp thiết bị (`device_class`)

Dạng `{platform}_{arch}_{ram}`:

- `platform`: giữ nguyên giá trị client gửi.
- `arch`: `arm64` (với `aarch64`, `arm64`), `arm32` (với `armv7l`, `armv8l`), `x64` (với `x86_64`, `amd64`).
- `ram`: `lt4gb` (dưới 4096 MB), `4-8gb` (4096 đến 8192 MB), `gt8gb` (trên 8192 MB).

Ví dụ: `android_arm64_4-8gb`, `ios_browser_arm64_4-8gb`. Tách theo `platform` vì số đo cho thấy cùng một bản nén chạy nhanh trên máy này nhưng chậm trên máy khác (INT8 không nhanh hơn trên Safari).

## 8. Việc client phải làm (tóm tắt)

1. Đăng ký; (tuỳ chọn) đo băng thông bằng `/api/probe`.
2. Xin model. Nếu `plan` rỗng thì bỏ qua bước 3.
3. Tải tối đa **4 miếng cùng lúc**. Mỗi miếng: tải theo `url` (chờ tối đa 10 giây), tính SHA-256, khớp thì ghi vào file tại `offset`, sai hoặc lỗi thì ghi vào `bad_chunks` và tải lại từ `fallback_url`. Báo `/api/have` định kỳ.
4. Đủ miếng: kiểm tra SHA-256 cả file, gửi `/api/have` với `complete: true`. Từ lúc này mở cổng `peer_port` phục vụ miếng cho máy khác.
5. Mở model, chạy ảnh thử, gửi `/api/feedback`. Nếu `switch` thì quay lại bước 2.

## 9. Phân công thực hiện

| Phần | Người | Đầu vào từ tài liệu này |
|---|---|---|
| Thiết kế tài liệu `.md`: `protocol.md`, tổng quan hệ thống, yêu cầu triển khai, skills | Phan Vũ Long | Toàn bộ tài liệu |
| Huấn luyện Model Selector: mô hình chọn phiên bản model nén theo cấu hình thiết bị và băng thông tại thời điểm yêu cầu | Huỳnh Tấn Lực | Mục 3 (`/api/request`), 5 (feedback làm dữ liệu huấn luyện), 7 (`device_class`) |
| Code server và client: các endpoint, giới hạn tốc độ, lập kế hoạch P2P, Registry, bộ chọn rule-based dự phòng, agent client, đo đạc | Nguyễn Văn Vũ | Mục 1 đến 8 |

Model Selector của Lực được gắn vào server qua một interface chung. Khi Selector chưa sẵn sàng hoặc trả lỗi, server dùng bộ chọn rule-based (theo `tong_quan_he_thong.md`, mục 7.5), nên việc huấn luyện không chặn luồng phân phối.

## 10. Lịch sử thay đổi

| Phiên bản | Ngày | Nội dung |
|---|---|---|
| 1.0 | 13/10/2026 | Bản đầu tiên cho task Implement |
| 1.0 | 14/10/2026 | Sửa tài liệu, **không đổi định dạng tin nhắn**: server dùng Python FastAPI; danh mục model là Registry `registry/model_registry.json` thay cho `models/manifest.json`; `UNKNOWN_VARIANT` áp dụng cả với phiên bản chưa `ready`; cập nhật phân công |
| 1.0 | 14/10/2026 | **Bổ sung, tương thích ngược**: `/api/feedback` nhận thêm đánh giá của người dùng (có `feedback_id`, mẫu 10 và 11); các tin nhắn cũ giữ nguyên |