# Hướng dẫn chạy demo Version 1 (Windows `cmd`)

Tài liệu này hướng dẫn từng bước chạy server, client, P2P và fallback trên Windows.
Mọi lệnh chạy từ **thư mục gốc repo** (`pbl4-edge-model-distribution`). Trên Linux,
thay `\` bằng `/` và `set X=Y` bằng `export X=Y`.

## 1. Cài đặt (một lần)

Cần Python 3.10 trở lên.

```cmd
cd pbl4-edge-model-distribution
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev]
```

Tải model, sinh Registry, tải nhãn ImageNet và ảnh demo:

```cmd
python tools\download_models.py
python tools\build_registry.py
python tools\download_assets.py
```

- `build_registry.py` phải in `4 variant, 4 ready`. Nếu chỉ có `generated_at` thay đổi
  trong `registry/model_registry.json` thì không cần commit file này.
- `download_assets.py` tạo `samples\imagenet_synset.txt` (1000 nhãn) và `samples\cat.jpg`,
  `samples\dog.jpg`. Nếu không tải được ảnh, tự chép một ảnh `.jpg` vào `samples\`.
- Nếu cửa sổ báo lỗi `UnicodeEncodeError` khi in tiếng Việt (thường gặp khi chuyển hướng
  output ra file), chạy `set PYTHONIOENCODING=utf-8` (hoặc `chcp 65001`) rồi chạy lại.

Kiểm tra mọi thứ hoạt động:

```cmd
python tools\protocol\validate_examples.py
python -m pytest -q
```

## 2. Chạy server

Terminal 1:

```cmd
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Dòng log khởi động xác nhận server sẵn sàng, ví dụ:

```
INFO    Đã nạp Registry: 4 variant, 4 ready (mobilenetv2_fp32, mobilenetv2_int8, resnet50_fp32, resnet50_int8)
INFO    Server sẵn sàng: protocol 1.0, 4 variant ready (...), selector rule-v1, giới hạn tốc độ không giới hạn, peer bật, SLO latency 500 ms, ...
```

