# pbl4-edge-model-distribution

Hệ thống phân phối model AI (ONNX) cho thiết bị edge theo băng thông, PBL4 Version 1.

- **Server** (Python FastAPI): đọc danh mục model động từ Registry, chọn phiên bản model
  (MobileNetV2/ResNet-50, FP32/INT8) vừa hạn tải theo băng thông và dung lượng của thiết bị,
  lập kế hoạch tải theo miếng 1 MiB, phục vụ miếng có giới hạn tốc độ, ghép P2P giữa các máy
  cùng lớp và nhận feedback.
- **Client agent** (Python CLI): đăng ký, đo băng thông, xin model, tải tối đa 4 miếng song
  song có kiểm SHA-256 và fallback, chạy phân loại ảnh bằng ONNX Runtime (Top-5), gửi số đo
  thực tế; đồng thời làm peer chia sẻ miếng cho máy khác. Model tải về nằm lại trên máy:
  `python -m client.detect` mở hộp thoại chọn ảnh trên máy và nhận diện offline, không cần
  server. Client báo trên terminal khi mất kết nối tới server.

Hợp đồng giao thức: [docs/protocol.md](docs/protocol.md) và
[docs/protocol.schema.json](docs/protocol.schema.json).

## Chạy chương trình

Mọi lệnh chạy trong `cmd` tại thư mục gốc repo (Windows). Chạy `chcp 65001` trước nếu chữ tiếng
Việt bị lỗi. Ví dụ dưới dùng server ở `http://127.0.0.1:8000`; chạy trên hai máy thì thay bằng IP
LAN của máy server, ví dụ `http://192.168.137.1:8000` (xem [docs/run_demo.md](docs/run_demo.md)
mục 7 về hotspot và firewall).

### 1. Cài đặt (một lần)

```cmd
pip install -e .[dev]
python tools\download_models.py
python tools\build_registry.py
python tools\download_assets.py
```

`build_registry.py` phải in `4 variant, 4 ready`. Máy chỉ chạy client thì chỉ cần thư mục
`client/` và `samples/imagenet_synset.txt`, cài `pip install requests onnxruntime numpy pillow`.

### 2. Server

```cmd
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Server in `Server sẵn sàng ... 4 variant ready` là xong. Muốn giả lập mạng chậm (để thấy server
chọn model khác nhau và thấy lợi ích P2P), đặt giới hạn tốc độ trước khi chạy:

```cmd
set PBL4_RATE_LIMIT_KBPS=2000
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Bấm **Ctrl+C** để tắt server. Server giữ trạng thái trong RAM, tắt rồi bật lại là trạng thái sạch.

### 3. Client khi CÓ kết nối server: xin và tải model

```cmd
python -m client.agent --server http://127.0.0.1:8000 --device-id laptopB --peer-port 0
```

- Bỏ `--image` thì client mở **hộp thoại chọn ảnh** trên máy; hoặc truyền thẳng
  `--image samples\dog.jpg` (ảnh ở đâu cũng được).
- Client chạy 7 bước: đăng ký, đo băng thông, xin model (server trả lý do chọn), tải từng miếng có
  kiểm SHA-256, nhận diện ảnh (Top-5), gửi feedback.
