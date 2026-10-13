"""Ghi feedback ra JSONL và quyết định keep/switch cho cả lớp thiết bị."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from server.registry import RegistryVariant
from server.selector import fmt_num

BYTES_PER_MB = 1024 * 1024


@dataclass(frozen=True)
class FeedbackDecision:
    action: str  # "keep" | "switch"
    new_variant_id: str | None
    applies_to_class: str
    reason: str


def decide(
    *,
    variant_id: str,
    latency_ms_median: float,
    device_class: str,
    current: RegistryVariant | None,
    ready_variants: Sequence[RegistryVariant],
    latency_slo_ms: float,
) -> FeedbackDecision:
    """Mặc định keep. Nếu latency vượt ngưỡng và có variant ready nhỏ hơn cùng task,
    chuyển sang variant nhỏ hơn có top-1 cao nhất (bằng nhau thì file nhỏ hơn)."""
    latency = fmt_num(latency_ms_median, 2)
    slo = fmt_num(latency_slo_ms)
    if latency_ms_median <= latency_slo_ms:
        return FeedbackDecision(
            "keep",
            None,
            device_class,
            f"Thời gian suy luận {latency} ms không vượt ngưỡng {slo} ms; "
            f"giữ {variant_id} cho lớp {device_class}.",
        )

    if current is None:
        return FeedbackDecision(
            "keep",
            None,
            device_class,
            f"Thời gian suy luận {latency} ms vượt ngưỡng {slo} ms nhưng {variant_id} "
            "không còn trong Registry nên không so sánh được kích thước; giữ nguyên.",
        )

    smaller = [
        v
        for v in ready_variants
        if v.task_id == current.task_id
        and v.variant_id != current.variant_id
        and v.size_bytes < current.size_bytes
    ]
    if not smaller:
        return FeedbackDecision(
            "keep",
            None,
            device_class,
            f"Thời gian suy luận {latency} ms vượt ngưỡng {slo} ms nhưng không còn phiên bản "
            f"nhỏ hơn {variant_id} cùng task; giữ {variant_id}.",
        )

    target = sorted(smaller, key=lambda v: (-v.top1_accuracy, v.size_bytes, v.variant_id))[0]
    return FeedbackDecision(
        "switch",
        target.variant_id,
        device_class,
        f"Thời gian suy luận {latency} ms vượt ngưỡng {slo} ms; chuyển lớp {device_class} "
        f"từ {variant_id} ({fmt_num(current.size_bytes / BYTES_PER_MB)} MB) sang "
        f"{target.variant_id} ({fmt_num(target.size_bytes / BYTES_PER_MB)} MB, "
        f"top-1 {target.top1_accuracy:.4f}), bản nhỏ hơn có top-1 cao nhất.",
    )


class FeedbackStore:
    """Ghi mỗi feedback thành một dòng JSON trong feedback-YYYYMMDD.jsonl."""

    def __init__(self, feedback_dir: Path) -> None:
        self.feedback_dir = feedback_dir
        self._lock = threading.Lock()

    def path_for(self, when: datetime) -> Path:
        return self.feedback_dir / f"feedback-{when:%Y%m%d}.jsonl"

    def append(self, record: dict[str, Any], when: datetime | None = None) -> Path:
        path = self.path_for(when or datetime.now())
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return path
