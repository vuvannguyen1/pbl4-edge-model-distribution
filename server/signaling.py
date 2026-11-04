"""Signaling cho P2P WebRTC giữa các trình duyệt (trang /app).

Trình duyệt không mở được cổng như client Python, nên hai trình duyệt cùng lớp thiết bị trao
miếng model qua WebRTC DataChannel. Server chỉ chuyển tiếp tin nhắn SDP (offer/answer) giữa
chúng, không đọc nội dung; miếng model đi thẳng giữa hai máy trong mạng LAN và vẫn được kiểm
SHA-256 như miếng tải từ server.

- `POST /api/signal`: gửi một `SignalMessage` cho thiết bị khác.
- `GET /api/signal/{device_id}?wait=20`: long-poll lấy tin nhắn đang chờ. Trang /app gọi liên tục,
  nên lần gọi gần nhất cũng cho biết trình duyệt còn mở trang (peer "online"). Trình duyệt đóng
  trang thì long-poll dừng ngay (kết nối bị ngắt) và trang gửi "bye" cho chính nó: hết online.

Dùng hàng đợi + khoá luồng (không dùng asyncio.Event) để chạy đúng cả trong TestClient lẫn
uvicorn; long-poll kiểm hàng đợi mỗi 0,2 giây.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

ONLINE_S = 12.0  # không gọi long-poll lâu hơn ngần này thì coi như đã đóng trang
MAX_QUEUE = 200
POLL_STEP_S = 0.2
MAX_WAIT_S = 25.0


class SignalHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, deque[dict[str, Any]]] = {}
        self._last_poll: dict[str, float] = {}
        self._waiting: dict[str, int] = {}
        self._closed: set[str] = set()  # đã báo đóng trang, chờ long-poll đang dở kết thúc
        self.relayed = 0  # số tin nhắn đã chuyển tiếp (dashboard)
        self.offers = 0  # số lần mở kết nối P2P WebRTC

    def post(self, message: dict[str, Any]) -> bool:
        """Xếp tin nhắn cho thiết bị nhận. Trả False nếu thiết bị nhận không online."""
        target = message["to_device"]
        if message.get("type") == "bye" and target == message.get("from_device"):
            self.mark_offline(target)  # trang tự báo đóng
            return True
        with self._lock:
            if not self._online_locked(target):
                return False
            queue = self._queues.setdefault(target, deque(maxlen=MAX_QUEUE))
            queue.append(message)
            self.relayed += 1
            if message.get("type") == "offer":
                self.offers += 1
            return True

    def _drain(self, device_id: str) -> list[dict[str, Any]]:
        with self._lock:
            queue = self._queues.get(device_id)
            if not queue:
                return []
            items = list(queue)
            queue.clear()
            return items

    def mark_offline(self, device_id: str) -> None:
        with self._lock:
            self._last_poll.pop(device_id, None)
            self._queues.pop(device_id, None)
            self._closed.add(device_id)

    async def wait(
        self,
        device_id: str,
        wait_s: float,
        disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> list[dict[str, Any]]:
        """Chờ tin nhắn tối đa `wait_s` giây; dừng sớm khi trình duyệt ngắt kết nối."""
        wait_s = max(0.0, min(wait_s, MAX_WAIT_S))
        with self._lock:
            self._closed.discard(device_id)
            self._last_poll[device_id] = time.monotonic()
            self._waiting[device_id] = self._waiting.get(device_id, 0) + 1
        dropped = False
        try:
            deadline = time.monotonic() + wait_s
            while True:
                items = self._drain(device_id)
                if items or time.monotonic() >= deadline:
                    return items
                if disconnected is not None and await disconnected():
                    dropped = True
                    return []
                await asyncio.sleep(POLL_STEP_S)
        finally:
            with self._lock:
                self._waiting[device_id] -= 1
                if not self._waiting[device_id]:
                    del self._waiting[device_id]
                if dropped or device_id in self._closed:
                    self._last_poll.pop(device_id, None)  # đóng trang giữa lúc chờ: hết online ngay
                else:
                    self._last_poll[device_id] = time.monotonic()

    def _online_locked(self, device_id: str) -> bool:
        if device_id in self._closed:
            return False
        if device_id in self._waiting:
            return True
        last = self._last_poll.get(device_id)
        return last is not None and time.monotonic() - last < ONLINE_S

    def is_online(self, device_id: str) -> bool:
        with self._lock:
            return self._online_locked(device_id)

    def online_devices(self) -> list[str]:
        with self._lock:
            return sorted(
                d for d in set(self._last_poll) | set(self._waiting) if self._online_locked(d)
            )
