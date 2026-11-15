# Tổng hợp lệnh test và demo (bài toán phát hiện vật thể)

Kịch bản kiểm thử dùng các máy sau, tất cả chung một Wi-Fi hoặc hotspot:

| Máy | Vai trò | Cần cài |
|---|---|---|
| **S** | Server + dashboard (laptop Windows) | Repo, Python |
| **A**, **B** | Hai điện thoại (khác cấu hình cũng được), dùng trình duyệt | Không cần cài gì |
| **L1**, **L2** | (Tuỳ chọn) hai laptop chạy client Python | Repo, Python |

- Thay `S_IP` bằng IP của máy S (xem dòng `Link cho client` khi bật server). IP đổi khi đổi mạng.
- Lệnh trong khối `cmd` chạy ở cmd; lệnh trong khối `powershell` chạy ở PowerShell.
- Mọi lệnh chạy tại thư mục gốc repo (`pbl4-edge-model-distribution`). Chữ tiếng Việt bị lỗi thì
  chạy `chcp 65001` trước.

---

## 0. Chuẩn bị (một lần)

**Máy S (và L1, L2 nếu dùng client Python):** lấy code mới và cài thư viện:

```cmd
git checkout dev
git pull origin dev
pip install -e .[dev]
chcp 65001
```

**Máy S:** tải 10 model, thư viện cho trình duyệt (onnxruntime-web) và ảnh mẫu, rồi sinh Model
Card + Registry. Lệnh cuối phải in `10 variant, 10 ready`:

```cmd
python tools\download_models.py
python tools\download_assets.py
python tools\curate_detection_registry.py
```

**Máy S:** công cụ tạo chứng chỉ HTTPS (camera trực tiếp trên điện thoại cần HTTPS). Bỏ qua nếu
máy đã có Git for Windows (có sẵn openssl):

```cmd
pip install cryptography
```

**Máy S:** thư viện chấm bình luận bằng PhoBERT (thiếu thì server chấm theo từ khoá):

```cmd
pip install -e .[phobert]
```

**Máy S:** mở cổng 8000 (HTTP) và 8443 (HTTPS) (PowerShell chạy bằng quyền Administrator):

```powershell
New-NetFirewallRule -DisplayName "PBL4 server" -Direction Inbound -Protocol TCP -LocalPort 8000,8443 -Action Allow -Profile Any
```

**L1 và L2 (chỉ khi demo P2P của client Python):** mở cổng chia sẻ 9000, rồi kiểm tra tới được
server (phải là `TcpTestSucceeded : True`):

```powershell
New-NetFirewallRule -DisplayName "PBL4 peer 9000" -Direction Inbound -Protocol TCP -LocalPort 9000 -Action Allow -Profile Any
Test-NetConnection S_IP -Port 8000
```

---

## 1. Bật server (máy S)

```cmd
python -m server.serve
```

Thấy gì:

```text
... Server sẵn sàng: protocol 1.0, 10 variant ready (...), selector rule-v1, giới hạn tốc độ không giới hạn, ...
========================================================================
 Link cho client (điện thoại, laptop cùng mạng): https://S_IP:8443/app/
 Link dashboard của server:                      http://S_IP:8000/
========================================================================
```

