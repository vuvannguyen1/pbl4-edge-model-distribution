# pbl4-edge-model-distribution

Hệ thống phân phối model **phát hiện vật thể** (ONNX) cho điện thoại, laptop và thiết bị edge
theo cấu hình và băng thông, có P2P giữa các thiết bị cùng cấu hình. PBL4.

- **Server** (Python FastAPI): đọc danh mục 10 model CV từ Registry (YOLOX nano→x, SSD
  MobileNet FP32/INT8, Faster R-CNN INT8, RetinaNet), chọn bản có mAP cao nhất mà thiết bị
  tải kịp, đủ RAM, chạy được (trình duyệt hay Python) và đủ nhanh cho chế độ dùng (camera trực
  tiếp hay ảnh chụp); lập kế hoạch tải theo miếng 1 MiB, ghép P2P giữa các máy có cùng model, nhận
  feedback và học tốc độ thật của từng lớp thiết bị.
- **Giao diện client `/app`** (trình duyệt, không cài gì): mở link là tự đăng ký, xin model, tải
  từng miếng (từ máy khác đã có model qua WebRTC, không có thì từ server) có kiểm SHA-256, lưu model trong trình
  duyệt và **phát hiện vật thể bằng camera** (onnxruntime-web, chạy ngay trên máy).
- **Client Python** (`client.agent`, `client.detect`): cho thiết bị không có trình duyệt; chạy
  được cả 10 model trên ảnh, làm peer HTTP chia sẻ miếng cho máy khác, nhận diện offline.
- **Dashboard** `/ui/`: link + mã QR cho client, thiết bị và P2P, Registry, quyết định chọn
  model, truyền model, đánh giá.

Hợp đồng giao thức: [docs/protocol.md](docs/protocol.md) và
[docs/protocol.schema.json](docs/protocol.schema.json).

## Bắt đầu nhanh: client chỉ cần mở link

Trên máy server (một lần: `pip install -e .[dev]`, `python tools\download_models.py`,
`python tools\download_assets.py`):

```cmd
python -m server.serve
```

Lệnh in đúng một link cho client và một link dashboard, theo IP của mạng đang dùng, ví dụ:

```text
 Link cho client (điện thoại, laptop cùng mạng): https://172.20.10.2:8443/app/
 Link dashboard của server:                      http://172.20.10.2:8000/
```

- Người dùng mở link (hoặc quét mã QR ở trang Tổng quan của dashboard), bấm **Xin model**, rồi
  **Bật camera**. Lần đầu mở bản HTTPS, trình duyệt báo chứng chỉ không tin cậy (chứng chỉ tự ký
  của server): chọn *Nâng cao → Tiếp tục*.
- IP đổi khi đổi mạng. Máy có nhiều card mạng (VMware, VirtualBox...) mà link in sai IP thì chỉ định
  bằng `python -m server.serve --ip <IP đúng>`.
- Điện thoại không mở được link: mở tường lửa (PowerShell chạy bằng quyền Administrator):
  `New-NetFirewallRule -DisplayName "PBL4 server" -Direction Inbound -Protocol TCP -LocalPort 8000,8443 -Action Allow -Profile Any`
- Trình duyệt chỉ cho mở camera liên tục trên HTTPS. Muốn chạy không cần chứng chỉ: `--no-https`,
  link client thành `http://<IP>:8000/app/`, chỉ chụp được từng ảnh.
- Chứng chỉ tạo một lần trong `data\certs\` (cần `pip install cryptography`, hoặc openssl có sẵn
  trong Git for Windows). Tham số: `--port 8000 --https-port 8443`, `--no-https`.

Lần đầu mở link, người dùng đặt tên cho máy (hai máy đang hoạt động không được trùng tên), đổi lại
được ở thẻ **Máy này**. Tên này hiện cho máy khác khi chia sẻ model. Giao diện client có 3 tab:

| Tab | Làm gì |
|---|---|
| Model | Chọn **Camera trực tiếp** (server chọn model ≥ 5 khung/giây trên máy này) hoặc **Ảnh chụp** (model chính xác hơn, ≤ 2,5 s/ảnh); xem các bước, lý do server chọn, lưới miếng S (server) / P (peer). Model lưu trong trình duyệt, lần sau mở link dùng ngay. Khi trang mở, máy khác xin cùng model (khác cấu hình cũng được) tải toàn bộ từ máy này, và được báo "Bạn đã nhận được mô hình ... từ máy ..." |
| Camera | Camera trực tiếp có khung + tên vật thể tiếng Việt, FPS; chụp/chọn ảnh; ngưỡng tin cậy; lọc theo loại vật thể; đổi camera trước/sau |
| Đánh giá | Chọn 1–5 sao và viết nhận xét, dùng được ngay khi máy có model, không cần chụp khung hình (`POST /api/feedback`) |

## Chạy chương trình

Mọi lệnh chạy trong `cmd` tại thư mục gốc repo (Windows). Chạy `chcp 65001` trước nếu chữ tiếng
Việt bị lỗi. Ví dụ dưới dùng server ở `http://127.0.0.1:8000`; chạy trên hai máy thì thay bằng IP
LAN của máy server, ví dụ `http://192.168.137.1:8000` (xem [docs/run_demo.md](docs/run_demo.md)
mục 7 về hotspot và firewall).