- Model được **giữ lại trên máy** ở `data\client\<device_id>\<variant_id>\`, kèm
  `data\client\<device_id>\active.json` ghi model đang dùng. Chạy lại cùng lệnh thì không tải lại.

Tham số hay dùng:

| Tham số | Ý nghĩa |
|---|---|
| `--bandwidth 2000` | Giả lập băng thông (kbps) thay vì đo bằng `/api/probe` |
| `--preferred-variant mobilenetv2_int8` | Xin đúng một model, bỏ qua Selector |
| `--peer-port 9000 --serve` | Chia sẻ model cho máy khác (P2P) và **chạy liên tục** đến khi bấm Ctrl+C |
| `--follow-switch` | Server bảo đổi sang model nhẹ hơn thì tự xin lại |

### 4. Client khi NGẮT kết nối server: dùng model đã tải

Chỉ cần đã chạy `client.agent` thành công **một lần** khi có server. Sau đó, kể cả khi tắt máy bật
lại hoặc server đã đóng:

```cmd
python -m client.detect --device-id laptopB
python -m client.detect --device-id laptopB --image samples\dog.jpg samples\cat.jpg
python -m client.detect --device-id laptopB --image samples
```

- Không gọi server. Kiểm SHA-256 model trước khi dùng, nạp model một lần rồi nhận diện mọi ảnh.
- Bỏ `--image`: mở hộp thoại chọn ảnh (chọn được nhiều ảnh), nhận diện xong lại mở hộp thoại cho
  ảnh tiếp theo; bấm **Cancel** để thoát.
- Chỉ có một thiết bị trong `data\client\` thì bỏ được `--device-id`.

`client.agent` cũng tự xử lý khi server đóng:

| Lúc server đóng | Client in ra |
|---|---|
| Trước khi chạy `client.agent` (không kết nối được) | `Chuyển sang chế độ offline`, nhận diện bằng model có sẵn, `HOÀN TẤT (offline)` (bỏ qua feedback) |
| Khi client đang chạy `--serve` | `[giờ] MẤT KẾT NỐI tới server ...`; server mở lại thì `[giờ] Đã kết nối lại server ...` |
| Giữa lúc đang tải hoặc gửi | `[MẤT KẾT NỐI] Không kết nối được server ...` rồi dừng |

Cache chưa có model mà không kết nối được server thì client báo cần chạy lại khi có kết nối.

### 5. Tóm tắt

| Tình huống | Lệnh |
|---|---|
| Bật server | `uvicorn server.main:app --host 0.0.0.0 --port 8000` |
| Có server, tải model lần đầu | `python -m client.agent --server http://<IP>:8000 --device-id laptopB --peer-port 0` |
| Có server, chia sẻ model và chạy liên tục | `python -m client.agent --server http://<IP>:8000 --device-id laptopA --peer-port 9000 --serve` |
| Không có server, nhận diện ảnh | `python -m client.detect --device-id laptopB` |
| Tải lại model từ đầu | `rmdir /s /q data\client\laptopB` rồi chạy lại `client.agent` |

Hướng dẫn demo đầy đủ (các mức băng thông, P2P, fallback, nhận diện offline, hotspot, đọc log,
lỗi hay gặp): **[docs/run_demo.md](docs/run_demo.md)**.

Kiểm thử: `python -m pytest -q`.

## Demo client trên điện thoại Android bằng Termux

Không cần clone hoặc chép toàn bộ repository vào điện thoại. Client chỉ cần thư mục `client/` và file nhãn `samples/imagenet_synset.txt`; các file model `.onnx` vẫn để trên server.

### 1. Cách A: clone toàn bộ repository trên Termux

Sử dụng cách này nếu repository đã được đưa lên GitHub/GitLab. Thay URL bên dưới bằng URL thật của repository:

```bash
pkg update
pkg upgrade -y
pkg install -y git python unzip
termux-setup-storage

cd ~
git clone <REPOSITORY_URL> pbl4-edge-model-distribution
cd ~/pbl4-edge-model-distribution
```

