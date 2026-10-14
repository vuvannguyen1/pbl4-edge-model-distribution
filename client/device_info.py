"""Thu thập thông tin thiết bị: platform, cpu_arch, ram_mb, runtime, free_storage_mb.

Chỉ dùng thư viện chuẩn: RAM đọc bằng ctypes (Windows) hoặc /proc/meminfo (Linux).
"""

from __future__ import annotations

import ctypes
import platform as _platform
import re
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

BYTES_PER_MB = 1024 * 1024
_DEVICE_ID_BAD_CHARS = re.compile(r"[^A-Za-z0-9_-]")


@dataclass(frozen=True)
class DeviceInfo:
    platform: str
    cpu_arch: str
    ram_mb: int
    runtime: str
    free_storage_mb: float | None


def detect_platform() -> str:
    """`windows` trên Windows, `linux` cho các hệ còn lại (protocol chưa có macOS)."""
    return "windows" if sys.platform.startswith("win") else "linux"


def sanitize_device_id(name: str) -> str:
    """Giữ ký tự A-Z a-z 0-9 _ -, tối đa 32 ký tự (quy ước device_id)."""
    cleaned = _DEVICE_ID_BAD_CHARS.sub("-", name.strip())[:32].strip("-")
    return cleaned or "device"


def default_device_id() -> str:
    return sanitize_device_id(socket.gethostname())


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def total_ram_mb() -> int:
    """Tổng RAM (MB). Trả 1 nếu không đọc được để vẫn hợp lệ schema (ram_mb >= 1)."""
    try:
        if sys.platform.startswith("win"):
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return max(1, int(status.ullTotalPhys // BYTES_PER_MB))
            return 1
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return max(1, int(line.split()[1]) // 1024)  # giá trị tính bằng kB
    except (OSError, ValueError, AttributeError):
        pass
    return 1


def runtime_string() -> str:
    import onnxruntime

    return f"onnxruntime {onnxruntime.__version__}"


def free_storage_mb(path: Path) -> float | None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return round(shutil.disk_usage(path).free / BYTES_PER_MB, 1)
    except OSError:
        return None


def collect_device_info(cache_dir: Path, platform_override: str | None = None) -> DeviceInfo:
    return DeviceInfo(
        platform=platform_override or detect_platform(),
        cpu_arch=_platform.machine() or "unknown",
        ram_mb=total_ram_mb(),
        runtime=runtime_string(),
        free_storage_mb=free_storage_mb(cache_dir),
    )
