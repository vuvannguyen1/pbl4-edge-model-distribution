"""Feedback tự động của agent (số đo): ghi JSONL và quyết định keep/switch cho cả lớp thiết bị."""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from server.registry import RegistryVariant
from server.textfmt import BYTES_PER_MB, download_seconds, fmt_num, quality_text


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
    runtime_family: str | None = None,
    upgrade_budget_ms: float | None = None,
    bandwidth_kbps: float | None = None,
    deadline_s: float | None = None,
) -> FeedbackDecision:
    """Mặc định keep.

    - Latency vượt ngưỡng và có variant ready nhỏ hơn cùng task (thiết bị chạy được): chuyển
      sang bản nhỏ hơn có chất lượng cao nhất (top-1 hoặc mAP; bằng nhau thì file nhỏ hơn).
    - Có `upgrade_budget_ms` (lượt xin có mode) và máy chạy dư sức: nếu một bản chất lượng cao
      hơn, ước lượng theo tỉ lệ GFLOPs, vẫn chạy trong 80 % ngân sách và tải kịp hạn ở băng thông
      của lượt đó thì đề xuất nâng cấp.
    """
    latency = fmt_num(latency_ms_median, 2)
    slo = fmt_num(latency_slo_ms)
    compatible = [
        v for v in ready_variants if runtime_family is None or v.supports_runtime(runtime_family)
    ]
    if latency_ms_median <= latency_slo_ms:
        downloadable = [
            v
            for v in compatible
            if not (bandwidth_kbps and deadline_s)
            or download_seconds(v.size_bytes, bandwidth_kbps) <= deadline_s
        ]
        upgrade = _upgrade(current, downloadable, latency_ms_median, upgrade_budget_ms)
        if upgrade is not None and current is not None:
            target, estimate = upgrade
            return FeedbackDecision(
                "switch",
                target.variant_id,
                device_class,
                f"Thời gian suy luận {latency} ms, còn dư so với ngân sách "
                f"{fmt_num(upgrade_budget_ms or 0)} ms; nâng lớp {device_class} từ {variant_id} "
                f"({quality_text(current)}) lên {target.variant_id} ({quality_text(target)}, "
                f"ước tính {fmt_num(estimate)} ms theo tỉ lệ GFLOPs).",
            )
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
        for v in compatible
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

    target = sorted(smaller, key=lambda v: (-v.quality, v.size_bytes, v.variant_id))[0]
    return FeedbackDecision(
        "switch",
        target.variant_id,
        device_class,
        f"Thời gian suy luận {latency} ms vượt ngưỡng {slo} ms; chuyển lớp {device_class} "
        f"từ {variant_id} ({fmt_num(current.size_bytes / BYTES_PER_MB)} MB) sang "
        f"{target.variant_id} ({fmt_num(target.size_bytes / BYTES_PER_MB)} MB, "
        f"{quality_text(target)}), bản nhỏ hơn có {target.quality_label} cao nhất.",
    )


UPGRADE_HEADROOM = 0.8


def _upgrade(
    current: RegistryVariant | None,
    compatible: Sequence[RegistryVariant],
    latency_ms: float,
    budget_ms: float | None,
) -> tuple[RegistryVariant, float] | None:
    if budget_ms is None or current is None or not current.gflops:
        return None
    options = []
    for v in compatible:
        if v.task_id != current.task_id or v.quality <= current.quality or not v.gflops:
            continue
        estimate = latency_ms * v.gflops / current.gflops
        if estimate <= budget_ms * UPGRADE_HEADROOM:
            options.append((v, estimate))
    if not options:
        return None
    return max(options, key=lambda o: (o[0].quality, -o[0].size_bytes))


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
