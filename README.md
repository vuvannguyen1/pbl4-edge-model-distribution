# pbl4-edge-model-distribution

Hệ thống phân phối model AI (ONNX) cho thiết bị edge theo băng thông, PBL4 Version 1.

- **Server** (Python FastAPI): đọc danh mục model động từ Registry, chọn phiên bản model
  (MobileNetV2/ResNet-50, FP32/INT8) vừa hạn tải theo băng thông và dung lượng của thiết bị,
  lập kế hoạch tải theo miếng 1 MiB, phục vụ miếng có giới hạn tốc độ, ghép P2P giữa các máy
  cùng lớp và nhận feedback.
- **Client agent** (Python CLI): đăng ký, đo băng thông, xin model, tải tối đa 4 miếng song
  song có kiểm SHA-256 và fallback, chạy phân loại ảnh bằng ONNX Runtime (Top-5), gửi số đo
  thực tế; đồng thời làm peer chia sẻ miếng cho máy khác.

Hợp đồng giao thức: [docs/protocol.md](docs/protocol.md) và
[docs/protocol.schema.json](docs/protocol.schema.json).

## Chạy nhanh

```cmd
pip install -e .[dev]
python tools\download_models.py
python tools\build_registry.py
python tools\download_assets.py

uvicorn server.main:app --host 0.0.0.0 --port 8000
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg
```

Hướng dẫn demo đầy đủ (các mức băng thông, P2P, fallback, hotspot, đọc log, lỗi hay gặp):
**[docs/run_demo.md](docs/run_demo.md)**.

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
| `client/` | Agent CLI, downloader, inference, peer service |
| `registry/`, `metadata/model_cards/`, `models/` | Registry sinh tự động, Model Card, file `.onnx` (không commit) |
| `tools/` | Tải model, sinh Registry, tải nhãn/ảnh, kiểm file mẫu giao thức |
| `docs/` | Giao thức, schema, gói mẫu, hướng dẫn demo, báo cáo triển khai |
| `tests/` | Bộ test `pytest` |
| `data/` | Log, feedback, cache client (sinh lúc chạy, không commit) |
