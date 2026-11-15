# Báo cáo triển khai Version 1: Server và Client

- Nhánh: `vu`, thực hiện đêm 14/10/2026 (02:10–06:00), chưa push.
- Phạm vi: Phase 0–5 theo yêu cầu: chuẩn bị, server FastAPI, client agent, kiểm thử, P2P và
  fallback, tài liệu.
- Nguồn hợp đồng: `docs/protocol.schema.json` > `docs/protocol.md` > `rules.md` >
  `yeu_cau_trien_khai.md` > `tong_quan_he_thong.md` > `skills/`.

## 0. Tóm tắt kết quả

| Phase | Trạng thái |
|---|---|
| 0. Chuẩn bị (pyproject, nhãn, ảnh, validate 9 file mẫu) | Xong |
| 1. Server (6 endpoint, lỗi, selector, token bucket, log) | Xong |
| 2. Client agent (register → probe → request → tải → have → inference → feedback) | Xong |
| 3. Kiểm thử | Xong: 189 passed in 20.77s |
| 4. P2P và fallback | Xong (kiểm trên một máy với nhiều tiến trình; chưa thử giữa hai máy thật qua hotspot) |
| 5. Tài liệu | Xong: `docs/run_demo.md`, báo cáo này, `README.md` |

Lệnh chạy:

```cmd
uvicorn server.main:app --host 0.0.0.0 --port 8000
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg
```

## 1. File đã tạo hoặc sửa

Không sửa file nào trong danh sách cấm (`docs/protocol.md`, `docs/protocol.schema.json`,
`docs/examples/*`, `registry/model_registry.json`, `metadata/model_cards/*`, `rules.md`,
`tong_quan_he_thong.md`, `yeu_cau_trien_khai.md`, `skills/*`). `tools/build_registry.py` được
chạy lại, kết quả `4 variant, 4 ready`; Registry chỉ khác `generated_at` nên đã khôi phục, không commit.

| File | Mới/Sửa | Nội dung |
|---|---|---|
| `pyproject.toml` | Mới | Dependency runtime (fastapi, uvicorn, requests, onnxruntime, numpy, pillow), `dev` (pytest, jsonschema, httpx), cấu hình pytest và ruff |
| `.gitignore` | Sửa | Thêm `data/`, `*.part`, `*.onnx`, ảnh trong `samples/` |
| `README.md` | Sửa | Mô tả dự án, chạy nhanh, link `docs/run_demo.md` |
| `server/config.py` | Mới | 7 biến môi trường `PBL4_*`, đường dẫn tương đối tính từ gốc repo |
| `server/errors.py` | Mới | `ProtocolError(code, message, http_status)`, bảng code → HTTP status |
| `server/schemas.py` | Mới | Model Pydantic cho đủ 13 `$defs` có tin nhắn, `extra="forbid"`, tin nhắn client đặt thêm `strict=True` |
| `server/registry.py` | Mới | Đọc Registry, lọc `ready`, kiểm chunk, chặn path traversal, tự nạp lại khi file đổi |
| `server/devices.py` | Mới | Lưu thiết bị, IP từ kết nối, tính `device_class` |
| `server/selector.py` | Mới | `ModelSelector`, bộ lọc cứng, `RuleBasedSelector` (`rule-v1`), `FallbackSelector`, `MLSelectorPlaceholder` |
| `server/planner.py` | Mới | Lập plan chunk, chia nửa đầu cho peer |
| `server/ratelimit.py` | Mới | Token bucket dùng chung cho chunk và probe |
| `server/state.py` | Mới | `request_id`, tiến độ chunk, ghi nhận `complete`, peer bị báo lỗi, variant ghi nhớ theo lớp |
| `server/feedback.py` | Mới | Quyết định keep/switch, ghi JSONL |
| `server/service.py` | Mới | Điều phối nghiệp vụ cho từng endpoint, log theo `request_id` |
| `server/main.py` | Mới | Tạo app, route, exception handler, log console + file theo ngày |
| `client/device_info.py` | Mới | platform, cpu_arch, RAM (ctypes / `/proc/meminfo`), runtime, dung lượng trống |
| `client/api.py` | Mới | Dựng payload theo protocol, gọi REST, `ApiError` |
| `client/downloader.py` | Mới | Tải 4 miếng song song, SHA-256, fallback, tải tiếp, báo `/api/have`, kho cục bộ |
| `client/inference.py` | Mới | Tiền xử lý, ONNX Runtime, Top-5, đo load/latency/RAM |
| `client/peer.py` | Mới | Peer service `GET /chunks/{variant_id}/{index}` |
| `client/agent.py` | Mới | CLI 7 bước, `--follow-switch`, `--serve` |
| `tools/download_assets.py` | Mới | Tải `samples/imagenet_synset.txt` (kiểm đủ 1000 dòng) và 2 ảnh Wikimedia |
| `tools/protocol/validate_examples.py` | Mới | Kiểm 9 file mẫu theo `$defs`; test dùng chung |
| `samples/imagenet_synset.txt`, `samples/README.md` | Mới | Nhãn ImageNet và nguồn ảnh |
| `tests/*.py` (13 file) | Mới | Bộ test, xem mục 4 |
| `docs/run_demo.md` | Mới | Hướng dẫn demo trên Windows `cmd` |
| `docs/implementation_report_v1.md` | Mới | Báo cáo này |

## 2. Hành vi của từng endpoint

Mọi lỗi có dạng `{"error": {"code", "message"}}`, `message` tiếng Việt, không chứa đường dẫn,
`storage_path` hay stack trace.

