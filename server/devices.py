"""Lưu thiết bị đã đăng ký và tính device_class (mục 7 của protocol)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

_ARCH_MAP = {
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm32",
    "armv8l": "arm32",
    "x86_64": "x64",
    "amd64": "x64",
}

RAM_LOW_MB = 4096
RAM_HIGH_MB = 8192


def arch_bucket(cpu_arch: str) -> str | None:
    """`aarch64`/`arm64` -> arm64, `armv7l`/`armv8l` -> arm32, `x86_64`/`amd64` -> x64."""
    return _ARCH_MAP.get(cpu_arch.strip().lower())


def ram_bucket(ram_mb: int) -> str:
    if ram_mb < RAM_LOW_MB:
        return "lt4gb"
    if ram_mb <= RAM_HIGH_MB:
        return "4-8gb"
    return "gt8gb"


def compute_device_class(platform: str, cpu_arch: str, ram_mb: int) -> str:
    """Trả `{platform}_{arch}_{ram}`; ném ValueError nếu kiến trúc không thuộc 3 nhóm."""
    arch = arch_bucket(cpu_arch)
    if arch is None:
        raise ValueError(
            f"Kiến trúc CPU '{cpu_arch}' không được hỗ trợ "
            "(cần aarch64/arm64, armv7l/armv8l hoặc x86_64/amd64)."
        )
    return f"{platform}_{arch}_{ram_bucket(ram_mb)}"


@dataclass(frozen=True)
class Device:
    device_id: str
    platform: str
    cpu_arch: str
    ram_mb: int
    runtime: str
    peer_port: int | None
    ip: str
    device_class: str
    registered_at: str

    @property
    def peer_origin(self) -> str | None:
        """`http://<ip>:<peer_port>` nếu thiết bị có thể phục vụ peer."""
        if self.peer_port is None or not self.ip:
            return None
        host = f"[{self.ip}]" if ":" in self.ip else self.ip
        return f"http://{host}:{self.peer_port}"


class DeviceStore:
    """Thiết bị đã đăng ký, giữ trong RAM của tiến trình server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._devices: dict[str, Device] = {}

    def register(
        self,
        *,
        device_id: str,
        platform: str,
        cpu_arch: str,
        ram_mb: int,
        runtime: str,
        peer_port: int | None,
        ip: str,
    ) -> tuple[Device, bool]:
        """Đăng ký hoặc cập nhật thiết bị. Trả (thiết bị, có phải đăng ký lại không)."""
        device = Device(
            device_id=device_id,
            platform=platform,
            cpu_arch=cpu_arch,
            ram_mb=ram_mb,
            runtime=runtime,
            peer_port=peer_port,
            ip=ip,
            device_class=compute_device_class(platform, cpu_arch, ram_mb),
            registered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        with self._lock:
            existed = device_id in self._devices
            self._devices[device_id] = device
        return device, existed

    def get(self, device_id: str) -> Device | None:
        with self._lock:
            return self._devices.get(device_id)

    def all(self) -> list[Device]:
        with self._lock:
            return list(self._devices.values())
