"""Ghi lịch sử vào CSDL từ luồng phân phối (`server/service.py`) theo kiểu "lỗi thì bỏ qua".

CSDL chỉ lưu lịch sử: lỗi ở đây không được chặn đăng ký, xin model hay feedback. Mỗi hàm
tự import `server.db` (như trước khi tách) và ghi lại đúng câu log cũ khi thất bại.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pbl4.server")


def record_device(**fields: Any) -> None:
    try:
        from server.db import get_session_factory, record_device as db_record_device

        with get_session_factory()() as db_session:
            db_record_device(session=db_session, **fields)
    except Exception as exc:
        logger.debug("Không thể ghi device vào CSDL: %s", exc)


def record_request(rid: str, **fields: Any) -> None:
    try:
        from server.db import get_session_factory, record_distribution_request as db_record_req

        with get_session_factory()() as db_session:
            db_record_req(session=db_session, request_id=rid, **fields)
    except Exception as exc:
        logger.debug("[%s] Không thể ghi request vào CSDL: %s", rid, exc)


def record_performance(rid: str, **fields: Any) -> None:
    try:
        from server.db import get_session_factory, record_performance_feedback as db_record_perf

        with get_session_factory()() as db_session:
            db_record_perf(session=db_session, request_id=rid, **fields)
    except Exception as exc:
        logger.warning("[%s] Không thể lưu feedback vào CSDL: %s", rid, exc)


def record_manual(feedback_id: str, **fields: Any) -> None:
    try:
        from server.db import get_session_factory, record_user_feedback as db_record_user

        with get_session_factory()() as db_session:
            db_record_user(session=db_session, feedback_id=feedback_id, **fields)
    except Exception as exc:
        logger.warning("[%s] Không thể lưu manual feedback vào CSDL: %s", feedback_id, exc)
