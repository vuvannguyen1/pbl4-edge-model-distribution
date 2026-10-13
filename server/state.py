"""Trạng thái phân phối trong RAM: request_id, tiến độ chunk, variant thiết bị đã có.

Trạng thái mất khi khởi động lại server (Version 1 không có database).
Mọi thao tác đều khóa bằng threading.Lock vì handler có thể chạy song song.
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from server.schemas import PlanItem


def url_origin(url: str) -> str:
    """`http://10.0.0.2:9000/chunks/x/0` -> `http://10.0.0.2:9000`."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@dataclass
class DownloadRequest:
    request_id: str
    device_id: str
    variant_id: str
    chunk_count: int
    plan: list[PlanItem]
    selector_version: str
    reason: str
    created_at: str
    received: set[int] = field(default_factory=set)
    complete: bool = False


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        self._requests: dict[str, DownloadRequest] = {}
        # (device_id, variant_id) đã gửi /api/have complete=true và đủ chunk
        self._complete: set[tuple[str, str]] = set()
        # origin peer từng gửi chunk sai -> không giao lại
        self._bad_origins: set[str] = set()
        # device_class -> variant được ghi nhớ từ feedback
        self._class_variant: dict[str, str] = {}

    # ------------------------------------------------------------ request

    def create_request(
        self,
        *,
        device_id: str,
        variant_id: str,
        chunk_count: int,
        selector_version: str,
        reason: str,
        plan: list[PlanItem],
        complete: bool = False,
    ) -> DownloadRequest:
        """Tạo request mới. `complete=True` khi thiết bị đã có đủ variant (plan rỗng)."""
        with self._lock:
            request_id = f"r-{next(self._counter):04d}"
            record = DownloadRequest(
                request_id=request_id,
                device_id=device_id,
                variant_id=variant_id,
                chunk_count=chunk_count,
                plan=list(plan),
                selector_version=selector_version,
                reason=reason,
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                received=set(range(chunk_count)) if complete else set(),
                complete=complete,
            )
            self._requests[request_id] = record
            return record

    def get_request(self, request_id: str) -> DownloadRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def add_chunks(self, request_id: str, chunks: list[int]) -> tuple[int, int]:
        """Gộp chunk mới vào tiến độ. Trả (số chunk đã có, tổng số chunk)."""
        with self._lock:
            record = self._requests[request_id]
            record.received.update(chunks)
            return len(record.received), record.chunk_count

    def mark_complete(self, request_id: str) -> bool:
        """Ghi nhận hoàn tất nếu server đã thấy đủ mọi chunk. Trả True nếu ghi nhận."""
        with self._lock:
            record = self._requests[request_id]
            if len(record.received) < record.chunk_count:
                return False
            record.complete = True
            self._complete.add((record.device_id, record.variant_id))
            return True

    # ------------------------------------------------------------ peer / have

    def has_complete(self, device_id: str, variant_id: str) -> bool:
        with self._lock:
            return (device_id, variant_id) in self._complete

    def devices_with_complete(self, variant_id: str) -> list[str]:
        with self._lock:
            return sorted(d for d, v in self._complete if v == variant_id)

    def ban_origin(self, origin: str) -> None:
        with self._lock:
            self._bad_origins.add(origin)

    def unban_origin(self, origin: str) -> None:
        with self._lock:
            self._bad_origins.discard(origin)

    def is_banned(self, origin: str) -> bool:
        with self._lock:
            return origin in self._bad_origins

    # ------------------------------------------------------------ feedback

    def remember_class_variant(self, device_class: str, variant_id: str) -> None:
        with self._lock:
            self._class_variant[device_class] = variant_id

    def class_variant(self, device_class: str) -> str | None:
        with self._lock:
            return self._class_variant.get(device_class)
