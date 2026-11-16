"""Cấu hình, kết quả và lỗi của `client.agent.Agent`.

Module lá (chỉ phụ thuộc `client.paths` và các kiểu dữ liệu của downloader/inference), để
`client.agent_cli` dựng `AgentConfig` mà không phải import `client.agent`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from client.downloader import DownloadResult
from client.inference import InferenceResult
from client.paths import DEFAULT_LABELS, EXIT_ERROR, default_cache_dir

EXIT_NO_VARIANT = 2


class AgentError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class AgentConfig:
    server: str
    device_id: str
    image: Path | None  # None: main() mở hộp thoại chọn ảnh trước khi chạy
    deadline_s: float = 60.0
    bandwidth_kbps: float | None = None
    preferred_variant: str | None = None
    peer_port: int = 9000
    cache_dir: Path | None = None
    runs: int = 20
    platform: str | None = None
    follow_switch: bool = False
    serve: bool = False
    labels: Path = DEFAULT_LABELS
    probe_kb: int = 256
    refresh: bool = False  # True: hỏi server dù kho đã có model hợp lệ
    # photo: server chọn model chạy một ảnh trong ~2,5 s trên máy này; live: ~5 khung/giây;
    # None: không lọc theo tốc độ (như protocol 1.0)
    mode: str | None = "photo"

    def resolved_cache_dir(self) -> Path:
        return self.cache_dir or default_cache_dir(self.device_id)


@dataclass
class AgentResult:
    device_class: str
    request_id: str
    variant: dict[str, Any]
    model_path: Path
    download_ms: float
    download: DownloadResult | None
    inference: InferenceResult
    feedback_payload: dict[str, Any]
    feedback: dict[str, Any]
    history: list[str] = field(default_factory=list)  # các variant đã xin, theo thứ tự
    offline: bool = False  # True: không kết nối được server, dùng model trong cache
    source: str = "server"  # "server": vừa xin model; "cache": dùng model có sẵn, không xin
