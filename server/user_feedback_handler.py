"""Đường import cũ của `record_user_feedback`; mã nằm ở `server/feedback/text.py`.

`server/service.py` và route `/api/user-feedback` import hàm qua module này ngay lúc gọi,
nên thay thế `server.user_feedback_handler.record_user_feedback` (trong test) vẫn có tác dụng.
"""

from server.feedback.text import record_user_feedback

__all__ = ["record_user_feedback"]
