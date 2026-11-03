# Tổng hợp lệnh test và demo

Có 3 máy: **S** là server, **A** và **B** là hai laptop client cùng cấu hình.

- Thay `S_IP` bằng IP của máy S, `A_IP` bằng IP của laptop A (xem bằng `ipconfig`). Chạy tất cả
  trên một máy thì dùng `127.0.0.1`.
- Lệnh trong khối `cmd` chạy ở cmd; lệnh trong khối `powershell` chạy ở PowerShell.
- Mọi lệnh chạy tại thư mục gốc repo. Chữ tiếng Việt bị lỗi thì chạy `chcp 65001` trước.
- Cả 3 máy phải dùng chung một Wi-Fi hoặc hotspot.

---

## 0. Chuẩn bị (một lần)

**Cả 3 máy:** lấy code mới và cài thư viện:

```cmd
git checkout dev
git pull origin dev
pip install -e .[dev]
chcp 65001
```

**Máy S:** tải model và tạo danh mục model. Lệnh `build_registry` phải in `4 variant, 4 ready`:

```cmd
python tools\download_models.py
python tools\build_registry.py
python tools\download_assets.py
```

**Máy S:** mở cổng 8000 (PowerShell chạy bằng quyền Administrator):

```powershell
New-NetFirewallRule -DisplayName "PBL4 server 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow -Profile Any
```

**Laptop A và B:** mở cổng chia sẻ cho P2P (PowerShell chạy bằng quyền Administrator):

```powershell
New-NetFirewallRule -DisplayName "PBL4 peer 9000" -Direction Inbound -Protocol TCP -LocalPort 9000 -Action Allow -Profile Any
```

**Laptop A và B:** kiểm tra kết nối tới server; kết quả phải là `TcpTestSucceeded : True`:

```powershell
Test-NetConnection S_IP -Port 8000
```

---

## 1. Bật server (máy S)

```cmd
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

- Dòng đầu tiên server in ra cho biết giới hạn tốc độ. Mặc định là `không giới hạn`.
- Tắt server: **Ctrl+C**. Server giữ trạng thái trong RAM, nên tắt rồi bật lại là trạng thái
  sạch.

---

## 2. Client xin model và nhận diện (laptop A)

| Việc | Lệnh | Thấy gì |
|---|---|---|
| Lần đầu: xin và tải model | `python -m client.agent --server http://S_IP:8000 --device-id laptopA --peer-port 0 --bandwidth 600 --image samples\dog.jpg` | Chạy đủ 7 bước, có `Kho cục bộ: mobilenetv2_int8 complete` |
| Lần sau: dùng model trên máy | `python -m client.agent --server http://S_IP:8000 --device-id laptopA --peer-port 0 --image samples\cat.jpg` | `Model có sẵn trên máy (không gửi /api/request)` |
| Buộc hỏi server | Thêm `--refresh` vào lệnh trên | Plan rỗng, không tải lại |
| Không truyền ảnh | Bỏ `--image` | Mở cửa sổ chọn ảnh |

Tham số hay dùng:

| Tham số | Ý nghĩa |
|---|---|
| `--bandwidth 2000` | Giả lập băng thông (kbps) thay vì đo bằng `/api/probe` |
| `--preferred-variant mobilenetv2_int8` | Xin đúng một model, bỏ qua bộ chọn |
| `--peer-port 9000 --serve` | Chia sẻ model cho máy khác (P2P) và chạy liên tục đến khi bấm Ctrl+C |
| `--follow-switch` | Server bảo đổi sang model nhẹ hơn thì tự xin lại |
| `--refresh` | Hỏi server dù máy đã có model hợp lệ |

