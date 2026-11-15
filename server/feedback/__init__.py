"""Ba luồng feedback của server.

- `metrics`: số đo tự động của agent (`POST /api/feedback` không có `feedback_id`), quyết định
  keep/switch cho cả lớp thiết bị.
- `manual`: đánh giá thủ công của người dùng (`POST /api/feedback` có `feedback_id`), qua
  `evaluate_manual`, chống ghi trùng theo `feedback_id`.
- `text`: nhận xét dạng chữ (`POST /api/user-feedback`), chấm bằng PhoBERT hoặc từ khoá.

Các tên dưới đây import được từ `server.feedback` như trước khi tách.
"""

from server.feedback.manual import (
    COMMENT_WEIGHT,
    GOOD_SCORE,
    LOW_SCORE,
    REVIEW_MIN_COUNT,
    STAR_WEIGHT,
    ManualFeedbackStore,
    VariantStats,
    evaluate_manual,
    final_score,
)
from server.feedback.metrics import (
    UPGRADE_HEADROOM,
    FeedbackDecision,
    FeedbackStore,
    decide,
)

__all__ = [
    "COMMENT_WEIGHT",
    "GOOD_SCORE",
    "LOW_SCORE",
    "REVIEW_MIN_COUNT",
    "STAR_WEIGHT",
    "UPGRADE_HEADROOM",
    "FeedbackDecision",
    "FeedbackStore",
    "ManualFeedbackStore",
    "VariantStats",
    "decide",
    "evaluate_manual",
    "final_score",
]