| Endpoint | Hành vi |
|---|---|
| `POST /api/register` | Đọc JSON; nếu có `protocol_version` khác `"1.0"` trả **409 `VERSION_MISMATCH`** trước khi validate. Validate `RegisterRequest` (sai dạng, thiếu trường, sai kiểu, thừa trường → **400**). IP lấy từ kết nối. `device_class = {platform}_{arch}_{ram}`; kiến trúc lạ → 400. Gọi lại cùng `device_id` thì cập nhật. Trả `chunk_size = 1048576`, `peer_enabled` theo `PBL4_PEER_ENABLED`. |
| `GET /api/probe?size_kb=256` | `size_kb` 16–1024 (mặc định 256), ngoài khoảng hoặc không phải số → 400. Trả đúng `size_kb × 1024` byte ngẫu nhiên, `application/octet-stream`, có `Content-Length`, đi qua token bucket. |
| `POST /api/request` | Thiết bị lạ → 404 `UNKNOWN_DEVICE`. `preferred_variant` không có hoặc không `ready` → 404 `UNKNOWN_VARIANT`; hợp lệ thì bỏ qua Selector. Ngược lại: Registry (`ready`) → bộ lọc cứng (task, dung lượng, hạn tải) → Selector. Không còn ứng viên → **422 `NO_VARIANT_FITS`**, message nêu hạn, băng thông và từng variant bị loại. `request_id` dạng `r-0001` tăng dần. `variant` đúng 13 trường. `estimated_download_s = size_bytes × 8 / 1000 / băng thông` (băng thông của request, nếu null thì `PBL4_RATE_LIMIT_KBPS`, nếu 0 thì `null`). `plan` đủ mọi miếng, URL `http://`. `have_variants` chỉ được tin khi server đã ghi nhận `complete` của chính thiết bị đó (khi đó `plan = []`). `reason` tiếng Việt có số cụ thể. |
| `GET /api/chunks/{variant_id}/{index}` | Variant không có / không `ready` → 404 `UNKNOWN_VARIANT`; `index < 0` hoặc `≥ chunk_count` → 404 `CHUNK_OUT_OF_RANGE`; index không phải số → 400. Đường dẫn file resolve trong `PBL4_MODEL_STORE`, chặn path traversal; file thiếu hoặc kích thước khác Registry → 500 `INTERNAL` (chi tiết chỉ ghi log). `seek` + `read` từng khối 16 KiB, mỗi khối lấy token từ bucket, có `Content-Length`. |
| `POST /api/have` | Thiết bị lạ → 404 `UNKNOWN_DEVICE`; `request_id` lạ hoặc thuộc thiết bị khác → 400; `variant_id` khác variant của request → 404 `UNKNOWN_VARIANT`; index ngoài phạm vi → 404 `CHUNK_OUT_OF_RANGE`. Gộp chunk vào tiến độ, log `bad_chunks` và ngừng giao peer từng gửi sai. `complete: true` chỉ được ghi nhận khi server đã thấy đủ mọi miếng. Luôn trả `continue` (protocol 1.0 cho phép). |
| `POST /api/feedback` | Kiểm ID như `/api/have`. Ghi một dòng JSONL vào `data/feedback/feedback-YYYYMMDD.jsonl` (toàn bộ feedback + `timestamp` UTC, `device_class`, `platform`, `runtime`, `selector_version`, `selector_reason`, `decision`, `data_kind`). Mặc định `keep`; nếu `latency_ms_median > PBL4_LATENCY_SLO_MS` và có variant `ready` nhỏ hơn cùng task thì `switch` sang bản nhỏ hơn có top-1 cao nhất, và ghi nhớ cho cả `device_class`. |
| Route/phương thức không có | 404/405 giữ nguyên status, body là error envelope với `code = "BAD_REQUEST"`. |
| Lỗi bất ngờ | 500 `INTERNAL`, stack trace chỉ ghi vào log server. |

Selector `rule-v1`: bộ lọc cứng loại variant sai task, vượt dung lượng trống, hoặc tải không kịp
hạn; sau đó, nếu lớp thiết bị có variant được ghi nhớ từ feedback và nó vẫn qua bộ lọc thì chọn
nó, ngược lại chọn top-1 cao nhất (bằng nhau chọn file nhỏ hơn). Ngưỡng với hạn 60 s, tính từ
`size_bytes` thật:

| Variant | Kích thước | Băng thông tối thiểu | Chọn khi |
|---|---|---|---|
| `resnet50_fp32` | 102 576 593 B | 13 676.9 kbps | ≥ 13 677 kbps |
| `resnet50_int8` | 25 816 052 B | 3 442.1 kbps | 3 443 – 13 676 kbps |
| `mobilenetv2_fp32` | 13 964 571 B | 1 861.9 kbps | 1 862 – 3 442 kbps |
| `mobilenetv2_int8` | 3 655 033 B | 487.3 kbps | 488 – 1 861 kbps |
| (không có) | | | < 488 kbps → 422 |

Log: mỗi dòng của một lượt xin model bắt đầu bằng `[request_id]` (thiết bị và đăng ký, ứng viên,
bị loại kèm lý do, quyết định Selector, plan theo nguồn, tiến độ, miếng lỗi, hoàn tất, feedback).
Lượt bị từ chối 422 cũng được cấp một `request_id` để ghi log. Ghi ra console và
`data/logs/server-YYYYMMDD.log`.

## 3. Client agent

`python -m client.agent` in 7 bước có tiêu đề. Tham số đúng bảng yêu cầu, thêm `--follow-switch`,
`--serve`, `--labels`. Mã thoát: 0 thành công, 2 `NO_VARIANT_FITS`, 1 lỗi khác.

- Thiết bị: `platform` tự nhận biết, `cpu_arch = platform.machine()`, RAM bằng
  `GlobalMemoryStatusEx` (Windows) hoặc `/proc/meminfo`, `runtime = "onnxruntime <ver>"`,
  dung lượng trống bằng `shutil.disk_usage(cache_dir)`.
- Băng thông: `/api/probe` 256 KB nếu không có `--bandwidth`.
- Tải: tối đa 4 miếng song song, chờ tối đa 10 s không nhận được byte, SHA-256 từng miếng, ghi
  vào `.part` tạo sẵn đủ kích thước theo `offset`, sai/lỗi → `bad_chunks` + thử lại từ
  `fallback_url`, tối đa 3 lần mỗi miếng. Báo `/api/have` mỗi 4 miếng. Đủ miếng → kiểm SHA-256
  toàn file → đổi tên → `/api/have complete=true`. Miếng đã có đúng hash trên đĩa được dùng lại.
- Inference: `CPUExecutionProvider`, tên input lấy từ `session.get_inputs()[0].name`, tiền xử lý
  theo `variant` (resize cạnh ngắn 256 bilinear, cắt giữa theo `input_shape`, chuẩn hóa), 3 lần
  warm-up rồi `--runs` lần lấy trung vị, softmax, Top-5 với nhãn `samples/imagenet_synset.txt`,
  RAM đỉnh bằng `GetProcessMemoryInfo` (Windows) hoặc `ru_maxrss`.
- Peer: sau khi `complete`, mở `ThreadingHTTPServer` ở `peer_port`, chỉ phục vụ variant đã kiểm
  hash toàn file; `--serve` giữ chạy đến Ctrl+C.

## 4. Kiểm thử

Chạy: `python -m pytest -q` (Python 3.14.5, Windows 10). Kết quả thật lần chạy cuối:

```
........................................................................ [ 38%]
........................................................................ [ 76%]
.............................................                            [100%]
189 passed in 20.77s
```