Model tải về nằm ở `data\client\<device_id>\<variant_id>\`. File `variant.json` ghi trạng thái
`partial` (đang tải), `complete` (dùng được) hoặc `invalid` (hỏng).

### Gọi riêng từng API (PowerShell, dùng khi cần trình bày từng bước)

Chạy hai dòng này trước, một lần:

```powershell
$S = "http://S_IP:8000"
function Send($path, $obj) { $json = $obj | ConvertTo-Json -Depth 5; Invoke-RestMethod -Method Post -Uri "$S$path" -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($json)) }
```

**1. Kết nối (đăng ký thiết bị):**

```powershell
Send "/api/register" @{protocol_version="1.0"; device_id="laptopA"; platform="windows"; cpu_arch="AMD64"; ram_mb=8192; runtime="onnxruntime 1.31.0"; peer_port=$null}
```

**2. Xin model:**

```powershell
$r = Send "/api/request" @{device_id="laptopA"; task="image_classification"; bandwidth_kbps=600; deadline_s=60; free_storage_mb=$null; preferred_variant=$null; have_variants=@()}
$r.request_id; $r.variant.id; $r.reason
```

**3. Gửi feedback:**

```powershell
Send "/api/feedback" @{feedback_id="fb-demo-001"; device_id="laptopA"; variant_id="mobilenetv2_int8"; rating=5; correct=$true; prediction="tiger cat"; confidence=0.61; expected_label="tiger cat"; comment="Ket qua dung"; request_id=$r.request_id; detected_at=$null; created_at=(Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")}
```

- Lệnh 2 và 3 cần chạy lệnh 1 trước.
- Gửi lại đúng lệnh 3 lần nữa thì server trả `status: duplicate`, không ghi trùng.
- Lệnh 2 chỉ xin model, không tải file. Muốn tải và nhận diện thì dùng `client.agent`.
- Trên PowerShell 5.1, chữ có dấu trong kết quả trả về có thể hiện lỗi font. Đó chỉ là lỗi hiển
  thị; dữ liệu server lưu vẫn đúng.

---

## 3. Gửi feedback (laptop A)

Feedback mặc định đánh giá **lần detect gần nhất** của thiết bị. Nên gửi ngay sau mỗi lần detect.

| Việc | Lệnh / thao tác | Thấy gì |
|---|---|---|
| Đánh giá khi server bật | `python -m client.feedback_cli --device-id laptopA --server http://S_IP:8000`, trả lời `5`, `y`, Enter | `✓ fb-...: server xác nhận`, `Đánh giá: positive` |
| Trả lời sẵn bằng tham số | `python -m client.feedback_cli --device-id laptopA --server http://S_IP:8000 --rating 2 --correct no --expected-label beagle --comment "Nham"` | `Đánh giá: negative` |
| Thoát giữa chừng | Chạy lệnh đánh giá, nhấn **Enter bỏ trống** ở câu điểm hoặc câu y/n | `Đã thoát đánh giá; ... không được lưu` |
| Đánh giá khi **server tắt** | Ctrl+C trên S, rồi chạy lệnh đánh giá | `giữ pending, không cần nhập lại` |
| Xem hàng chờ | `type data\client\laptopA\feedback_queue.jsonl` | Dòng mới có `"status": "pending"` |
| Bật lại S rồi đánh giá tiếp | Chạy lệnh đánh giá | CLI hỏi `Gửi 1 đánh giá lần trước? (y/n)`: `y` chỉ gửi đánh giá cũ; `n` giữ đánh giá cũ và nhập đánh giá mới |
| Gửi bù không cần hỏi | `python -m client.feedback_cli --sync-pending --device-id laptopA --server http://S_IP:8000` | `XONG: đã gửi hết.` |

Lưu ý: `client.agent` tự gửi bù đánh giá `pending`, không hỏi, mỗi khi kết nối được server.

---

## 4. Khi server tắt (Ctrl+C trên S)

| Việc | Lệnh | Thấy gì |
|---|---|---|
| Nhận diện bằng agent | `python -m client.agent --server http://S_IP:8000 --device-id laptopA --peer-port 0 --image samples\dog.jpg` | `Chuyển sang chế độ offline`, `HOÀN TẤT (offline)` |
| Nhận diện không cần server | `python -m client.detect --device-id laptopA --image samples\dog.jpg samples\cat.jpg` | Kết quả 2 ảnh, `chạy offline` |
| Máy chưa có model | `python -m client.agent --server http://S_IP:8000 --device-id laptopZ --peer-port 0 --image samples\dog.jpg` | `chưa thể detect offline vì máy chưa có model phù hợp` |
| Làm hỏng model | `echo x>> data\client\laptopA\mobilenetv2_int8\MobileNetV2_INT8.onnx` rồi chạy lại lệnh agent | Báo `invalid`. Bật S rồi chạy lại thì client tải lại model |

---

## 5. P2P giữa A và B (hai laptop cùng cấu hình)

Điều kiện để có P2P:

1. A và B cùng `device_class`: cùng hệ điều hành, cùng loại CPU, RAM cùng nhóm (dưới 4 GB,
   4–8 GB, hoặc trên 8 GB).
2. B chưa có model trên máy.
3. Cả hai xin cùng một model: dùng chung `--bandwidth`.
4. B kết nối được tới A ở cổng 9000.

**Laptop A và B:** xóa model cũ, bắt buộc để B phải tải:

```cmd
rmdir /s /q data\client\laptopA
rmdir /s /q data\client\laptopB
```

**Máy S:** tắt server cũ, bật lại với giới hạn 2 Mbps (dòng đầu tiên phải có
`giới hạn tốc độ 2000 kbps`):

```cmd
set PBL4_RATE_LIMIT_KBPS=2000
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

**Laptop A:** tải từ server rồi giữ chia sẻ. Ghi lại dòng `device_class`, đợi đến dòng
`Đang phục vụ peer ở cổng 9000` và **giữ cửa sổ này mở**:

```cmd
python -m client.agent --server http://S_IP:8000 --device-id laptopA --image samples\dog.jpg --bandwidth 2000 --peer-port 9000 --serve
```

**Laptop B:** kiểm tra tới được A (phải là `True`), rồi tải:

```powershell
Test-NetConnection A_IP -Port 9000
```

```cmd
python -m client.agent --server http://S_IP:8000 --device-id laptopB --image samples\dog.jpg --bandwidth 2000 --peer-port 9100
```

Kết quả mong đợi:

| Ở đâu | Thấy gì |
|---|---|
| B, bước 2 | `device_class` **giống hệt** A |
| B, bước 4 | `Kế hoạch tải: 7 miếng từ peer, 7 miếng từ server` và dòng `peer http://A_IP:9000: miếng [0, 1, ..., 6]` |
| B, bước 5 | Các dòng `miếng i ← peer` xen với `miếng i ← server`, tất cả đều `sha256 OK` |
| B, bước 7 | `chunks_from_peer` là 7 |
| Laptop A | `[peer] gửi miếng ... cho <IP của B>` |
| Server | `... đã có đủ mobilenetv2_fp32, nên lấy miếng [0-6] từ peer` |

**Demo khi peer bị tắt:** Ctrl+C trên A, rồi trên B chạy:

```cmd
python -m client.agent --server http://S_IP:8000 --device-id laptopC --image samples\dog.jpg --bandwidth 2000 --peer-port 0
```

B báo `peer ...: không kết nối được`, tải lại các miếng đó từ server, và vẫn xong với `sha256 OK`.
Server in `Ngừng giao peer http://A_IP:9000`.

Nếu không thấy P2P:

| Dấu hiệu | Cách xử lý |
|---|---|
| B in `Kế hoạch tải: 14 miếng từ server` | `device_class` của A và B khác nhau. So dòng `device_class` ở bước 2; RAM khác nhóm thì không P2P được giữa hai máy này |
| B in `Model có sẵn trên máy` | B còn model cũ. Chạy `rmdir /s /q data\client\laptopB` rồi chạy lại |
| B in `peer ...: không kết nối được` | Tường lửa trên A chặn cổng 9000, hoặc hotspot chặn các máy nói chuyện với nhau |
| A và B được chọn model khác nhau | Dùng cùng `--bandwidth 2000`, hoặc thêm `--preferred-variant mobilenetv2_fp32` cho cả hai |
| Vừa bật lại server thì B không thấy peer | Server quên A khi khởi động lại. Chạy lại lệnh của A (không cần xóa model) |

---

## 5b. Giao diện web (thay cho đọc log/terminal khi trình chiếu)

| Việc | Lệnh / địa chỉ | Thấy gì |
|---|---|---|
| Dashboard server | Mở `http://S_IP:8000/` trên máy chiếu | Tổng quan, Thiết bị & P2P, Registry, Quyết định, Truyền model, Đánh giá, Log; tự cập nhật mỗi 2 giây |
| Giao diện client (laptop A, có chia sẻ P2P) | `python -m client.web_ui --server http://S_IP:8000 --device-id laptopA --peer-port 9000 --open` | Trang `http://127.0.0.1:8080`: chọn ảnh, bấm Nhận diện, xem tiến trình + Top-5, gửi đánh giá |
| Giao diện client (laptop B) | `python -m client.web_ui --server http://S_IP:8000 --device-id laptopB --open` | Như trên; dashboard màn **Truyền model** thấy lưới miếng sọc (peer) xen miếng đặc (server) |
| Xin model thử không cần thiết bị chạy | Dashboard → **Quyết định chọn model** → form **Xin model thử** | Lý do chọn và plan, lượt hiện nhãn “xin thử từ web” |
| Gửi đánh giá từ dashboard | Dashboard → **Đánh giá model** → tab **Đánh giá người dùng** | Verdict positive/neutral/negative và thống kê variant |

Muốn thấy lưới miếng chạy dần khi demo: bật server với `set PBL4_RATE_LIMIT_KBPS=2000` như mục 5.
Giao diện client chỉ mở cho chính máy đó; thêm `--host 0.0.0.0` (và mở tường lửa cổng 8080) nếu
muốn xem từ máy khác.

---

## 6. Xem kết quả và chạy kiểm thử

| Nội dung | Lệnh |
|---|---|
| Log server | `type data\logs\server-YYYYMMDD.log` (trên S) |
| Đánh giá người dùng | `type data\feedback\manual-feedback-YYYYMMDD.jsonl` (trên S) |
| Số đo agent gửi | `type data\feedback\feedback-YYYYMMDD.jsonl` (trên S) |
| Model trên client | `type data\client\laptopA\mobilenetv2_int8\variant.json` |
| Chạy toàn bộ test | `python -m pytest -q` |

---

## 7. Dọn dẹp sau demo

**Máy S:** bỏ giới hạn tốc độ (hoặc mở cửa sổ cmd mới):

```cmd
set PBL4_RATE_LIMIT_KBPS=
```

Gỡ tường lửa (PowerShell chạy bằng quyền Administrator). Lệnh đầu chạy trên S, lệnh sau chạy trên
A và B:

```powershell
Remove-NetFirewallRule -DisplayName "PBL4 server 8000"
Remove-NetFirewallRule -DisplayName "PBL4 peer 9000"
```