### 1. Cài đặt (một lần)

```cmd
pip install -e .[dev]
python tools\download_models.py
python tools\download_assets.py
```

Chấm bình luận đánh giá bằng PhoBERT cần thêm `pip install -e .[phobert]` trên máy server (thiếu
thì server chấm theo từ khoá, `comment_engine` trả `lexicon`). Điểm đánh giá = số sao × 0,2 +
điểm PhoBERT (1–5) × 0,8; không có bình luận thì điểm bằng số sao.

`download_models.py` tải và kiểm tra đủ 10 model CV. `download_assets.py` tải thêm
onnxruntime-web vào `server/webclient/vendor/` để điện thoại không cần Internet (thiếu thì trang
`/app` lấy từ CDN).

Model Card của 10 model nằm trong `metadata/model_cards/` (cách tiền xử lý, giải mã đầu ra, mAP,
GFLOPs, runtime, RAM). Sinh lại Model Card và Registry sau khi đổi model:

```cmd
python tools\curate_detection_registry.py
```

Script gọi `tools\build_registry.py`: băm từng miếng 1 MiB, mở model bằng ONNX Runtime và **chạy
thử trên `samples\dog.jpg`** (phải thấy "dog") mới cho `ready`. Kết quả mong đợi `10 variant,
10 ready`. Máy chỉ chạy client Python thì chỉ cần thư mục `client/`, cài
`pip install requests onnxruntime numpy pillow`.

### 2. Server

```cmd
python -m server.serve
```