| File test | Nội dung |
|---|---|
| `test_examples_schema.py` | 9 file mẫu khớp `$defs` (01→RegisterRequest … 09→Error); validator chặn trường thừa |
| `test_registry.py` | Mọi variant `ready` → khối `Variant` đúng 13 trường, hợp lệ schema; hash từng chunk khớp byte thật (bỏ qua nếu thiếu `.onnx`); chặn path traversal; chỉ phân phối `ready`; thêm variant mới vào Registry → thành ứng viên không cần sửa code |
| `test_api.py` | Từng endpoint qua `TestClient`: response hợp lệ schema; JSON sai dạng/thiếu/sai kiểu/thừa trường → 400; `protocol_version = "2.0"` → 409; thiết bị lạ → 404 `UNKNOWN_DEVICE`; `preferred_variant` lạ → 404 `UNKNOWN_VARIANT`; chunk ngoài phạm vi → 404 `CHUNK_OUT_OF_RANGE`; băng thông thấp → 422; `size_kb` ngoài khoảng → 400; byte chunk khớp SHA-256 Registry, chunk cuối ngắn hơn đúng; `have_variants` không được tin khi chưa `complete`; feedback ghi JSONL và `switch` khi vượt SLO; route lạ, phương thức sai, lỗi 500 đều dùng envelope |
| `test_selector.py` | Ngưỡng 100000 → `resnet50_fp32`, 5000 → `resnet50_int8`, 2000 → `mobilenetv2_fp32`, 600 → `mobilenetv2_int8`, 100 → không có; ngưỡng tính lại từ `size_bytes`; lọc dung lượng; lý do có số; ghi nhớ theo lớp; `FallbackSelector` khi primary ném lỗi, trả ID lạ, hoặc là `MLSelectorPlaceholder` |
| `test_device_class.py` | Đủ các trường hợp map `arch` (hoa/thường) và `ram` (biên 4095/4096/8192/8193), kiến trúc lạ |
| `test_ratelimit.py` | N byte ở giới hạn kbps mất khoảng N×8/1000/kbps giây (sai số ±15–35 %): một luồng, hai luồng dùng chung bucket, bản async, và `/api/probe` qua `TestClient` |
| `test_client_payloads.py` | Mọi payload client (register, request, have, feedback, cả nhánh `null`) hợp lệ schema; lọc `device_id`; `localhost` → `127.0.0.1` |
| `test_downloader.py` | Server giả trả byte sai → `bad_chunks` + tải lại từ `fallback_url`; peer tắt; peer 404; server luôn sai → lỗi, không `complete`, đúng 3 lần thử; hash toàn file sai; tải tiếp từ `.part`; `switch` dừng tải |
| `test_inference.py` | Tiền xử lý đúng shape/dtype/chuẩn hóa với ảnh tự sinh; nhãn 1000 dòng; softmax/Top-5; chạy cả 4 model thật, tên input đúng (`input`/`data`) |
| `test_e2e.py` | uvicorn thật trong thread + agent (gọi hàm) với ảnh Pillow, `--bandwidth 600` → `mobilenetv2_int8`: hash file đúng, đủ 5 nhãn, feedback trong JSONL, log có `request_id`; lần chạy thứ hai plan rỗng; mã thoát 2 khi 422; `--follow-switch` |
| `test_p2p.py` | Hai client cùng máy khác `device_id`/`peer_port`: B nhận ≥ 1 miếng `source: "peer"`; tắt peer → client sau vẫn hoàn tất nhờ fallback; planner chỉ chọn peer cùng lớp, có `peer_port`, đã `complete`, chưa bị báo lỗi, và tôn trọng `PBL4_PEER_ENABLED`; peer service không phục vụ file `.part` hay variant hỏng; cổng peer bận → đăng ký `peer_port = null` |

Formatter/linter: `ruff format` và `ruff check` (bộ luật E, F, W, I, B, UP, dòng 100 ký tự) sạch
trên `server/`, `client/`, `tests/`, `tools/protocol/`, `tools/download_assets.py`. `ruff` được cài
riêng làm công cụ, không thêm vào dependency của dự án.

`tools/protocol/validate_examples.py`: `9/9 file mẫu hợp lệ`.

## 5. Output thật của các lần chạy demo

Điều kiện đo: laptop Windows 10 Home, Intel Core i5-10300H, RAM 16 GB (client đọc được
16221 MB), Python 3.14.5, onnxruntime 1.31.0 (CPUExecutionProvider), fastapi 0.143.0,
uvicorn 0.54.0. Server và client chạy hai tiến trình riêng trên cùng máy (`localhost`), ảnh
`samples\cat.jpg` hoặc `samples\dog.jpg`. Giới hạn tốc độ đặt bằng `PBL4_RATE_LIMIT_KBPS`, client
**không** truyền `--bandwidth` mà tự đo bằng `/api/probe`. Đây là số đo của một lần chạy demo,
không phải benchmark có kiểm soát.

### 5.1. Bốn mức băng thông (server giới hạn tốc độ, client đo bằng probe)

| `PBL4_RATE_LIMIT_KBPS` | Probe đo được (kbps) | Variant chọn | Ước tính | Tải thật | Miếng | Mã thoát |
|---|---|---|---|---|---|---|
| 100,000 | 97,054 | `resnet50_fp32` | 8.5 s | 8.7 s | 98 từ server | 0 |
| 5,000 | 4,894 | `resnet50_int8` | 42.2 s | 41.4 s | 25 từ server | 0 |
| 2,000 | 2,001 | `mobilenetv2_fp32` | 55.8 s | 56.0 s | 14 từ server | 0 |
| 600 | 598 | `mobilenetv2_int8` | 48.9 s | 48.8 s | 4 từ server | 0 |

Lý do server trả (nguyên văn):

- 100,000 kbps: Băng thông 97054 kbps, hạn 60 s: chọn resnet50_fp32 (top-1 0.7497, tải ước tính 8.5 s), top-1 cao nhất trong 4 ứng viên đạt điều kiện.
- 5,000 kbps: Băng thông 4894 kbps, hạn 60 s: chọn resnet50_int8 (top-1 0.7477, tải ước tính 42.2 s), top-1 cao nhất trong 3 ứng viên đạt điều kiện; loại resnet50_fp32 vì cần 167.7 s > hạn 60 s.
- 2,000 kbps: Băng thông 2001 kbps, hạn 60 s: chọn mobilenetv2_fp32 (top-1 0.6948, tải ước tính 55.8 s), top-1 cao nhất trong 2 ứng viên đạt điều kiện; loại resnet50_fp32 vì cần 410 s > hạn 60 s, resnet50_int8 vì cần 103.2 s > hạn 60 s.
- 600 kbps: Băng thông 598 kbps, hạn 60 s: chọn mobilenetv2_int8 (top-1 0.6830, tải ước tính 48.9 s), top-1 cao nhất trong 1 ứng viên đạt điều kiện; loại mobilenetv2_fp32 vì cần 186.7 s > hạn 60 s, resnet50_fp32 vì cần 1371.1 s > hạn 60 s, resnet50_int8 vì cần 345.1 s > hạn 60 s.

Output đầy đủ ở mức 5000 kbps:

```

══ [1/7] Thông tin thiết bị ══════════════════════════════════════════
  device_id      : laptop5000
  platform       : windows
  cpu_arch       : AMD64
  ram_mb         : 16221
  runtime        : onnxruntime 1.31.0
  dung lượng trống: 45,737 MB (<scratchpad>\demo_out2\cache_5000)
  peer_port      : null (không chia sẻ)

══ [2/7] Đăng ký với server (POST /api/register) ═════════════════════
  Server: http://localhost:8000
  device_class   : windows_x64_gt8gb
  chunk_size     : 1,048,576 byte
  peer_enabled   : True

══ [3/7] Đo băng thông (GET /api/probe) ══════════════════════════════
  Nhận 262,144 byte trong 0.428 s → 4,894 kbps

══ [4/7] Xin model (POST /api/request) ═══════════════════════════════
  Gửi: băng thông 4,894.4 kbps, hạn 60 s, preferred_variant=None, have_variants=[]
  request_id     : r-0001
  Variant        : resnet50_int8 (ResNet50_INT8.onnx, 24.62 MB, 25 miếng, int8, top-1 0.7477)
  Lý do          : Băng thông 4894 kbps, hạn 60 s: chọn resnet50_int8 (top-1 0.7477, tải ước tính 42.2 s), top-1 cao nhất trong 3 ứng viên đạt điều kiện; loại resnet50_fp32 vì cần 167.7 s > hạn 60 s.
  Tải ước tính   : 42.2 s
  Kế hoạch tải   : 25 miếng từ server

══ [5/7] Tải model (25 miếng, tối đa 4 song song) ════════════════════
  [  1/25] miếng   0 ← server  1.00 MB    6.60 s  sha256 OK
  [  2/25] miếng   1 ← server  1.00 MB    6.66 s  sha256 OK
  [  3/25] miếng   3 ← server  1.00 MB    6.67 s  sha256 OK
  [  4/25] miếng   2 ← server  1.00 MB    6.74 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 4,974 kbps, bad_chunks=0 ← continue
  [  5/25] miếng   4 ← server  1.00 MB    6.71 s  sha256 OK
  [  6/25] miếng   5 ← server  1.00 MB    6.71 s  sha256 OK
  [  7/25] miếng   6 ← server  1.00 MB    6.72 s  sha256 OK
  [  8/25] miếng   7 ← server  1.00 MB    6.71 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 5,006 kbps, bad_chunks=0 ← continue
  [  9/25] miếng   8 ← server  1.00 MB    6.72 s  sha256 OK
  [ 10/25] miếng   9 ← server  1.00 MB    6.71 s  sha256 OK
  [ 11/25] miếng  10 ← server  1.00 MB    6.71 s  sha256 OK
  [ 12/25] miếng  11 ← server  1.00 MB    6.71 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 5,003 kbps, bad_chunks=0 ← continue
  [ 13/25] miếng  12 ← server  1.00 MB    6.71 s  sha256 OK
  [ 14/25] miếng  13 ← server  1.00 MB    6.71 s  sha256 OK
  [ 15/25] miếng  14 ← server  1.00 MB    6.71 s  sha256 OK
  [ 16/25] miếng  15 ← server  1.00 MB    6.71 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 5,003 kbps, bad_chunks=0 ← continue
  [ 17/25] miếng  16 ← server  1.00 MB    6.71 s  sha256 OK
  [ 18/25] miếng  17 ← server  1.00 MB    6.71 s  sha256 OK
  [ 19/25] miếng  18 ← server  1.00 MB    6.71 s  sha256 OK
  [ 20/25] miếng  19 ← server  1.00 MB    6.71 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 5,003 kbps, bad_chunks=0 ← continue
  [ 21/25] miếng  20 ← server  1.00 MB    6.70 s  sha256 OK
  [ 22/25] miếng  21 ← server  1.00 MB    6.71 s  sha256 OK
  [ 23/25] miếng  22 ← server  1.00 MB    6.70 s  sha256 OK
  [ 24/25] miếng  23 ← server  1.00 MB    6.72 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 4,998 kbps, bad_chunks=0 ← continue
  [ 25/25] miếng  24 ← server  0.62 MB    1.17 s  sha256 OK
  SHA-256 toàn file khớp c234f30975989788… → ResNet50_INT8.onnx
  → /api/have: 1 miếng mới, complete=True, băng thông server 4,723 kbps, bad_chunks=0 ← continue
  Tải xong: 25 miếng từ server, 0 từ peer, 0 dùng lại, 0 lần lỗi

══ [6/7] Chạy suy luận ONNX Runtime (resnet50_int8) ══════════════════
  Ảnh            : samples\cat.jpg
  Input          : 'data' [1, 3, 224, 224]
  Nạp model      : 77.4 ms
  Latency trung vị: 16.23 ms (20 lần, sau 3 lần warm-up)
  RAM đỉnh       : 118.9 MB

   #  Nhãn                              Xác suất
  ──  ────────────────────────────────  ────────
   1  window screen                       45.27 %
   2  tiger cat                           30.99 %
   3  tabby                                9.94 %
   4  Egyptian cat                         5.28 %
   5  lynx                                 3.62 %

══ [7/7] Gửi phản hồi (POST /api/feedback) ═══════════════════════════
  Gửi: download 41,444.0 ms, load 77.4 ms, latency 16.23 ms, RAM 118.9 MB, top-1 window screen (0.4527)
  Quyết định     : KEEP
  Áp dụng cho lớp: windows_x64_gt8gb
  Lý do          : Thời gian suy luận 16.23 ms không vượt ngưỡng 500 ms; giữ resnet50_int8 cho lớp windows_x64_gt8gb.

HOÀN TẤT: resnet50_int8 (request r-0001), top-1 'window screen' 45.3 %, feedback keep

EXIT=0 wall=42.8s
```

### 5.2. P2P: client A làm nguồn cho client B (server giới hạn 2000 kbps)

Ba tiến trình client: A (`--peer-port 9000 --serve`), B (`--peer-port 9100`), C (`--peer-port 0`),
cùng `--bandwidth 2000`. Cổng 9001 trên máy demo bị một dịch vụ Windows (PID 4) chiếm nên B dùng 9100.

| Client | Variant | Miếng từ server | Miếng từ peer | Lần lỗi | Thời gian tải (`download_ms`) |
|---|---|---|---|---|---|
| A (tải trước, rồi `--serve`) | `mobilenetv2_fp32` | 14 | 0 | 0 | 55,957.1 ms |
| B (A đang chạy) | `mobilenetv2_fp32` | 7 | 7 | 0 | 26,589.6 ms |
| C (đã tắt A; B đã thoát) | `mobilenetv2_fp32` | 14 | 0 | 7 | 57,963.6 ms |

Thời gian theo đồng hồ của script điều phối:

```
A xong, peer mở 57.4s
B exit 0 28.5s
Đã tắt A
C exit 0 58.6s
```

Output của B (từ bước 4):

