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