Tạo môi trường và cài dependency:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install requests numpy pillow onnxruntime
```

Kiểm tra cài đặt:

```bash
python --version
python -c "import requests, numpy, PIL; print('client dependencies: OK')"
python -c "import onnxruntime; print('onnxruntime:', onnxruntime.__version__)"
```

Nếu `onnxruntime` không cài được trên Termux, không cần cài lại toàn bộ; nguyên nhân là package này không phải lúc nào cũng có wheel cho Android. Khi đó cần ONNX Runtime Android native để chạy inference trên điện thoại.

### 2. Cách B: tạo gói client tối thiểu trên máy tính

Chạy từ thư mục gốc repository trong PowerShell:

```powershell
Compress-Archive -Path client, samples\imagenet_synset.txt -DestinationPath client-termux.zip -Force
```

Chép `client-termux.zip` sang điện thoại, ví dụ vào thư mục Download. Không chép thư mục `models/`.

### 3. Cài Termux cho gói tối thiểu

Trong Termux:

```bash
pkg update
pkg install python unzip
termux-setup-storage
mkdir -p ~/pbl4-client
cd ~/pbl4-client
unzip ~/storage/shared/Download/client-termux.zip
```

Sau khi giải nén, cấu trúc phải là:

```text
~/pbl4-client/client/
~/pbl4-client/samples/imagenet_synset.txt
```

Cài dependency cho client:

```bash
cd ~/pbl4-client
python -m pip install requests numpy pillow onnxruntime
```

Kiểm tra ONNX Runtime:

```bash
python -c "import onnxruntime; print(onnxruntime.__version__)"
```

Nếu Termux không có wheel `onnxruntime` tương thích, client có thể kết nối và tải model nhưng không chạy inference; khi đó cần dùng ONNX Runtime Android native hoặc chạy client trên máy tính.

### 4. Chạy server trên máy tính

Lấy IP LAN của máy tính bằng `ipconfig`, ví dụ `192.168.1.10`, sau đó chạy:

```powershell
$env:PBL4_PUBLIC_BASE_URL="http://192.168.1.10:8000"
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Điện thoại và máy tính phải cùng Wi-Fi. Nếu Windows Firewall chặn kết nối, mở port 8000 bằng PowerShell Administrator:

```powershell
New-NetFirewallRule -DisplayName "PBL4 Server 8000" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

### 5. Cấu hình và chạy client trên điện thoại

Cho phép Termux truy cập bộ nhớ bằng `termux-setup-storage`. Ảnh trong Download có thể dùng qua đường dẫn:

```text
~/storage/shared/Download/dog.jpg
```

Kiểm tra kết nối tới server:

```bash
python -c "import requests; print(requests.get('http://192.168.1.10:8000/api/probe?size_kb=16', timeout=10).status_code)"
```

Chạy demo với model nhẹ `mobilenetv2_int8`:

```bash
# Nếu clone toàn bộ repo:
cd ~/pbl4-edge-model-distribution
source .venv/bin/activate

# Nếu dùng gói tối thiểu:
# cd ~/pbl4-client

export PBL4_SERVER_URL="http://192.168.1.10:8000"
export PBL4_DEVICE_ID="phoneA"
export PBL4_IMAGE="$HOME/storage/shared/Download/dog.jpg"

python -m client.agent \
  --server "$PBL4_SERVER_URL" \
  --device-id "$PBL4_DEVICE_ID" \
  --platform android \
  --image "$PBL4_IMAGE" \
  --preferred-variant mobilenetv2_int8 \
  --peer-port 0
```

Hoặc chạy trực tiếp không dùng biến môi trường:

```bash
# cd ~/pbl4-edge-model-distribution  # nếu clone toàn bộ repo
# cd ~/pbl4-client                    # nếu dùng gói tối thiểu
python -m client.agent \
  --server http://192.168.1.10:8000 \
  --device-id phoneA \
  --platform android \
  --image ~/storage/shared/Download/dog.jpg \
  --preferred-variant mobilenetv2_int8 \
  --peer-port 0
```

Client sẽ tự đăng ký thông số thiết bị qua `/api/register`, xin model qua `/api/request`, tải và kiểm tra các chunk, chạy inference trên ảnh local, sau đó gửi feedback. Ảnh không bị upload lên server.

## Cấu trúc

| Thư mục | Nội dung |
|---|---|
| `server/` | FastAPI app, Registry, Selector, Planner, token bucket, feedback |
| `client/` | Agent CLI, detect offline, chọn ảnh, theo dõi server, downloader, inference, peer |
| `registry/`, `metadata/model_cards/`, `models/` | Registry sinh tự động, Model Card, file `.onnx` (không commit) |
| `tools/` | Tải model, sinh Registry, tải nhãn/ảnh, kiểm file mẫu giao thức |
| `docs/` | Giao thức, schema, gói mẫu, hướng dẫn demo, báo cáo triển khai |
| `tests/` | Bộ test `pytest` |
| `data/` | Log, feedback, cache client (sinh lúc chạy, không commit) |