```
══ [4/7] Xin model (POST /api/request) ═══════════════════════════════
  Gửi: băng thông 2,000.0 kbps, hạn 60 s, preferred_variant=None, have_variants=[]
  request_id     : r-0002
  Variant        : mobilenetv2_fp32 (MobileNetV2_FP32.onnx, 13.32 MB, 14 miếng, fp32, top-1 0.6948)
  Lý do          : Băng thông 2000 kbps, hạn 60 s: chọn mobilenetv2_fp32 (top-1 0.6948, tải ước tính 55.9 s), top-1 cao nhất trong 2 ứng viên đạt điều kiện; loại resnet50_fp32 vì cần 410.3 s > hạn 60 s, resnet50_int8 vì cần 103.3 s > hạn 60 s. laptopA cùng lớp windows_x64_gt8gb đã có đủ mobilenetv2_fp32, nên lấy miếng [0-6] từ peer, còn lại từ server để tải song song.
  Tải ước tính   : 55.9 s
  Kế hoạch tải   : 7 miếng từ peer, 7 miếng từ server
    peer http://127.0.0.1:9000: miếng [0, 1, 2, 3, 4, 5, 6] (fallback về server nếu lỗi)

══ [5/7] Tải model (14 miếng, tối đa 4 song song) ════════════════════
  [  1/14] miếng   1 ← peer    1.00 MB    0.01 s  sha256 OK
  [  2/14] miếng   2 ← peer    1.00 MB    0.01 s  sha256 OK
  [  3/14] miếng   0 ← peer    1.00 MB    0.01 s  sha256 OK
  [  4/14] miếng   3 ← peer    1.00 MB    0.01 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server null, bad_chunks=0 ← continue
  [  5/14] miếng   4 ← peer    1.00 MB    0.01 s  sha256 OK
  [  6/14] miếng   5 ← peer    1.00 MB    0.01 s  sha256 OK
  [  7/14] miếng   6 ← peer    1.00 MB    0.01 s  sha256 OK
  [  8/14] miếng   7 ← server  1.00 MB   16.57 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 1,930 kbps, bad_chunks=0 ← continue
  [  9/14] miếng   8 ← server  1.00 MB   16.63 s  sha256 OK
  [ 10/14] miếng   9 ← server  1.00 MB   16.70 s  sha256 OK
  [ 11/14] miếng  10 ← server  1.00 MB   16.77 s  sha256 OK
  [ 12/14] miếng  13 ← server  0.32 MB    4.14 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 2,214 kbps, bad_chunks=0 ← continue
  [ 13/14] miếng  11 ← server  1.00 MB    9.85 s  sha256 OK
  [ 14/14] miếng  12 ← server  1.00 MB    9.86 s  sha256 OK
  SHA-256 toàn file khớp c0c3f76d93fa3fd6… → MobileNetV2_FP32.onnx
  → /api/have: 2 miếng mới, complete=True, băng thông server 2,029 kbps, bad_chunks=0 ← continue
  Tải xong: 7 miếng từ server, 7 từ peer, 0 dùng lại, 0 lần lỗi
  Peer service đang chạy ở cổng 9100, chia sẻ: mobilenetv2_fp32
```

Log phía peer A khi B tải:

```
Đang phục vụ peer ở cổng 9000 (mobilenetv2_fp32). Bấm Ctrl+C để dừng.
  [peer] gửi miếng 0 của mobilenetv2_fp32 (1,048,576 byte) cho 127.0.0.1
  [peer] gửi miếng 1 của mobilenetv2_fp32 (1,048,576 byte) cho 127.0.0.1
  [peer] gửi miếng 2 của mobilenetv2_fp32 (1,048,576 byte) cho 127.0.0.1
  [peer] gửi miếng 3 của mobilenetv2_fp32 (1,048,576 byte) cho 127.0.0.1
  [peer] gửi miếng 4 của mobilenetv2_fp32 (1,048,576 byte) cho 127.0.0.1
  [peer] gửi miếng 5 của mobilenetv2_fp32 (1,048,576 byte) cho 127.0.0.1
  [peer] gửi miếng 6 của mobilenetv2_fp32 (1,048,576 byte) cho 127.0.0.1
```

### 5.3. Fallback: tắt A rồi chạy client C

```
══ [4/7] Xin model (POST /api/request) ═══════════════════════════════
  Gửi: băng thông 2,000.0 kbps, hạn 60 s, preferred_variant=None, have_variants=[]
  request_id     : r-0003
  Variant        : mobilenetv2_fp32 (MobileNetV2_FP32.onnx, 13.32 MB, 14 miếng, fp32, top-1 0.6948)
  Lý do          : Băng thông 2000 kbps, hạn 60 s: chọn mobilenetv2_fp32 (top-1 0.6948, tải ước tính 55.9 s), top-1 cao nhất trong 2 ứng viên đạt điều kiện; loại resnet50_fp32 vì cần 410.3 s > hạn 60 s, resnet50_int8 vì cần 103.3 s > hạn 60 s. laptopA, laptopB cùng lớp windows_x64_gt8gb đã có đủ mobilenetv2_fp32, nên lấy miếng [0-6] từ peer, còn lại từ server để tải song song.
  Tải ước tính   : 55.9 s
  Kế hoạch tải   : 7 miếng từ peer, 7 miếng từ server
    peer http://127.0.0.1:9000: miếng [0, 2, 4, 6] (fallback về server nếu lỗi)
    peer http://127.0.0.1:9100: miếng [1, 3, 5] (fallback về server nếu lỗi)

══ [5/7] Tải model (14 miếng, tối đa 4 song song) ════════════════════
  [cảnh báo] miếng 3: peer http://127.0.0.1:9100/chunks/mobilenetv2_fp32/3: không kết nối được (peer tắt, firewall hoặc AP isolation)
  [  1/14] miếng   3 ← server  1.00 MB   16.58 s  sha256 OK
  [cảnh báo] miếng 0: peer http://127.0.0.1:9000/chunks/mobilenetv2_fp32/0: không kết nối được (peer tắt, firewall hoặc AP isolation)
  [  2/14] miếng   0 ← server  1.00 MB   16.64 s  sha256 OK
  [cảnh báo] miếng 1: peer http://127.0.0.1:9100/chunks/mobilenetv2_fp32/1: không kết nối được (peer tắt, firewall hoặc AP isolation)
  [  3/14] miếng   1 ← server  1.00 MB   16.70 s  sha256 OK
  [cảnh báo] miếng 2: peer http://127.0.0.1:9000/chunks/mobilenetv2_fp32/2: không kết nối được (peer tắt, firewall hoặc AP isolation)
  [  4/14] miếng   2 ← server  1.00 MB   16.78 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 1,783 kbps, bad_chunks=4 ← continue
  [  5/14] miếng   7 ← server  1.00 MB   10.94 s  sha256 OK
  [cảnh báo] miếng 4: peer http://127.0.0.1:9000/chunks/mobilenetv2_fp32/4: không kết nối được (peer tắt, firewall hoặc AP isolation)
  [  6/14] miếng   4 ← server  1.00 MB   16.57 s  sha256 OK
  [cảnh báo] miếng 5: peer http://127.0.0.1:9100/chunks/mobilenetv2_fp32/5: không kết nối được (peer tắt, firewall hoặc AP isolation)
  [  7/14] miếng   5 ← server  1.00 MB   16.67 s  sha256 OK
  [cảnh báo] miếng 6: peer http://127.0.0.1:9000/chunks/mobilenetv2_fp32/6: không kết nối được (peer tắt, firewall hoặc AP isolation)
  [  8/14] miếng   6 ← server  1.00 MB   16.77 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 1,986 kbps, bad_chunks=3 ← continue
  [  9/14] miếng   8 ← server  1.00 MB   16.77 s  sha256 OK
  [ 10/14] miếng   9 ← server  1.00 MB   16.77 s  sha256 OK
  [ 11/14] miếng  10 ← server  1.00 MB   16.79 s  sha256 OK
  [ 12/14] miếng  11 ← server  1.00 MB   16.77 s  sha256 OK
  → /api/have: 4 miếng mới, complete=False, băng thông server 2,001 kbps, bad_chunks=0 ← continue
  [ 13/14] miếng  13 ← server  0.32 MB    2.91 s  sha256 OK
  [ 14/14] miếng  12 ← server  1.00 MB   11.36 s  sha256 OK
  SHA-256 toàn file khớp c0c3f76d93fa3fd6… → MobileNetV2_FP32.onnx
  → /api/have: 2 miếng mới, complete=True, băng thông server 2,058 kbps, bad_chunks=0 ← continue
  Tải xong: 14 miếng từ server, 0 từ peer, 0 dùng lại, 7 lần lỗi
```

