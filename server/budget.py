"""Ngân sách suy luận theo chế độ dùng và tốc độ ước lượng của thiết bị (phát hiện vật thể)."""

from __future__ import annotations

from server.devices import Device
from server.state import StateStore

DEFAULT_LIVE_FPS = 5.0  # camera trực tiếp: tối thiểu 5 khung/giây -> 200 ms/khung
PHOTO_BUDGET_MS = 2500.0  # ảnh chụp: chờ tối đa 2,5 s
# Ngưỡng feedback đề xuất đổi model: vượt ngân sách quá 50 %
SLO_OVER_BUDGET = 1.5
# Tốc độ ước lượng (ms cho mỗi GFLOP) khi lớp thiết bị chưa có số đo thật.
# Đo trên Chrome (onnxruntime-web WASM 1 luồng) và onnxruntime CPU của laptop thử nghiệm.
DEFAULT_MS_PER_GFLOP = {
    ("onnxruntime-web", True): 140.0,  # trình duyệt điện thoại
    ("onnxruntime-web", False): 70.0,  # trình duyệt laptop/PC
    ("onnxruntime", True): 40.0,  # client Python trên Android (Termux)
    ("onnxruntime", False): 12.0,  # client Python trên laptop/PC
}
MAX_THREAD_SPEEDUP = 4  # WASM đa luồng: mỗi luồng thêm nhanh ~50 %, tính tối đa 4 luồng


def budget_for(mode: str | None, target_fps: float | None) -> float | None:
    if mode == "live":
        return 1000.0 / (target_fps or DEFAULT_LIVE_FPS)
    if mode == "photo":
        return PHOTO_BUDGET_MS
    return None


def device_speed(device: Device, state: StateStore) -> tuple[float, str]:
    """ms/GFLOP của thiết bị: số đo thật của lớp nếu có, ngược lại ước lượng theo runtime."""
    measured = state.class_speed(device.device_class)
    if measured is not None:
        return measured, "đo thực tế"
    base = DEFAULT_MS_PER_GFLOP[(device.runtime_family, device.is_mobile)]
    threads = min(device.threads, MAX_THREAD_SPEEDUP)
    return base / (1 + 0.5 * (threads - 1)), "ước lượng"
