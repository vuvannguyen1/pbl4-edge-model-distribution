"""Trạng thái phân phối trong RAM: request_id, tiến độ chunk, variant thiết bị đã có.

Trạng thái mất khi khởi động lại server (Version 1 không có database).
Mọi thao tác đều khóa bằng threading.Lock vì handler có thể chạy song song.
"""

from __future__ import annotations

import itertools
import statistics
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from urllib.parse import urlsplit

from server.schemas import PlanItem


def url_origin(url: str) -> str:
    """`http://10.0.0.2:9000/chunks/x/0` -> `http://10.0.0.2:9000`."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    # Chỉ để dashboard quan sát (/admin-api); không ảnh hưởng luồng phân phối.
    source: str = "client"  # "web": lượt "xin model thử" từ dashboard, không tải file
    requested_bandwidth_kbps: float | None = None
    bandwidth_kbps: float | None = None  # băng thông server dùng để chọn
    deadline_s: float | None = None
    estimated_download_s: float | None = None
    decision_log: list[str] = field(default_factory=list)
    updated_at: str = ""
    completed_at: str | None = None
    server_bandwidth_kbps: float | None = None  # client đo được khi tải từ server (/api/have)
    bad_chunks: set[int] = field(default_factory=set)
    mode: str | None = None  # "live" | "photo" | None
    budget_ms: float | None = None  # ngân sách suy luận Selector đã dùng
    runtime_family: str | None = None
    # số đo client gửi qua /api/feedback (dashboard: FPS, miếng nhận từ peer)
    metrics: dict | None = None


# Số lần đo gần nhất giữ lại để tính tốc độ một lớp thiết bị (ms cho mỗi GFLOP)
SPEED_SAMPLES = 20


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
        # device_class -> các lần đo ms/GFLOP gần nhất (từ /api/feedback)
        self._class_speed: dict[str, deque[float]] = {}

    # ------------------------------------------------------------ request

    def reserve_request_id(self) -> str:
        """Cấp request_id cho lượt xin bị từ chối, chỉ để ghi log (không lưu request)."""
        with self._lock:
            return f"r-{next(self._counter):04d}"

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
        source: str = "client",
        requested_bandwidth_kbps: float | None = None,
        bandwidth_kbps: float | None = None,
        deadline_s: float | None = None,
        estimated_download_s: float | None = None,
        decision_log: Iterable[str] = (),
        mode: str | None = None,
        budget_ms: float | None = None,
        runtime_family: str | None = None,
    ) -> DownloadRequest:
        """Tạo request mới. `complete=True` khi thiết bị đã có đủ variant (plan rỗng)."""
        with self._lock:
            request_id = f"r-{next(self._counter):04d}"
            now = utc_now()
            record = DownloadRequest(
                request_id=request_id,
                device_id=device_id,
                variant_id=variant_id,
                chunk_count=chunk_count,
                plan=list(plan),
                selector_version=selector_version,
                reason=reason,
                created_at=now,
                received=set(range(chunk_count)) if complete else set(),
                complete=complete,
                source=source,
                requested_bandwidth_kbps=requested_bandwidth_kbps,
                bandwidth_kbps=bandwidth_kbps,
                deadline_s=deadline_s,
                estimated_download_s=estimated_download_s,
                decision_log=list(decision_log),
                updated_at=now,
                completed_at=now if complete else None,
                mode=mode,
                budget_ms=budget_ms,
                runtime_family=runtime_family,
            )
            self._requests[request_id] = record
            return record

    def get_request(self, request_id: str) -> DownloadRequest | None:
        with self._lock:
            return self._requests.get(request_id)

    def add_chunks(
        self,
        request_id: str,
        chunks: list[int],
        bad_chunks: Iterable[int] = (),
        server_bandwidth_kbps: float | None = None,
    ) -> tuple[int, int]:
        """Gộp chunk mới vào tiến độ. Trả (số chunk đã có, tổng số chunk)."""
        with self._lock:
            record = self._requests[request_id]
            record.received.update(chunks)
            # miếng đã nhận đúng sau khi thử lại thì không còn tính là hỏng
            record.bad_chunks.update(bad_chunks)
            record.bad_chunks.difference_update(record.received)
            if server_bandwidth_kbps:
                record.server_bandwidth_kbps = server_bandwidth_kbps
            record.updated_at = utc_now()
            return len(record.received), record.chunk_count

    def mark_complete(self, request_id: str) -> bool:
        """Ghi nhận hoàn tất nếu server đã thấy đủ mọi chunk. Trả True nếu ghi nhận."""
        with self._lock:
            record = self._requests[request_id]
            if len(record.received) < record.chunk_count:
                return False
            record.complete = True
            record.completed_at = record.completed_at or utc_now()
            self._complete.add((record.device_id, record.variant_id))
            return True

    def declare_complete(self, device_id: str, variant_id: str) -> None:
        """Thiết bị khai báo đã có đủ variant (/api/announce, SHA-256 đã khớp Registry)."""
        with self._lock:
            self._complete.add((device_id, variant_id))

    def set_metrics(self, request_id: str, metrics: dict) -> None:
        with self._lock:
            record = self._requests.get(request_id)
            if record is not None:
                record.metrics = dict(metrics)

    # ------------------------------------------------------------ snapshot cho dashboard

    def requests_snapshot(self) -> list[DownloadRequest]:
        """Bản sao các request (cũ trước); sửa bản sao không ảnh hưởng trạng thái thật."""
        with self._lock:
            return [
                replace(
                    r,
                    plan=list(r.plan),
                    received=set(r.received),
                    decision_log=list(r.decision_log),
                    bad_chunks=set(r.bad_chunks),
                )
                for r in self._requests.values()
            ]

    def complete_pairs(self) -> list[tuple[str, str]]:
        """Các cặp (device_id, variant_id) đã báo complete và đủ chunk."""
        with self._lock:
            return sorted(self._complete)

    def banned_origins(self) -> list[str]:
        with self._lock:
            return sorted(self._bad_origins)

    def class_variants(self) -> dict[str, str]:
        """device_class -> variant được ghi nhớ từ quyết định switch của feedback."""
        with self._lock:
            return dict(self._class_variant)

    # ------------------------------------------------------------ peer / have

    def has_unfinished(self, variant_id: str) -> bool:
        """Có lượt tải variant này chưa xong (không tính lượt "xin thử" từ dashboard)."""
        with self._lock:
            return any(
                r.variant_id == variant_id and not r.complete and r.source != "web"
                for r in self._requests.values()
            )

    def has_complete(self, device_id: str, variant_id: str) -> bool:
        with self._lock:
            return (device_id, variant_id) in self._complete

    def devices_with_complete(self, variant_id: str) -> list[str]:
        with self._lock:
            return sorted(d for d, v in self._complete if v == variant_id)

    def ban_origin(self, origin: str) -> bool:
        """Đánh dấu peer gửi sai. Trả True nếu đây là lần đầu bị đánh dấu."""
        with self._lock:
            if origin in self._bad_origins:
                return False
            self._bad_origins.add(origin)
            return True

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

    def record_speed(self, device_class: str, ms_per_gflop: float) -> None:
        with self._lock:
            samples = self._class_speed.setdefault(device_class, deque(maxlen=SPEED_SAMPLES))
            samples.append(ms_per_gflop)

    def class_speed(self, device_class: str) -> float | None:
        """Trung vị ms/GFLOP đo được của lớp thiết bị; None nếu chưa có số đo."""
        with self._lock:
            samples = self._class_speed.get(device_class)
            return statistics.median(samples) if samples else None

    def class_speeds(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {
                cls: {"ms_per_gflop": statistics.median(v), "samples": len(v)}
                for cls, v in self._class_speed.items()
                if v
            }