Log server tương ứng:

```
02:46:39 INFO    [r-0003] Chọn mobilenetv2_fp32 (MobileNetV2_FP32.onnx, 14 miếng, 13964571 bytes), selector rule-v1, ước tính 55.9 s. Plan: peer http://127.0.0.1:9000: 4 miếng [0, 2, 4, 6]; peer http://127.0.0.1:9100: 3 miếng [1, 3, 5]; server: 7 miếng [7-13]
02:46:58 WARNING [r-0003] Miếng lỗi 3 từ http://127.0.0.1:9100/chunks/mobilenetv2_fp32/3
02:46:58 WARNING [r-0003] Ngừng giao peer http://127.0.0.1:9100 cho các plan sau
02:46:58 WARNING [r-0003] Miếng lỗi 0 từ http://127.0.0.1:9000/chunks/mobilenetv2_fp32/0
02:46:58 WARNING [r-0003] Ngừng giao peer http://127.0.0.1:9000 cho các plan sau
02:46:58 WARNING [r-0003] Miếng lỗi 1 từ http://127.0.0.1:9100/chunks/mobilenetv2_fp32/1
02:46:58 WARNING [r-0003] Miếng lỗi 2 từ http://127.0.0.1:9000/chunks/mobilenetv2_fp32/2
02:47:17 WARNING [r-0003] Miếng lỗi 4 từ http://127.0.0.1:9000/chunks/mobilenetv2_fp32/4
02:47:17 WARNING [r-0003] Miếng lỗi 5 từ http://127.0.0.1:9100/chunks/mobilenetv2_fp32/5
02:47:17 WARNING [r-0003] Miếng lỗi 6 từ http://127.0.0.1:9000/chunks/mobilenetv2_fp32/6
02:47:37 INFO    [r-0003] Hoàn tất: laptopC đã có đủ mobilenetv2_fp32 và hash toàn file khớp
02:47:37 INFO    [r-0003] Feedback laptopC/mobilenetv2_fp32: tải 57963.6 ms (peer 0, server 14), nạp 41.1 ms, latency 4.09 ms x20, RAM 105.5 MB, top1 Labrador retriever (0.5349) -> keep . Ghi feedback-20261014.jsonl
```

### 5.4. Bảng số đo

| Lần chạy | Variant | Ảnh | `download_ms` | `load_ms` | `latency_ms_median` (20 lần) | `peak_ram_mb` | Top-1 (độ tự tin) |
|---|---|---|---|---|---|---|---|
| server 100,000 kbps | `resnet50_fp32` | cat.jpg | 8,694.4 | 301.1 | 34.64 | 285.4 | tiger cat (0.5885) |
| server 5,000 kbps | `resnet50_int8` | cat.jpg | 41,444.0 | 77.4 | 16.23 | 118.9 | window screen (0.4527) |
| server 2,000 kbps | `mobilenetv2_fp32` | cat.jpg | 55,967.5 | 40.9 | 2.87 | 105.7 | tiger cat (0.4744) |
| server 600 kbps | `mobilenetv2_int8` | cat.jpg | 48,791.8 | 33.6 | 2.73 | 95.9 | tiger cat (0.4073) |
| P2P A, server 2,000 kbps | `mobilenetv2_fp32` | dog.jpg | 55,957.1 | 57.3 | 5.13 | 99.9 | Labrador retriever (0.5349) |
| P2P B, server 2,000 kbps | `mobilenetv2_fp32` | dog.jpg | 26,589.6 | 39.1 | 3.93 | 99.3 | Labrador retriever (0.5349) |
| Fallback C, server 2,000 kbps | `mobilenetv2_fp32` | dog.jpg | 57,963.6 | 41.1 | 4.09 | 105.5 | Labrador retriever (0.5349) |

Ghi chú: `top1_confidence` là độ tự tin trên một ảnh, không phải độ chính xác của model. Với
`samples\cat.jpg`, `resnet50_int8` xếp `window screen` (45 %) trên `tiger cat` (31 %); với
`samples\dog.jpg` cả 4 model đều cho `Labrador retriever` hoặc giống chó gần đó. Nên dùng
`dog.jpg` khi demo.

## 6. Quyết định đã tự chọn

1. **Đọc body JSON thủ công rồi validate bằng Pydantic** thay vì để FastAPI tự validate tham số
   body, để kiểm `protocol_version` (409) trước khi validate cả gói (400). Handler
   `RequestValidationError` vẫn được ghi đè (dùng cho query/path, ví dụ `size_kb=abc`,
   `index=abc`) và trả 400.
2. **Pydantic `strict=True` cho tin nhắn client** để không tự ép kiểu (`"6144"` → lỗi 400), khớp
   schema. Số nguyên vẫn được nhận cho trường `number`.
3. **Route/phương thức không tồn tại**: giữ HTTP 404/405, `code = "BAD_REQUEST"` vì enum `code`
   của schema không có mã "not found" chung.
4. **`/api/have`**: `device_id` không khớp chủ của `request_id` → 400 `BAD_REQUEST`;
   `variant_id` không khớp → 404 `UNKNOWN_VARIANT`; index trong `chunks`/`bad_chunks` ngoài phạm vi
   → 404 `CHUNK_OUT_OF_RANGE`. `complete: true` chỉ được ghi nhận khi server đã nhận đủ mọi index
   (không tin khai báo mù quáng); nếu thiếu thì log cảnh báo, vẫn trả `continue`.
5. **Ghi nhớ theo `device_class` chỉ khi feedback trả `switch`**, không ghi nhớ khi `keep`. Nếu
   ghi nhớ cả `keep`, sau một lần chạy ở 600 kbps (chọn `mobilenetv2_int8`) mọi lần sau của cùng
   lớp sẽ dính `mobilenetv2_int8` kể cả ở băng thông cao, demo không còn thấy hệ thống thích nghi.
