"""Số đo và cách viết số dùng chung cho câu lý do và log (selector, feedback, service)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.registry import RegistryVariant

BYTES_PER_MB = 1024 * 1024


def fmt_num(value: float, digits: int = 1) -> str:
    """60.0 -> "60", 41.27 -> "41.3", fmt_num(18.5, 2) -> "18.5": số gọn cho câu lý do."""
    text = f"{round(float(value), digits):.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def download_seconds(size_bytes: int, bandwidth_kbps: float) -> float:
    """Thời gian tải = size_bytes × 8 / 1000 / bandwidth_kbps (1 kbps = 1000 bit/s)."""
    return size_bytes * 8 / 1000 / bandwidth_kbps


def estimate_download_s(size_bytes: int, bandwidth_kbps: float | None) -> float | None:
    if not bandwidth_kbps:
        return None
    return round(download_seconds(size_bytes, bandwidth_kbps), 1)


def quality_number(variant: RegistryVariant) -> str:
    """mAP dạng phần trăm ("25.8 %"), top-1 giữ dạng cũ ("0.7180")."""
    if variant.map50_95 is not None:
        return f"{variant.map50_95 * 100:.1f} %"
    return f"{variant.top1_accuracy:.4f}"


def quality_text(variant: RegistryVariant) -> str:
    """Chất lượng cho câu lý do: "mAP 25.8 %" hoặc "top-1 0.7180"."""
    return f"{variant.quality_label} {quality_number(variant)}"
