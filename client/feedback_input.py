"""Kiểm giá trị người dùng nhập cho một đánh giá (điểm, đúng/sai, nhãn, nhận xét).

Dùng chung cho `client.feedback_cli` (terminal) và `client.web_ui` (giao diện web).
"""

from __future__ import annotations

import re
from collections.abc import Callable

MAX_LABEL = 200
MAX_COMMENT = 1000
VARIANT_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
_YES = {"y", "yes", "c", "co", "có", "d", "dung", "đúng", "1", "true"}
_NO = {"n", "no", "k", "khong", "không", "s", "sai", "0", "false"}


class FeedbackInputError(Exception):
    """Thiếu thông tin hoặc giá trị nhập không hợp lệ."""


def parse_rating(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise FeedbackInputError("Điểm phải là số nguyên từ 1 đến 5.") from None
    if not 1 <= value <= 5:
        raise FeedbackInputError("Điểm phải từ 1 đến 5.")
    return value


def parse_yes_no(text: str) -> bool:
    value = text.strip().lower()
    if value in _YES:
        return True
    if value in _NO:
        return False
    raise FeedbackInputError("Trả lời y (có) hoặc n (không).")


def check_text(limit: int) -> Callable[[str], str | None]:
    def parse(text: str) -> str | None:
        value = text.strip()
        if len(value) > limit:
            raise FeedbackInputError(f"Tối đa {limit} ký tự (đang có {len(value)}).")
        return value or None

    return parse