6. **Feedback `switch`** chọn trong các variant `ready`, cùng task, `size_bytes` nhỏ hơn bản hiện
   tại; không kiểm hạn tải ở bước này (bước `/api/request` sau sẽ kiểm bằng bộ lọc cứng).
7. **`preferred_variant`** bỏ qua cả Selector lẫn bộ lọc cứng (đúng "dùng đúng bản đó"); nếu ước
   tính vượt hạn thì `reason` ghi rõ "vượt hạn". `selector_version` ghi là `preferred`.
8. **`estimated_download_s`** tính trên cả file theo công thức yêu cầu, kể cả khi một phần lấy từ
   peer (ước tính bảo thủ). Khi `plan` rỗng vì đã có file thì trả `0.0`.
9. **Peer trong plan**: nửa đầu (làm tròn lên, 4 miếng → 2 như file mẫu 04) giao cho peer; nhiều
   peer thì chia vòng tròn. Peer hợp lệ: khác thiết bị, cùng lớp, có `peer_port`, đã `complete`
   variant, `PBL4_PEER_ENABLED` bật, origin chưa bị báo lỗi. Một origin bị báo trong `bad_chunks`
   (đúng URL của miếng peer trong plan) bị ngừng giao cho mọi plan sau; thiết bị đó đăng ký lại
   thì được giao lại.
10. **`bad_chunks` gồm cả lỗi mạng/timeout/404**, không chỉ sai hash (theo yêu cầu của task;
    protocol chỉ nói "sai mã băm").
11. **`chunks_from_peer`/`chunks_from_server`** đếm số miếng đã kiểm hash đúng theo nguồn thực tế
    cuối cùng (miếng peer lỗi rồi tải lại từ server tính vào server). Miếng dùng lại từ đĩa không
    tính vào nguồn nào nhưng vẫn được báo trong `/api/have`.
12. **`bandwidth_kbps` trong `/api/have`** = số byte nhận từ server (đếm theo từng khối khi đang tải,
    gồm miếng đang dở) chia thời gian kể từ lần báo trước; `null` nếu khoảng đó không nhận byte nào
    từ server. Cách đếm theo miếng hoàn tất cho số dao động mạnh (506 → 6505 kbps) vì 4 miếng tải
    song song.
13. **Timeout 10 s mỗi miếng** hiểu là 10 s không nhận được byte (read timeout của `requests`),
    không phải tổng thời gian, vì 1 MiB ở 600 kbps cần khoảng 14 s.
14. **Token bucket kiểu "đặt chỗ"** (cho phép token âm) với `threading.Lock`, burst 20 ms ở tốc độ
    giới hạn, bắt đầu với 0 token. Burst nhỏ bù việc `sleep` trên Windows ngủ quá giờ (~15,6 ms)
    mà không làm sai số đo probe (giới hạn 5000 kbps: hai lần chạy đo được 4985 và 4894 kbps). Khối stream 16 KiB để khi
    giới hạn thấp và 4 kết nối song song, mỗi kết nối vẫn nhận byte trong vòng 10 s.
15. **Client đổi `localhost` thành `127.0.0.1`**: trên Windows `localhost` thử IPv6 trước, uvicorn
    `--host 0.0.0.0` chỉ nghe IPv4 nên mỗi kết nối mới chậm khoảng 2 s.
16. **Client bỏ qua proxy hệ thống** (`requests.Session.trust_env = False`) vì mọi kết nối là
    LAN/hotspot.
17. **Kho cục bộ của client**: `data/client/<device_id>/<variant_id>/<file>` kèm `variant.json`
    (khối variant) chỉ ghi sau khi hash toàn file khớp; `have_variants` và peer service đều dựa vào
    kho này, khóa theo `variant_id`. `--cache-dir` mặc định tính từ gốc repo.
18. **Kiểm cổng peer trước khi đăng ký**: cổng bận thì đăng ký `peer_port = null` thay vì quảng
    bá một peer không trả lời.
19. **Thêm `server/service.py` và `client/api.py`** ngoài cấu trúc gợi ý, để logic nghiệp vụ và
    dựng payload không nằm trong handler hay CLI (`rules.md` mục 2).
20. **JSONL feedback** có thêm `selector_reason`, `decision` và `data_kind: "demo"` (theo
    `yeu_cau_trien_khai.md` 4.7: ghi lý do Selector và đánh dấu dữ liệu demo). Tên file log và
    feedback theo ngày giờ địa phương; trường `timestamp` là UTC.
21. **Registry tự nạp lại** khi thời điểm sửa file đổi (chạy lại `build_registry.py` không cần
    khởi động lại server). Bản ghi `ready` nhưng thiếu `top1_accuracy`/`normalize`, chunk không
    nhất quán, hoặc `storage_path` thoát khỏi Model Store bị bỏ qua kèm log cảnh báo.
22. **Ảnh demo không commit** (giấy phép thuộc tác giả trên Wikimedia Commons), được ignore và tải
    bằng `tools/download_assets.py`; file nhãn được commit. Script dùng `requests` vì `urllib` trên
    máy này lỗi `CERTIFICATE_VERIFY_FAILED` với Wikimedia, và dùng URL ảnh gốc vì URL thumbnail
    trả 400/429.
23. **Nền tảng client**: Windows → `windows`, còn lại → `linux` (schema chưa có macOS); có
    `--platform` để ghi đè.
24. **`--serve` dừng bằng Ctrl+C** trả mã 0; không có `--serve` thì tiến trình kết thúc sau
    feedback và peer service tắt theo.
25. **Kiểm tra sức khỏe** dùng log khởi động (`Server sẵn sàng: ...`), không thêm endpoint ngoài
    protocol.

## 7. Mâu thuẫn trong tài liệu

1. `docs/examples/04_request_response.json` ghi `variant.file = "mobilenetv2-12-int8.onnx"`,
   còn Registry (`build_registry.py`, `yeu_cau_trien_khai.md` 4.1) dùng `MobileNetV2_INT8.onnx`.
   Code dùng giá trị trong Registry.
2. `docs/examples/README.md` nói hash từng miếng lấy từ `models/manifest.json`; `protocol.md`
   (lịch sử 14/10) đã thay bằng Registry `registry/model_registry.json`. Code dùng Registry.
3. `protocol.md` mục đầu ghi `tools/protocol/validate_examples.py` "chưa có, bổ sung sau": nay đã có.
4. File mẫu 04: `estimated_download_s = 6.2` với 2000 kbps, trong khi công thức
   `size_bytes × 8 / 1000 / kbps` cho 14.6 s (có thể mẫu đã trừ phần lấy từ peer). Code theo công
   thức của task (cả file).
5. File mẫu 08: lý do ghi "ngưỡng 50 ms của lớp", còn task quy định một ngưỡng chung
   `PBL4_LATENCY_SLO_MS` mặc định 500 ms. Code dùng ngưỡng chung.