- Mở dashboard `http://S_IP:8000/` trên máy chiếu. Trang **Tổng quan** có mã QR của link client.
- Tắt server: **Ctrl+C**. Thiết bị, lượt tải và tốc độ đã học nằm trong RAM, nên tắt rồi bật lại
  là trạng thái sạch (feedback và log vẫn còn trong `data\`).
- Lệnh cũ `uvicorn server.main:app --host 0.0.0.0 --port 8000` vẫn chạy nhưng chỉ có HTTP: điện
  thoại chỉ chụp được từng ảnh, không có camera trực tiếp.
- Tuỳ chọn: `--port 8000 --https-port 8443`, `--no-https` (link client thành HTTP, chỉ chụp ảnh),
  `--ip <IP>` khi link in sai IP (máy có nhiều card mạng như VMware).

---

## 2. Kịch bản chính: điện thoại A xin model và phát hiện vật thể

| Bước | Thao tác trên A | Thấy gì |
|---|---|---|
| 1 | Quét mã QR trên dashboard, hoặc gõ `https://S_IP:8443/app/` | Lần đầu: "Kết nối không riêng tư" (chứng chỉ tự ký) → **Nâng cao → Tiếp tục** |
| 2 | Nhập tên máy (ví dụ `Máy của Minh`), bấm **Vào** | Vào giao diện chính, tên hiện trên đầu trang. Đổi tên ở thẻ **Máy này** |
| 3 | Tab **Model**, chọn **Camera trực tiếp**, bấm **Xin model** | 6 bước đều ✓: đăng ký (lớp `android_browser_arm64_...`), đo băng thông, xin model (`yolox_nano`), tải 4 miếng có SHA-256, lưu vào máy, nạp model (4 luồng) |
| 4 | Đọc "Lý do server chọn", mở "Vì sao không chọn model khác" | Ngân sách 200 ms/khung; `yolox_x/l`, RetinaNet bị loại vì "không chạy được trên onnxruntime-web" |
| 5 | Trang tự sang tab **Camera**, bấm **Bật camera**, cho phép camera | Khung + tên vật thể tiếng Việt (người, chai, cốc, điện thoại...), số ms và khung/giây ở góc dưới |
| 6 | **Đổi camera**, kéo **Ngưỡng tin cậy**, chọn **Chỉ hiện: người** | Đổi camera trước/sau, lọc vật thể |
| 7 | **Chụp ảnh** (mở camera của máy) hoặc **Chọn ảnh** | Ảnh tĩnh kèm khung |
| 8 | Tải lại trang | Không hỏi lại tên. Tab Model: "Đang dùng yolox_nano", **không tải lại** (model lưu trong trình duyệt) |

Trên dashboard (S) lúc này:

| Màn | Thấy gì |
|---|---|
| Thiết bị & P2P | A: "Đang hoạt động", runtime `onnxruntime-web` · 4 luồng, P2P "Đang chia sẻ · WebRTC" |
| Truyền model | Lượt của A: 4/4 miếng, chế độ "Camera trực tiếp · ngân sách 200 ms", số đo FPS |
| Tổng quan → Cấu hình server | "Tốc độ đo được" của lớp thiết bị của A (học từ số đo A gửi về) |

---

## 3. Đánh giá kết quả (điện thoại A)

| Thao tác | Thấy gì |
|---|---|
| Tab **Đánh giá** (có model là dùng được ngay, không cần chụp khung hình) | Form chọn sao, nhận xét |
| Chọn 4 sao, nhận xét `rất tốt` → **Gửi đánh giá** | "Server đã nhận", "Đánh giá: Tích cực", `Điểm 4,8 / 5 · 4 sao, bình luận 5/5` |
| Chọn 5 sao, nhận xét `sai hết` → **Gửi đánh giá** | Bình luận kéo điểm xuống: `Điểm 1,8 / 5`, "Đánh giá: Tiêu cực" |
| (Tuỳ chọn) Tab Camera → **Đánh giá khung này** | Sang tab Đánh giá, gửi kèm khung hình và kết quả nhận diện |
| Dashboard → **Đánh giá model** | Đánh giá vừa gửi, thống kê theo model, FPS camera |

---

## 4. P2P giữa hai điện thoại A và B (WebRTC)

Điều kiện: A và B xin **cùng model** và A **đang mở trang** `/app`. Hai máy khác cấu hình vẫn chia
sẻ được: có máy đã có model thì lấy toàn bộ từ máy đó, server không phải gửi gì.

**Máy S:** tắt server, bật lại với giới hạn 3 Mbps để thấy rõ lợi ích P2P:

```cmd
set PBL4_RATE_LIMIT_KBPS=3000
python -m server.serve
```

| Bước | Thao tác | Thấy gì |
|---|---|---|
| 1 | Trên A: mở link, tab Model → **Xoá** model cũ (danh sách "Model đã lưu trên máy"), chọn **Ảnh chụp**, **Xin model**; **để nguyên trang mở** | A tải `yolox_tiny` (20 miếng) từ server, khoảng 55 giây |
| 2 | Trên B: mở link, chọn **Ảnh chụp**, **Xin model** | Bước "Tải từng miếng": `0 miếng từ server, 20 từ máy khác (P2P)`; lưới toàn ô **P** (sọc); thông báo "Bạn đã nhận được mô hình yolox_tiny từ máy ..."; danh sách model ghi "nhận từ máy ..." |
| 3 | Nhìn header trang A | Huy hiệu "Đã gửi yolox_tiny cho <tên máy B> · 20 miếng" |
| 4 | Dashboard → **Truyền model** → lượt của B | Lưới toàn P, peer `<tên máy A> · webrtc://... · WebRTC (trình duyệt)` |

**Demo khi peer tắt:** đóng trang trên A, rồi trên B xóa model và xin lại. Server không giao A làm
peer nữa (dashboard: A "Đã đóng trang"), B tải đủ 20 miếng từ server.

Nếu không thấy P2P:

| Dấu hiệu | Cách xử lý |
|---|---|
| B: `20 miếng từ server`, không có P | Trang A đã đóng hoặc khoá màn hình, hoặc hai máy xin khác model. Mở lại trang A rồi xin lại trên B |
| A và B được chọn model khác nhau | Tab Model → Tuỳ chọn nâng cao → **Model muốn dùng**: chọn cùng `yolox_tiny` trên cả hai |
| B báo "Peer ...: không nối được tới ...; lấy các miếng đó từ server" | Hotspot/router chặn hai máy nói chuyện trực tiếp. B vẫn tải xong từ server (đúng thiết kế dự phòng) |
| B: "đã có trên máy, không tải lại" | B còn model cũ: bấm **Xoá** trong "Model đã lưu trên máy" rồi xin lại |

Muốn bỏ giới hạn tốc độ: mở cửa sổ cmd mới, hoặc `set PBL4_RATE_LIMIT_KBPS=` rồi bật lại server.

---

## 5. Chọn model theo cấu hình và chế độ dùng

| Thao tác | Model mong đợi | Vì sao |
|---|---|---|
| Điện thoại, **Camera trực tiếp** | `yolox_nano` | Ngân sách 200 ms/khung, điện thoại ~140 ms/GFLOP |
| Điện thoại, **Ảnh chụp** | `yolox_tiny` | Ngân sách 2,5 s/ảnh |
| Laptop mở `https://S_IP:8443/app/`, Camera trực tiếp | `yolox_tiny` | Trình duyệt laptop + 4 luồng nhanh hơn điện thoại |
| Laptop, Ảnh chụp | `yolox_m` | Đủ nhanh cho model nặng hơn, mAP cao hơn |
| Dashboard → **Quyết định chọn model** → **Xin model thử**: chọn thiết bị, "Dùng để", băng thông | Tuỳ băng thông | Xem từng bước lọc: sai runtime, thiếu RAM, quá ngân sách, không tải kịp hạn |

Sau vài lượt dùng, server học tốc độ thật của từng lớp thiết bị (lý do ghi "tốc độ đo thực tế").
Máy chạy dư sức thì server đề xuất **nâng cấp**: tab Model hiện "Server đề xuất đổi sang ..." với
nút **Tải bản đề xuất**.

---

## 6. Khi server tắt (Ctrl+C trên S)

| Việc | Thấy gì |
|---|---|
| Trang `/app` đang mở trên A | Camera vẫn phát hiện vật thể bình thường (model chạy trên máy). Gửi đánh giá báo lỗi |
| Tải lại trang trên A | Không mở được cho tới khi bật lại server (trang do server phục vụ) |
| Bật lại server, mở lại link | Model nạp từ trình duyệt ngay, không tải lại |

---

## 7. Client Python (laptop không dùng trình duyệt, L1/L2)

### 7.1 Xin model và phát hiện vật thể trên ảnh

| Việc | Lệnh | Thấy gì |
|---|---|---|
| Lần đầu (chế độ ảnh chụp, mặc định) | `python -m client.agent --server http://S_IP:8000 --device-id laptopA --peer-port 0 --bandwidth 5000 --image samples\dog.jpg` | 7 bước, chọn `yolox_s`, bảng vật thể: `1  dog  92.6%  (35, 39, 646, 559)`, feedback `KEEP` |
| Lần sau: dùng model trên máy | `python -m client.agent --server http://S_IP:8000 --device-id laptopA --peer-port 0 --image samples\cat.jpg` | `Model có sẵn trên máy (không gửi /api/request)` |
| Buộc hỏi server | Thêm `--refresh` vào lệnh trên | Plan rỗng, không tải lại |
| Không truyền ảnh | Bỏ `--image` | Mở cửa sổ chọn ảnh |

Ngưỡng băng thông (thêm `--mode none` để chỉ thấy tác động của băng thông, hạn 60 s):

| `--bandwidth` | Model |
|---|---|
| 100000 | `yolox_x` (377,8 MB) |
| 30000 | `yolox_l` |
| 20000 | `yolox_m` |
| 5000 | `yolox_s` |
| 3000 | `yolox_tiny` |
| 600 | `yolox_nano` |
| 100 | lỗi 422 `NO_VARIANT_FITS`, thoát mã 2 |

Ví dụ: `python -m client.agent --server http://S_IP:8000 --device-id laptopA --peer-port 0 --mode none --bandwidth 30000 --image samples\dog.jpg`

Tham số hay dùng:

| Tham số | Ý nghĩa |
|---|---|
| `--mode photo` / `live` / `none` | photo (mặc định): model chạy 1 ảnh ≤ 2,5 s trên máy này; live: ≥ 5 khung/giây; none: không lọc tốc độ |
| `--bandwidth 2000` | Giả lập băng thông (kbps) thay vì đo bằng `/api/probe` |
| `--preferred-variant yolox_nano` | Xin đúng một model, bỏ qua bộ chọn |
| `--peer-port 9000 --serve` | Chia sẻ model cho máy khác (P2P HTTP) và chạy liên tục đến khi bấm Ctrl+C |
| `--follow-switch` | Server bảo đổi model thì tự xin lại |
| `--refresh` | Hỏi server dù máy đã có model hợp lệ |

Model tải về nằm ở `data\client\<device_id>\<variant_id>\` (file `.onnx` + `variant.json` ghi
`partial`/`complete`/`invalid`).

### 7.2 Đánh giá bằng CLI

| Việc | Lệnh | Thấy gì |
|---|---|---|
| Đánh giá lần detect gần nhất | `python -m client.feedback_cli --device-id laptopA --server http://S_IP:8000`, trả lời `5`, `y`, Enter | `✓ fb-...: server xác nhận`, `Đánh giá: positive` |
| Trả lời sẵn bằng tham số | `python -m client.feedback_cli --device-id laptopA --server http://S_IP:8000 --rating 2 --correct no --expected-label cat --comment "Nham"` | `Đánh giá: negative` |
| Đánh giá khi **server tắt** | Ctrl+C trên S, rồi chạy lệnh đánh giá | `giữ pending, không cần nhập lại` |
| Gửi bù khi server bật lại | `python -m client.feedback_cli --sync-pending --device-id laptopA --server http://S_IP:8000` | `XONG: đã gửi hết.` |

### 7.3 Khi server tắt

| Việc | Lệnh | Thấy gì |
|---|---|---|
| Nhận diện bằng agent | `python -m client.agent --server http://S_IP:8000 --device-id laptopA --peer-port 0 --image samples\dog.jpg` | `Chuyển sang chế độ offline`, `HOÀN TẤT (offline)` |
| Nhận diện không cần server | `python -m client.detect --device-id laptopA --image samples\dog.jpg samples\cat.jpg` | Bảng vật thể cho 2 ảnh, chạy offline |
| Máy chưa có model | `python -m client.agent --server http://S_IP:8000 --device-id laptopZ --peer-port 0 --image samples\dog.jpg` | `chưa thể detect offline vì máy chưa có model phù hợp` |
| Làm hỏng model | `echo x>> data\client\laptopA\yolox_s\yolox_s.onnx` rồi chạy lại lệnh agent | Báo `invalid`. Bật S rồi chạy lại thì client tải lại model |

### 7.4 P2P giữa hai laptop L1, L2 (HTTP)

**L1 và L2:** xóa model cũ. **Máy S:** bật server với `set PBL4_RATE_LIMIT_KBPS=3000` như mục 4.

```cmd
rmdir /s /q data\client\laptopA
rmdir /s /q data\client\laptopB
```

**L1:** tải rồi giữ chia sẻ, đợi dòng `Đang phục vụ peer ở cổng 9000`, **giữ cửa sổ mở**:

```cmd
python -m client.agent --server http://S_IP:8000 --device-id laptopA --image samples\dog.jpg --mode none --bandwidth 3000 --peer-port 9000 --serve
```

**L2:** kiểm tra tới được L1 (phải `True`), rồi tải:

```powershell
Test-NetConnection L1_IP -Port 9000
```

```cmd
python -m client.agent --server http://S_IP:8000 --device-id laptopB --image samples\dog.jpg --mode none --bandwidth 3000 --peer-port 9100
```

Kết quả mong đợi: L2 bước 4 `Kế hoạch tải: 20 miếng từ peer` (`yolox_tiny`); bước 5 các dòng
`← peer`, đều `sha256 OK`, rồi `Nguồn model : nhận yolox_tiny từ máy laptopA`; L1 in
`[peer] gửi miếng ...`. Ctrl+C trên L1 rồi chạy L2 với `--device-id laptopC --peer-port 0`:
L2 báo peer không kết nối được, tải lại từ server và vẫn xong.

---

## 8. Gọi riêng từng API (PowerShell, dùng khi cần trình bày từng bước)

Chạy hai dòng này trước, một lần:

```powershell
$S = "http://S_IP:8000"
function Send($path, $obj) { $json = $obj | ConvertTo-Json -Depth 5; Invoke-RestMethod -Method Post -Uri "$S$path" -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($json)) }
```

**1. Đăng ký thiết bị** (giả lập điện thoại dùng trình duyệt):

```powershell
Send "/api/register" @{protocol_version="1.0"; device_id="phoneDemo"; platform="android_browser"; cpu_arch="arm64"; ram_mb=4096; runtime="onnxruntime-web 1.20.1"; peer_port=$null; capabilities=@{threads=1; webgpu=$false; camera=$true; p2p="webrtc"}}
```

**2. Xin model** (camera trực tiếp; đổi `mode="photo"` để so sánh):

```powershell
$r = Send "/api/request" @{device_id="phoneDemo"; task="object_detection"; bandwidth_kbps=20000; deadline_s=60; free_storage_mb=$null; preferred_variant=$null; have_variants=@(); mode="live"; target_fps=5}
$r.request_id; $r.variant.id; $r.variant.detection.format; $r.reason
```

**3. Gửi đánh giá:**

```powershell
Send "/api/feedback" @{feedback_id="fb-demo-001"; device_id="phoneDemo"; variant_id=$r.variant.id; rating=5; correct=$true; prediction="dog 87%"; confidence=0.87; expected_label="dog"; comment="Ket qua dung"; request_id=$r.request_id; detected_at=$null; created_at=(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")}
```

- Lệnh 2 và 3 cần chạy lệnh 1 trước. Lệnh 2 chỉ xin model, không tải file.
- Gửi lại đúng lệnh 3 lần nữa thì server trả `status: duplicate`, không ghi trùng.
- Trên PowerShell 5.1, chữ có dấu trong kết quả có thể hiện lỗi font; dữ liệu server lưu vẫn đúng.

---

## 9. Xem kết quả và chạy kiểm thử

| Nội dung | Lệnh |
|---|---|
| Log server | `type data\logs\server-YYYYMMDD.log` (trên S) |
| Đánh giá người dùng | `type data\feedback\manual-feedback-YYYYMMDD.jsonl` (trên S) |
| Số đo client gửi (latency, FPS, miếng từ peer) | `type data\feedback\feedback-YYYYMMDD.jsonl` (trên S) |
| Model trên client Python | `type data\client\laptopA\yolox_s\variant.json` |
| Kiểm tra lại 10 model (chạy thử trên `samples\dog.jpg`) | `python tools\build_registry.py` → `10 variant, 10 ready` |
| Chạy toàn bộ test | `python -m pytest -q` (hiện 370 đạt; `test_retrain_correction_logic` hỏng do module ML Selector đang viết dở) |
| Chỉ test phần server cho trình duyệt (`/app`, WebRTC, chọn theo chế độ) | `python -m pytest -q tests\test_webclient.py tests\test_selector.py` |

---

## 10. Dọn dẹp sau demo

**Máy S:** bỏ giới hạn tốc độ (hoặc mở cửa sổ cmd mới):

```cmd
set PBL4_RATE_LIMIT_KBPS=
```

Gỡ tường lửa (PowerShell chạy bằng quyền Administrator). Lệnh đầu chạy trên S, lệnh sau trên L1, L2:

```powershell
Remove-NetFirewallRule -DisplayName "PBL4 server"
Remove-NetFirewallRule -DisplayName "PBL4 peer 9000"
```

Xóa model đã lưu trong trình duyệt điện thoại: tab Model → "Model đã lưu trên máy" → **Xoá**.
