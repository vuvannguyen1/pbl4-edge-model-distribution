"""Hai phần độc lập, cùng dùng `selector/artifacts/`:

- `feedback_classifier`: bộ chấm nhận xét của người dùng (PhoBERT hoặc từ khoá). Server dùng
  lúc chạy (`server/service.py`, `server/user_feedback_handler.py`, `server/serve.py`).
- `predict`, `retrain`, `train`: Model Selector học máy, thử nghiệm ngoại tuyến, chưa bật trong
  server (server chọn model bằng `server/selector.py`). Mã huấn luyện nằm ở `training/`.
"""