Biến môi trường (đặt bằng `set` trước khi chạy `uvicorn`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `PBL4_RATE_LIMIT_KBPS` | `0` | Giới hạn tốc độ tải chung của server (kbps), 0 = không giới hạn |
| `PBL4_PEER_ENABLED` | `true` | `false` để tắt việc giao miếng cho peer |
| `PBL4_LATENCY_SLO_MS` | `500` | Ngưỡng latency; feedback vượt ngưỡng sẽ trả `switch` sang bản nhỏ hơn |
| `PBL4_PUBLIC_BASE_URL` | rỗng | URL server ghi vào plan; rỗng thì lấy theo header `Host` |
| `PBL4_REGISTRY_PATH` | `registry/model_registry.json` | Registry |
| `PBL4_MODEL_STORE` | `models` | Thư mục chứa file `.onnx` |
| `PBL4_DATA_DIR` | `data` | Nơi ghi log và feedback |

Ví dụ giả lập mạng 2 Mbps để thấy thời gian tải thật:

```cmd
set PBL4_RATE_LIMIT_KBPS=2000
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Muốn bỏ giới hạn: `set PBL4_RATE_LIMIT_KBPS=` (hoặc mở terminal mới).

## 3. Chạy client theo từng mức băng thông

Terminal 2. `--bandwidth` (kbps) giả lập băng thông đo được, nên server chọn variant khác nhau;
hạn tải mặc định 60 s:

```cmd
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg --bandwidth 100000
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg --bandwidth 5000
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg --bandwidth 2000
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg --bandwidth 600
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg --bandwidth 100
```

Kết quả mong đợi (ngưỡng tính từ `size_bytes` thật trong Registry, hạn 60 s):

| `--bandwidth` | Variant được chọn | Vì sao |
|---|---|---|
| ≥ 13 677 kbps (ví dụ 100000) | `resnet50_fp32` | top-1 cao nhất (0.7497), 97.8 MB tải trong 8.2 s |
| 3 443 – 13 676 (ví dụ 5000) | `resnet50_int8` | `resnet50_fp32` cần 164.1 s > 60 s |
| 1 862 – 3 442 (ví dụ 2000) | `mobilenetv2_fp32` | `resnet50_int8` cần 103.3 s |
| 488 – 1 861 (ví dụ 600) | `mobilenetv2_int8` | `mobilenetv2_fp32` cần 186.2 s |
| < 488 (ví dụ 100) | lỗi 422 `NO_VARIANT_FITS`, client thoát mã 2 | không bản nào tải kịp |

Client in 7 bước: thông tin thiết bị, đăng ký (`device_class`), đo băng thông, xin model
(variant, lý do, thời gian ước tính, số miếng theo nguồn), tải từng miếng (nguồn, SHA-256),
Top-5 nhãn, phản hồi `keep`/`switch`.

Đo băng thông thật thay vì truyền `--bandwidth`: đặt `PBL4_RATE_LIMIT_KBPS` cho server rồi
bỏ `--bandwidth` khỏi lệnh client. Client tải `/api/probe` (256 KB) và tự tính kbps.

Tham số khác của client:

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--deadline` | `60` | Hạn tải (giây) |
| `--preferred-variant` | không | Xin đúng một variant, bỏ qua Selector |
| `--peer-port` | `9000` | Cổng chia sẻ miếng; `0` = không chia sẻ |
| `--cache-dir` | `data\client\<device_id>` | Nơi lưu model đã tải |
| `--runs` | `20` | Số lần chạy suy luận để lấy trung vị |
| `--follow-switch` | tắt | Feedback trả `switch` thì tự xin lại bản mới (1 lần) |
| `--serve` | tắt | Giữ peer service chạy đến khi bấm Ctrl+C |

Chạy lại cùng lệnh lần hai: client gửi `have_variants`, server đã ghi nhận `complete` nên trả
plan rỗng và client dùng file trong cache. Muốn tải lại từ đầu, xóa cache:
`rmdir /s /q data\client\laptopA`.

Demo `switch`: chạy server với ngưỡng rất thấp, ví dụ `set PBL4_LATENCY_SLO_MS=1`, rồi chạy
client với `--follow-switch`; server trả `switch` sang bản nhỏ hơn và client tự xin lại.

## 4. Demo P2P bằng hai client

Hai client trên cùng một laptop có cùng `device_class`, nên demo được trên một máy.
Mỗi client cần `--device-id` và `--peer-port` khác nhau.

Terminal 1 (server, giới hạn 2 Mbps để thấy rõ lợi ích P2P):

```cmd
set PBL4_RATE_LIMIT_KBPS=2000
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Terminal 2 (client A tải từ server rồi làm nguồn cho máy khác, giữ chạy):

```cmd
python -m client.agent --server http://localhost:8000 --device-id laptopA --image samples\dog.jpg --bandwidth 2000 --peer-port 9000 --serve
```

Đợi đến khi A in `Đang phục vụ peer ở cổng 9000`.

Terminal 3 (client B):

```cmd
python -m client.agent --server http://localhost:8000 --device-id laptopB --image samples\dog.jpg --bandwidth 2000 --peer-port 9100
```

B in `Kế hoạch tải: 7 miếng từ peer, 7 miếng từ server` cùng địa chỉ peer, các dòng
`miếng i ← peer`, và feedback có `chunks_from_peer` > 0. Terminal 2 in
`[peer] gửi miếng ... cho 127.0.0.1`. B tải nhanh hơn A vì miếng từ peer không bị giới hạn tốc độ.

## 5. Demo fallback khi peer tắt

1. Bấm Ctrl+C ở Terminal 2 để tắt peer A.
2. Terminal 3, chạy một client mới (C) không chia sẻ:

```cmd
python -m client.agent --server http://localhost:8000 --device-id laptopC --image samples\dog.jpg --bandwidth 2000 --peer-port 0
```

Server vẫn giao một nửa số miếng cho peer (vì A, B đã báo `complete`), nhưng peer không trả lời.
C in `[cảnh báo] miếng i: peer http://...: không kết nối được (...)`, tải lại từ `fallback_url` của
server, gửi các miếng lỗi trong `bad_chunks`, và vẫn hoàn tất với SHA-256 toàn file khớp.
Sau đó server ghi `Ngừng giao peer ...` và không giao peer lỗi đó cho các lượt sau.

## 6. Nhận diện offline bằng model đã tải

Model tải về nằm lại trên đĩa ở `data\client\<device_id>\<variant_id>\` (file `.onnx` và
`variant.json`), kể cả sau khi tắt máy. `data\client\<device_id>\active.json` ghi model đang dùng.
Chỉ cần chạy `client.agent` **một lần khi có server**; các lần sau nhận diện không cần server:

```cmd
python -m client.detect
python -m client.detect --image samples\dog.jpg
python -m client.detect --image samples\dog.jpg samples\cat.jpg
python -m client.detect --image samples
```

- Bỏ `--image`: mở **hộp thoại chọn ảnh** trên máy (chọn được nhiều ảnh), nhận diện, rồi mở lại
  hộp thoại cho ảnh tiếp theo; bấm **Cancel** để thoát. Model chỉ nạp một lần cho cả phiên. Máy
  không mở được hộp thoại (Termux, SSH) thì gõ đường dẫn, hoặc kéo-thả file vào cửa sổ cmd.
  `client.agent` cũng mở hộp thoại chọn một ảnh khi bỏ `--image`.
- Nạp model một lần rồi nhận diện mọi ảnh (truyền nhiều file, hoặc thư mục ảnh .jpg .jpeg .png
  .bmp .webp), in Top-5 và latency từng ảnh. Không gọi server.
- Kiểm SHA-256 toàn file trước khi dùng; file bị sửa/hỏng thì từ chối và báo cần tải lại.
- Chọn model: `--variant <id>` nếu có, rồi model trong `active.json`, rồi model top-1 cao nhất
  trong cache.
- Chọn cache: `--cache-dir`, rồi `--device-id`; bỏ trống thì dùng thư mục thiết bị duy nhất trong
  `data\client\` (có nhiều thì phải chọn bằng `--device-id`).

`client.agent` cũng tự chuyển offline: nếu không kết nối được server mà cache đã có model hợp lệ,
nó in `Chuyển sang chế độ offline`, chạy suy luận bằng model có sẵn và bỏ qua feedback
(`HOÀN TẤT (offline): ...`). Cache chưa có model thì báo cần chạy lại khi có kết nối server.

Sau khi đăng ký, `client.agent` theo dõi kết nối tới server (thử mở kết nối TCP mỗi 3 s, không
gửi request nên không sinh log phía server). Server đóng hoặc mạng bị ngắt thì client in
`[giờ] MẤT KẾT NỐI tới server ...`, server mở lại thì in `Đã kết nối lại server ...`. Hữu ích
nhất khi chạy `--serve` (client chạy lâu). Mất kết nối giữa lúc đang tải/gửi thì client dừng với
`[MẤT KẾT NỐI] ...`.

Demo: chạy `client.agent ... --serve` như mục 3, bấm Ctrl+C tắt server (client báo mất kết nối),
rồi chạy `client.detect`.

## 7. Chạy qua hotspot hoặc Wi-Fi

1. Bật Mobile Hotspot trên laptop chạy server. IP của laptop thường là `192.168.137.1`
   (xem bằng `ipconfig`).
2. Mở firewall (cmd **Run as Administrator**):

```cmd
netsh advfirewall firewall add rule name="PBL4 server" dir=in action=allow protocol=TCP localport=8000
netsh advfirewall firewall add rule name="PBL4 peer" dir=in action=allow protocol=TCP localport=9000
```

   Thêm luật tương tự cho các cổng peer khác (ví dụ 9100) nếu dùng.
3. Trên máy client thứ hai (cùng mạng):

```cmd
python -m client.agent --server http://192.168.137.1:8000 --device-id laptopB --image samples\dog.jpg --bandwidth 2000
```

- Server lấy IP client từ chính kết nối, nên URL peer trong plan là IP thật của máy kia.
- Nếu Wi-Fi bật **AP/client isolation**, hai client không gọi được nhau: client tự fallback về
  server (xem mục 5). P2P cần mạng không chặn client-client, ví dụ hotspot của laptop.
- Nếu URL trong plan bị sai host (ví dụ đi qua NAT), đặt
  `set PBL4_PUBLIC_BASE_URL=http://192.168.137.1:8000` trước khi chạy server.

## 8. Đọc log

- Log server: console và `data\logs\server-YYYYMMDD.log`. Mỗi dòng của một lượt xin model bắt
  đầu bằng `[r-0001]`: ứng viên, bị loại vì sao, Selector chọn gì, plan theo nguồn, tiến độ
  `/api/have`, miếng lỗi, hoàn tất, feedback.

```cmd
findstr "r-0003" data\logs\server-20261014.log
```

- Feedback: `data\feedback\feedback-YYYYMMDD.jsonl`, mỗi dòng một JSON gồm toàn bộ feedback
  cộng `timestamp` (UTC), `device_class`, `platform`, `runtime`, `selector_version`,
  `selector_reason`, `decision`, `data_kind`.
- Model client đã tải: `data\client\<device_id>\<variant_id>\`.

## 9. Lỗi hay gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `[WinError 10048]` khi chạy uvicorn | Cổng 8000 đang bị chiếm | `netstat -ano \| findstr :8000` rồi `taskkill /PID <pid> /F`, hoặc dùng `--port 8001` |
| Client in `Không mở được cổng peer ...` | Cổng peer đã có chương trình khác dùng (máy demo có dịch vụ Windows chiếm 9001) | Dùng `--peer-port` khác, ví dụ 9100 |
| `NO_VARIANT_FITS` (client thoát mã 2) | Băng thông quá thấp so với hạn | Tăng `--bandwidth` hoặc `--deadline` |
| `Thiếu file nhãn` | Chưa có `samples\imagenet_synset.txt` | `python tools\download_assets.py` |
| Server báo `4 variant, 0 ready` hoặc thiếu file | Chưa tải model / chưa sinh Registry | `python tools\download_models.py` rồi `python tools\build_registry.py` |
| Client nhận `INTERNAL: File model trên server không khớp Registry` | File `.onnx` đổi sau khi sinh Registry | Chạy lại `python tools\build_registry.py` (server tự nạp lại Registry) |
| Máy khác không kết nối được server | Firewall hoặc sai IP | Mở firewall (mục 7), kiểm tra `ipconfig`, thử `curl http://192.168.137.1:8000/api/probe?size_kb=16 -o NUL` |
| P2P luôn fallback | AP isolation, firewall cổng peer, hoặc peer đã tắt | Bình thường với mạng chặn P2P; mở firewall cổng peer, dùng hotspot laptop |
| Chữ tiếng Việt lỗi trong cmd | Code page cũ | `chcp 65001` |
| Tải chậm bất thường với `localhost` | Windows thử IPv6 trước | Client đã tự đổi `localhost` thành `127.0.0.1`; có thể dùng thẳng `http://127.0.0.1:8000` |