(hoặc lệnh cũ `uvicorn server.main:app --host 0.0.0.0 --port 8000`, chỉ có HTTP.) Server in
`Server sẵn sàng ... 10 variant ready` là xong. Muốn giả lập mạng chậm (để thấy server
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
  kiểm SHA-256, phát hiện vật thể trên ảnh (bảng vật thể, độ tin cậy, hộp), gửi feedback.
- `--mode photo` (mặc định): server chọn model chạy một ảnh trong ~2,5 s trên máy này; `--mode live`:
  ~5 khung/giây; `--mode none`: không lọc theo tốc độ (như protocol 1.0, có thể nhận yolox_x 378 MB).
- Model được **giữ lại trên máy** ở `data\client\<device_id>\<variant_id>\` (file `.onnx` và
  `variant.json` ghi kích thước, SHA-256, phiên bản, task, input, runtime và trạng thái
  `partial`/`complete`/`invalid`), kèm `data\client\<device_id>\active.json` ghi model đang dùng.
- Lần chạy sau, nếu kho đã có model `complete` đúng SHA-256 thì client **không gửi
  `/api/request`**, nhận diện ngay bằng model đó. Model bị sửa/hỏng thì bị đánh `invalid` và được
  tải lại khi có server. Thêm `--refresh` để buộc hỏi server (vd. kiểm tra bản mới).

Tham số hay dùng:

| Tham số | Ý nghĩa |
|---|---|
| `--bandwidth 2000` | Giả lập băng thông (kbps) thay vì đo bằng `/api/probe` |
| `--preferred-variant mobilenetv2_int8` | Xin đúng một model, bỏ qua Selector |
| `--peer-port 9000 --serve` | Chia sẻ model cho máy khác (P2P) và **chạy liên tục** đến khi bấm Ctrl+C |
| `--follow-switch` | Server bảo đổi sang model nhẹ hơn thì tự xin lại |
| `--refresh` | Hỏi server dù máy đã có model hợp lệ |

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
| Trước khi chạy `client.agent` (chờ mở kết nối tối đa 5 s) | `Chuyển sang chế độ offline`, nhận diện bằng model `complete` có sẵn, `HOÀN TẤT (offline)` (bỏ qua feedback tự động) |
| Khi client đang chạy `--serve` | `[giờ] MẤT KẾT NỐI tới server ...`; server mở lại thì `[giờ] Đã kết nối lại server ...` và gửi bù đánh giá `pending` |
| Giữa lúc xin/tải model | Chuyển sang offline nếu kho có model `complete` khác; không có thì dừng |
| Sau khi đã nhận diện xong | Bỏ qua gửi số đo, vẫn `HOÀN TẤT` |

Cache chưa có model mà không kết nối được server thì client báo chưa thể detect offline vì chưa có
model và cần chạy lại khi có kết nối. Server tắt không bao giờ làm xóa model trên máy.

### 5. Gửi đánh giá kết quả nhận diện

Sau `client.agent` hoặc `client.detect`, người dùng tự gửi đánh giá bằng lệnh:

```cmd
python -m client.feedback_cli --device-id laptopB --server http://127.0.0.1:8000
```

- CLI lấy model và dự đoán của **lần detect gần nhất** (`data\client\<device_id>\last_detect.json`),
  rồi hỏi: điểm (1-5), dự đoán có đúng không, nhãn thực tế (nếu biết), nhận xét. Có thể trả lời
  trước bằng `--variant-id`, `--rating 5 --correct yes --expected-label ... --comment ...`.
- Ở câu hỏi điểm và câu hỏi đúng/sai, **nhấn Enter bỏ trống là thoát**: đánh giá không được lưu
  vào hàng chờ. (Enter ở nhãn thực tế/nhận xét vẫn là bỏ qua câu đó.)
- Server đã chạy lại mà còn đánh giá `pending` từ lần trước, CLI liệt kê chúng và hỏi
  `Gửi N đánh giá lần trước? (y/n)`: `y` thì **chỉ gửi các đánh giá đó**, không phải đánh giá lại;
  `n` thì giữ chúng `pending` và nhập đánh giá mới (chỉ gửi đánh giá mới).
- Đánh giá được ghi `pending` vào `data\client\<device_id>\feedback_queue.jsonl` **trước**, rồi
  gửi `POST /api/feedback` nếu server đang chạy. Server trả kết quả đánh giá (`positive`/
  `neutral`/`negative`, thống kê của model); bản ghi chỉ thành `sent` khi server xác nhận.
- Server tắt: giữ `pending`, không phải nhập lại. Gửi bù khi server mở lại:

```cmd
python -m client.feedback_cli --sync-pending --server http://127.0.0.1:8000
```

  (`client.agent` cũng tự gửi bù khi kết nối được server.) Mỗi đánh giá có `feedback_id` riêng nên
  gửi lại nhiều lần server cũng chỉ ghi một lần (`data\feedback\manual-feedback-YYYYMMDD.jsonl`).

### 6. Giao diện web

Giao diện client `/app` (điện thoại, laptop) xem mục **Bắt đầu nhanh** ở đầu README. Các phần
mở rộng giao thức cho trình duyệt (capabilities, mode, khối detection, P2P WebRTC) ở
[docs/protocol.md](docs/protocol.md) mục 10: chỉ thêm trường tuỳ chọn, client 1.0 cũ không vỡ.
Font, icon và thư viện mã QR đã nằm trong repo nên chạy được cả khi không có internet.

**Dashboard server** (theo dõi toàn hệ thống). Bật server như mục 2 rồi mở trên trình duyệt:

```text
http://<IP máy server>:8000/      (tự chuyển tới /ui/)
```

| Màn hình | Bọc chức năng |
|---|---|
| Tổng quan | **Link + mã QR cho client**, số variant/thiết bị (trình duyệt / Python)/peer/request, tỉ lệ chunk peer–server, cảnh báo, cấu hình server, tốc độ suy luận đo được theo lớp thiết bị |
| Thiết bị & P2P | Thiết bị đã đăng ký, lớp thiết bị, runtime và số luồng, lần cuối liên lạc, chia sẻ peer qua WebRTC (đang mở trang hay đã đóng) hoặc HTTP |
| Registry model | `registry/model_registry.json` + Model Card: mAP, GFLOPs, chạy trên trình duyệt hay chỉ Python, cách giải mã đầu ra, chi tiết từng chunk; thêm model mới |
| Quyết định chọn model | Lý do Selector chọn, từng bước lọc ứng viên; form **Xin model thử** (`POST /api/request`) |
| Truyền model | Tiến độ từng lượt tải, lưới chunk theo nguồn server/peer, miếng lỗi |
| Đánh giá model | Số đo agent, đánh giá người dùng (có form gửi `POST /api/feedback`), nhận xét văn bản |
| Log sự kiện | Log server trong ngày, lọc theo mức và `request_id` |

Dashboard tự cập nhật mỗi 2 giây (nút **Tạm dừng** trên cùng). Dữ liệu thiết bị và request nằm
trong RAM của server nên khởi động lại server là mất; feedback và log thì còn (file trong `data/`).

**Giao diện client** (thay cho `client.agent` + `client.feedback_cli` trên máy client):

```cmd
python -m client.web_ui --server http://<IP>:8000 --device-id laptopA --open
```

Mở `http://127.0.0.1:8080`: chọn/kéo thả ảnh hoặc ảnh mẫu, bấm **Nhận diện** (máy có model thì dùng
ngay, chưa có thì xin server và tải, mất server thì chạy offline), xem tiến trình 7 bước và lưới
miếng, Top-5, rồi gửi đánh giá (server tắt thì lưu chờ, có nút **Gửi bù**).

