"""Đánh giá thủ công của người dùng (`client.feedback_cli`, trang /app): chấm điểm một lượt,
thống kê theo variant và chống ghi trùng theo `feedback_id`."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from server.textfmt import fmt_num

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
