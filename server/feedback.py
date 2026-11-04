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
from server.selector import download_seconds, fmt_num, quality_text

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


# ---------------------------------------------------------------- đánh giá thủ công

# Điểm tổng hợp = sao × 0,2 + điểm PhoBERT của bình luận × 0,8, cùng thang 1..5.
# PhoBERT trả điểm rời rạc (nhãn 1..5). Không có bình luận thì điểm tổng hợp bằng số sao.
STAR_WEIGHT = 0.2
COMMENT_WEIGHT = 0.8
LOW_SCORE = 2.5  # từ đây trở xuống là đánh giá xấu
GOOD_SCORE = 3.5  # từ đây trở lên là đánh giá tốt
REVIEW_MIN_COUNT = 5  # đủ số lượt mới kết luận về cả variant


@dataclass(frozen=True)
class VariantStats:
    count: int = 0
    correct: int = 0
    rating_sum: int = 0
    judged: int = 0  # số lượt có trả lời đúng/sai (CLI vẫn hỏi, trang /app thì không)
    score_sum: float = 0.0

    def add(self, correct: bool | None, rating: int, score: float | None = None) -> VariantStats:
        return VariantStats(
            self.count + 1,
            self.correct + int(bool(correct)),
            self.rating_sum + rating,
            self.judged + int(correct is not None),
            self.score_sum + (rating if score is None else score),
        )

    @property
    def correct_rate(self) -> float | None:
        return self.correct / self.judged if self.judged else None

    @property
    def avg_rating(self) -> float:
        return self.rating_sum / self.count if self.count else 0.0

    @property
    def avg_score(self) -> float:
        return self.score_sum / self.count if self.count else 0.0


def final_score(rating: int, comment_score: int | None) -> float:
    """Điểm tổng hợp của một lượt đánh giá; `comment_score` None: không có bình luận."""
    if comment_score is None:
        return float(rating)
    return round(rating * STAR_WEIGHT + comment_score * COMMENT_WEIGHT, 2)


def evaluate_manual(
    *,
    variant_id: str,
    correct: bool | None,
    rating: int,
    stats: VariantStats,
    comment_score: int | None = None,
    comment_engine: str | None = None,
) -> dict[str, Any]:
    """Đánh giá một feedback và cả variant (`stats` đã gồm feedback này).

    verdict theo điểm tổng hợp: `negative` nếu điểm <= 2,5 (hoặc CLI trả lời dự đoán sai),
    `positive` nếu điểm >= 3,5, còn lại `neutral`. `needs_review` khi variant có >= 5 lượt mà
    điểm trung bình <= 2,5.
    """
    score = final_score(rating, comment_score)
    if correct is False or score <= LOW_SCORE:
        verdict = "negative"
    elif score >= GOOD_SCORE:
        verdict = "positive"
    else:
        verdict = "neutral"
    needs_review = stats.count >= REVIEW_MIN_COUNT and stats.avg_score <= LOW_SCORE
    summary = (
        f"{variant_id} có {stats.count} đánh giá, trung bình {stats.avg_rating:.1f} sao, "
        f"điểm {stats.avg_score:.1f}"
    )
    if needs_review:
        reason = (
            f"{summary}: điểm trung bình không quá {fmt_num(LOW_SCORE)} sau ít nhất "
            f"{REVIEW_MIN_COUNT} lượt, cần xem lại variant này."
        )
    else:
        reason = f"{summary}."
    if comment_score is not None:
        reason += f" Lượt này: {rating} sao, bình luận {comment_score}/5, điểm {fmt_num(score, 2)}."
    return {
        "verdict": verdict,
        "final_score": score,
        "comment_score": comment_score,
        "comment_engine": comment_engine,
        "needs_review": needs_review,
        "variant_feedback_count": stats.count,
        "variant_correct_rate": (
            None if stats.correct_rate is None else round(stats.correct_rate, 4)
        ),
        "variant_avg_rating": round(stats.avg_rating, 2),
        "variant_avg_score": round(stats.avg_score, 2),
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
                        entry.get("correct"),
                        int(entry["rating"]),
                        (entry.get("evaluation") or {}).get("final_score"),
                    )
                except (ValueError, KeyError, TypeError):
                    continue
                self._seen.setdefault(feedback_id, entry)

    def has(self, feedback_id: str) -> bool:
        with self._lock:
            return feedback_id in self._seen

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
                record["correct"], record["rating"], record.get("final_score")
            )
            entry = make_entry(stats)
            path = self.path_for(when)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._stats[record["variant_id"]] = stats
            self._seen[record["feedback_id"]] = entry
            return entry, False, path
