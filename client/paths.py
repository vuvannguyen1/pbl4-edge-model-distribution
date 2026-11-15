"""Đường dẫn và hằng dùng chung của client.

Module lá: không import module nào khác của `client/`, nên agent, detect, feedback_cli và
web_ui đều lấy được đường dẫn mà không phải import lẫn nhau.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = REPO_ROOT / "samples" / "imagenet_synset.txt"
CLIENT_DATA = REPO_ROOT / "data" / "client"  # kho của từng thiết bị: data/client/<device_id>/

BYTES_PER_MB = 1024 * 1024
EXIT_OK = 0
EXIT_ERROR = 1
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".bmp", ".webp"})


def default_cache_dir(device_id: str) -> Path:
    """Kho model và hàng chờ đánh giá mặc định của một thiết bị."""
    return CLIENT_DATA / device_id
