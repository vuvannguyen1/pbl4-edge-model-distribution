"""Đọc file log của server (`server-YYYYMMDD.log`, định dạng của `server/logging_setup.py`)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

_LOG_LINE = re.compile(r"^(\d{2}:\d{2}:\d{2}) (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$")
_REQUEST_ID = re.compile(r"\[(r-\d+)\]")


def parse_log_lines(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Gom dòng log `HH:MM:SS LEVEL nội dung`; dòng tiếp theo (traceback) gộp vào dòng trước."""
    current: dict[str, Any] | None = None
    for line in lines:
        match = _LOG_LINE.match(line)
        if match:
            if current is not None:
                yield current
            time_text, level, message = match.groups()
            rid = _REQUEST_ID.search(message)
            current = {
                "time": time_text,
                "level": level,
                "message": message,
                "request_id": rid.group(1) if rid else None,
            }
        elif current is not None and line.strip():
            current["message"] += "\n" + line
    if current is not None:
        yield current
