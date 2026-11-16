"""Ghi log của server ra console và file theo ngày (`<logs_dir>/server-YYYYMMDD.log`).

Định dạng dòng `HH:MM:SS LEVEL nội dung` được dashboard đọc lại (`server/admin/logs.py`,
`parse_log_lines`), nên không đổi định dạng này.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path


class DailyFileHandler(logging.Handler):
    """Ghi log vào <logs_dir>/server-YYYYMMDD.log, tự đổi file khi sang ngày mới."""

    def __init__(self, logs_dir: Path) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self._day = ""
        self._handler: logging.FileHandler | None = None

    def emit(self, record: logging.LogRecord) -> None:
        day = datetime.now().strftime("%Y%m%d")
        if day != self._day or self._handler is None:
            if self._handler is not None:
                self._handler.close()
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            self._handler = logging.FileHandler(
                self.logs_dir / f"server-{day}.log", encoding="utf-8"
            )
            self._handler.setFormatter(self.formatter)
            self._day = day
        self._handler.emit(record)

    def close(self) -> None:
        if self._handler is not None:
            self._handler.close()
        super().close()


def setup_logging(logs_dir: Path) -> None:
    """Log ra console và file theo ngày. Gọi lại nhiều lần không bị nhân đôi handler."""
    if os.name == "nt" and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    root = logging.getLogger("pbl4")
    for handler in list(root.handlers):
        if getattr(handler, "_pbl4", False):
            root.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    file_handler = DailyFileHandler(logs_dir)
    for handler in (console, file_handler):
        handler.setFormatter(formatter)
        handler._pbl4 = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False
