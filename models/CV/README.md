# Model store: Computer Vision

Chỉ lưu model phục vụ các tác vụ Computer Vision đã được kiểm định. Model source và
artifact phân phối phải được đăng ký trong database trước khi có trạng thái `ready`.

Các model hiện được phân phối trong domain CV:

- `yolox_nano`, `yolox_tiny`, `yolox_s`, `yolox_m`, `yolox_l`, `yolox_x`
- `ssd_mobilenet_v1_12`, `ssd_mobilenet_v1_12_int8`
- `fasterrcnn_r50_12_int8`, `retinanet_resnet101`

File `.onnx` không commit vào Git. Sau khi clone/pull, chạy từ thư mục gốc repo:

```text
python tools\download_models.py
```

Script đọc `data/model_sources.json`, tải đủ 10 model và kiểm tra kích thước cùng SHA-256.
Các model khác domain phải nằm trong thư mục domain tương ứng, ví dụ `models/NLP`.
