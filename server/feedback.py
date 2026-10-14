"""Ghi feedback ra JSONL và quyết định keep/switch cho cả lớp thiết bị.

Feedback tự động của agent (số đo) quyết định keep/switch; đánh giá thủ công của người
dùng (`client.feedback_cli`) đi qua `evaluate_manual` và được chống ghi trùng theo
`feedback_id`.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
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


# ---------------------------------------------------------------- đánh giá thủ công

LOW_RATING = 2  # từ 2 sao trở xuống là đánh giá xấu
GOOD_RATING = 4
REVIEW_MIN_COUNT = 5  # đủ số lượt mới kết luận về cả variant
REVIEW_CORRECT_RATE = 0.5


@dataclass(frozen=True)
class VariantStats:
    count: int = 0
    correct: int = 0
    rating_sum: int = 0

    def add(self, correct: bool, rating: int) -> VariantStats:
        return VariantStats(self.count + 1, self.correct + int(correct), self.rating_sum + rating)

    @property
    def correct_rate(self) -> float:
        return self.correct / self.count if self.count else 0.0

    @property
    def avg_rating(self) -> float:
        return self.rating_sum / self.count if self.count else 0.0


def evaluate_manual(
    *, variant_id: str, correct: bool, rating: int, stats: VariantStats
) -> dict[str, Any]:
    """Đánh giá một feedback và cả variant (`stats` đã gồm feedback này).

    verdict: `negative` nếu dự đoán sai hoặc <= 2 sao, `positive` nếu đúng và >= 4 sao,
    còn lại `neutral`. `needs_review` khi variant có >= 5 lượt mà tỉ lệ đúng < 50 %.
    """
    if not correct or rating <= LOW_RATING:
        verdict = "negative"
    elif rating >= GOOD_RATING:
        verdict = "positive"
    else:
        verdict = "neutral"
    needs_review = stats.count >= REVIEW_MIN_COUNT and stats.correct_rate < REVIEW_CORRECT_RATE
    summary = (
        f"{variant_id} có {stats.count} đánh giá, đúng {stats.correct_rate * 100:.0f} %, "
        f"trung bình {stats.avg_rating:.1f} sao"
    )
    if needs_review:
        reason = (
            f"{summary}: tỉ lệ đúng dưới {REVIEW_CORRECT_RATE * 100:.0f} % sau ít nhất "
            f"{REVIEW_MIN_COUNT} lượt, cần xem lại variant này."
        )
    else:
        reason = f"{summary}."
    return {
        "verdict": verdict,
        "needs_review": needs_review,
        "variant_feedback_count": stats.count,
        "variant_correct_rate": round(stats.correct_rate, 4),
        "variant_avg_rating": round(stats.avg_rating, 2),
        "reason": reason,
    }


class ManualFeedbackStore:
    """Ghi đánh giá thủ công vào manual-feedback-YYYYMMDD.jsonl, không ghi trùng feedback_id.

    Khi khởi động đọc lại các file cũ để nhớ feedback_id đã nhận và thống kê từng variant,
    nên client gửi lại sau khi server khởi động lại vẫn không bị ghi hai lần.
    """

    PATTERN = "manual-feedback-*.jsonl"

    def __init__(self, feedback_dir: Path) -> None:
        self.feedback_dir = feedback_dir
        self._lock = threading.Lock()
        self._seen: dict[str, dict[str, Any]] = {}
        self._stats: dict[str, VariantStats] = {}
        self._load()

    def _load(self) -> None:
        if not self.feedback_dir.is_dir():
            return
        for path in sorted(self.feedback_dir.glob(self.PATTERN)):
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    feedback_id = entry["feedback_id"]
                    stats = self._stats.get(entry["variant_id"], VariantStats())
                    self._stats[entry["variant_id"]] = stats.add(
                        bool(entry["correct"]), int(entry["rating"])
                    )
                except (ValueError, KeyError, TypeError):
                    continue
                self._seen.setdefault(feedback_id, entry)

    def path_for(self, when: datetime) -> Path:
        return self.feedback_dir / f"manual-feedback-{when:%Y%m%d}.jsonl"

    def submit(
        self,
        record: dict[str, Any],
        make_entry: Callable[[VariantStats], dict[str, Any]],
        when: datetime,
    ) -> tuple[dict[str, Any], bool, Path | None]:
        """Ghi feedback mới. Trả (bản ghi, đã nhận trước đó chưa, file đã ghi)."""
        with self._lock:
            previous = self._seen.get(record["feedback_id"])
            if previous is not None:
                return previous, True, None
            stats = self._stats.get(record["variant_id"], VariantStats()).add(
                record["correct"], record["rating"]
            )
            entry = make_entry(stats)
            path = self.path_for(when)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._stats[record["variant_id"]] = stats
            self._seen[record["feedback_id"]] = entry
            return entry, False, path