| Tham số | Ý nghĩa |
|---|---|
| `--port 8081` | Đổi cổng giao diện (mặc định 8080) |
| `--peer-port 9000` | Chia sẻ model đã tải cho máy khác (P2P), như `client.agent --serve` |
| `--host 0.0.0.0` | Cho máy khác mở giao diện này (mặc định chỉ máy client) |
| `--cache-dir`, `--runs` | Như `client.agent` |

Trên Termux: chạy cùng lệnh rồi mở `http://127.0.0.1:8080` bằng trình duyệt của điện thoại.

### 7. Tóm tắt

| Tình huống | Lệnh |
|---|---|
| Bật server + in link cho client | `python -m server.serve` |
| Client điện thoại/laptop | Mở `https://<IP>:8443/app/` (hoặc quét QR ở dashboard) |
| Xem dashboard | Mở `http://<IP>:8000/` |
| Sinh lại Model Card + Registry | `python tools\curate_detection_registry.py` |
| Giao diện web cho client | `python -m client.web_ui --server http://<IP>:8000 --device-id laptopB --open` |
| Có server, tải model lần đầu | `python -m client.agent --server http://<IP>:8000 --device-id laptopB --peer-port 0` |
| Có server, chia sẻ model và chạy liên tục | `python -m client.agent --server http://<IP>:8000 --device-id laptopA --peer-port 9000 --serve` |
| Không có server, nhận diện ảnh | `python -m client.detect --device-id laptopB` |
| Gửi đánh giá lần detect gần nhất | `python -m client.feedback_cli --device-id laptopB --server http://<IP>:8000` |
| Gửi bù đánh giá còn pending | `python -m client.feedback_cli --sync-pending --server http://<IP>:8000` |
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
| `server/` | FastAPI app, Registry, Selector, Planner, token bucket, feedback; `admin_api.py` + `static/` là dashboard `/ui`; `webclient/` là giao diện client `/app`; `signaling.py` signaling WebRTC; `serve.py` lệnh bật server + HTTPS + in link |
| `client/` | Agent CLI, detect offline, chọn ảnh, theo dõi server, downloader, inference (`detection.py`: giải mã 4 định dạng model phát hiện vật thể), peer HTTP; `web_ui.py` + `web/` là giao diện web cho client Python |
| `metadata/model_cards/` | Model Card của 10 model CV (sinh bằng `tools/curate_detection_registry.py`) |
| `client/web/shared/` | CSS, font, icon, JS dùng chung cho cả hai giao diện web (xem `docs/design-system.md`) |
| `design-system/` | Kết quả gốc của skill ui-ux-pro-max |
| `registry/`, `models/` | Registry sinh tự động, file `.onnx` (không commit) |
| `tools/` | Tải model, sinh Registry, tải nhãn/ảnh, kiểm file mẫu giao thức |
| `docs/` | Giao thức, schema, gói mẫu, hướng dẫn demo, báo cáo triển khai |
| `tests/` | Bộ test `pytest` |
| `data/` | Log, feedback, cache client (sinh lúc chạy, không commit) |