6. `tong_quan_he_thong.md` 7.3 nói Selector trả `{"model_file", "reason"}`; task yêu cầu
   `SelectionResult(variant_id, reason, rejected, selector_version)`. Code theo task; tên file lấy
   từ Registry theo `variant_id`. Ví dụ ở 7.2/7.4 dùng `mobilenetv2_int8_v1`, khác `variant_id`
   thật `mobilenetv2_int8`.
7. Protocol mô tả `bad_chunks` là "miếng nhận về bị sai mã băm"; task yêu cầu ghi cả miếng lỗi
   mạng. Code ghi cả hai (xem quyết định 10).
8. Model Card có `lifecycle.status = "draft"` nhưng `build_registry.py` vẫn đặt `ready` khi kiểm
   định qua; `tong_quan_he_thong.md` mục 4 nói file mới bắt đầu ở `draft`/`validating`. Không ảnh
   hưởng code server (server chỉ đọc `status` trong Registry).
9. Protocol nói peer mặc định cổng 9000; hai client trên cùng một máy cần hai cổng khác nhau, nên
   demo dùng `--peer-port`.
10. `tools/build_registry.py` in tiếng Việt mà không đặt UTF-8 cho stdout, nên lỗi
    `UnicodeEncodeError` khi output bị chuyển hướng/pipe trên Windows (code page cp1252). Đã ghi
    cách xử lý (`set PYTHONIOENCODING=utf-8`) trong `docs/run_demo.md`; không sửa script.

## 8. Giới hạn còn lại / việc chưa làm

- **Chưa thử P2P giữa hai máy thật** qua hotspot/Wi-Fi. Đã kiểm bằng nhiều tiến trình trên một
  laptop (IP `127.0.0.1`). Trên mạng thật cần mở firewall cổng 8000 và cổng peer; AP isolation sẽ
  buộc fallback.
- **Trạng thái server nằm trong RAM**: khởi động lại server mất danh sách thiết bị, ghi nhận
  `complete`, peer bị cấm và variant ghi nhớ theo lớp. Client vẫn dùng lại file trong cache (các
  miếng được dùng lại, không tải lại).
- **`/api/have` luôn trả `continue`**; chưa có đổi variant giữa chừng khi băng thông tụt (protocol
  1.0 cho phép). Client đã xử lý được `switch` nếu server trả.
- **Bộ lọc cứng chưa xét runtime/platform/RAM** (Registry hiện chỉ có `supported_runtimes =
  ["onnxruntime"]`, `minimum_ram_mb = null`). Selector chưa dùng benchmark latency theo lớp thiết bị.
- **ML Selector** chỉ có khung `MLSelectorPlaceholder` và cơ chế `FallbackSelector`, chưa bật.
- **Ước tính thời gian tải** không trừ phần lấy từ peer.
- Về lý thuyết, burst 20 ms của bucket có thể làm probe đo cao hơn giới hạn một chút sau khi
  server rảnh lâu; lần chạy thật ở mục 5.1 không thấy điều này (100 000 kbps đo được 97 054 kbps).
- Số đo latency/RAM ở mục 5 là một lần chạy trên một laptop, không phải benchmark có kiểm soát;
  `peak_ram_mb` là RAM đỉnh của cả tiến trình client (gồm Python, numpy, ONNX Runtime).
- Chưa có client trình duyệt/điện thoại (ngoài phạm vi bắt buộc của Version 1).

## 9. Checklist nghiệm thu Version 1 (`yeu_cau_trien_khai.md` mục 6)

| Tiêu chí | Kết quả |
|---|---|
| Server khởi động theo hướng dẫn, có log khởi động xác nhận sẵn sàng | Đạt (`Server sẵn sàng: protocol 1.0, 4 variant ready ...`) |
| Registry nạp 4 variant từ metadata; không hard-code ID trong Selector | Đạt (không có `variant_id` nào trong `server/`, `client/`; selector đọc Registry) |
| Thêm artifact thử nghiệm cùng task/contract → thành ứng viên, không sửa điều kiện theo tên model | Đạt bằng test với Registry giả (`test_new_artifact_becomes_candidate_without_code_change`); chưa thử với file ONNX thứ 5 thật qua `build_registry.py` |
| Model ONNX nạp bằng ONNX Runtime và trả Top-5 cho ảnh mẫu | Đạt (cả 4 model) |
| Client đăng ký, nhận đúng `device_class`, `chunk_size`, `peer_enabled` | Đạt |
| `/api/request` trả variant hợp lệ, `request_id`, metadata, đủ plan | Đạt |
| Mọi chunk kiểm SHA-256; hash toàn file đúng sau khi ghép | Đạt |
| Peer phục vụ chunk đã xác minh trong LAN khả dụng | Đạt trên một máy (nhiều tiến trình); **chưa kiểm giữa hai máy qua hotspot** |
| Peer dừng → fallback về server thành công | Đạt (mục 5.3 và `test_p2p.py`) |
| `/api/have` và `/api/feedback` được nhận, xác thực, ghi log | Đạt |
| JSON request/response khớp schema; lỗi đúng envelope | Đạt (test kiểm mọi response và payload bằng `jsonschema`) |
| Demo đầu-cuối lặp lại được theo `docs/run_demo.md` | Đạt trên máy phát triển; nhóm cần chạy thử runbook một lần trên máy demo |
| Báo cáo đo tải, load, latency, RAM có điều kiện đo | Đạt (mục 5) |

## 10. Commit trong phiên

Các commit từ đầu phiên (`git log --oneline 7a10174..HEAD`, trước commit báo cáo này):

```
04c086a client: bao loi gon khi anh/ONNX loi thay vi in traceback
51ce295 docs: huong dan chay demo tren Windows va cap nhat README
456c3af client: kiem cong peer truoc khi dang ky, do bang thong theo byte that, loi peer ngan gon
8910dda server: khoi stream 16 KiB, request_id cho luot bi tu choi, log peer loi mot lan
0080377 style: ruff format va cau hinh ruff trong pyproject
a2a9ae1 tests: P2P hai client, planner chon peer, fallback khi tat peer
b3d3a10 client: peer service /chunks, --serve, kho cuc bo theo variant_id
1fe02b5 tests: payload client, downloader, inference va end-to-end
e4634e2 client: agent CLI register-probe-request-tai-inference-feedback
7dd2c88 client: tien xu ly anh, ONNX Runtime Top-5, do load/latency/RAM
3865d8c client: tai song song 4 chunk, kiem SHA-256, fallback, tai tiep, bao /api/have
a7214ff client: thong tin thiet bi va payload/HTTP theo protocol
d94527d tests: test endpoint, selector, registry, device_class, token bucket
3e5607a server: endpoint register/probe/request/chunks/have/feedback va log theo request_id
d3ec35f server: registry dong, device_class, selector rule-v1, planner, token bucket, feedback
d6117d4 server: cau hinh, error envelope va model Pydantic theo protocol.schema.json
32bdcb7 tools: validate_examples.py va test 9 file mau theo schema
34b69e7 tools: script tai nhan ImageNet va anh demo vao samples/
7c79126 chore: them pyproject.toml, ignore data/, file .part va anh demo
```
