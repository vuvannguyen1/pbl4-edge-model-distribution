"""Theo dõi kết nối tới server khi client còn chạy (đang tải model, đang --serve).

Mỗi `interval_s` giây thử mở kết nối TCP tới host:port của server (không gửi HTTP
request nên không sinh log phía server, không tốn token giới hạn tốc độ). Chỉ in khi
trạng thái đổi: mất kết nối, rồi kết nối lại. Khi kết nối lại thì gọi `on_reconnect`
(agent dùng để gửi bù feedback còn `pending`).
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

Printer = Callable[[str], None]


def server_reachable(server_url: str, timeout_s: float = 2.0) -> bool:
    parts = urlsplit(server_url)
    if not parts.hostname:
        return False
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        with socket.create_connection((parts.hostname, port), timeout=timeout_s):
            return True
    except OSError:
        return False


class ServerMonitor:
    def __init__(
        self,
        server_url: str,
        out: Printer = print,
        interval_s: float = 3.0,
        check: Callable[[], bool] | None = None,
        on_reconnect: Callable[[], None] | None = None,
    ) -> None:
        self.server_url = server_url
        self.out = out
        self.interval_s = interval_s
        self.check = check or (lambda: server_reachable(server_url))
        self.on_reconnect = on_reconnect
        self.connected = True  # chỉ bật sau khi vừa gọi server thành công
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll(self) -> None:
        ok = self.check()
        if ok == self.connected:
            return
        self.connected = ok
        now = time.strftime("%H:%M:%S")
        if ok:
            self.out(f"  [{now}] Đã kết nối lại server {self.server_url}")
            if self.on_reconnect is not None:
                try:
                    self.on_reconnect()
                except Exception as exc:  # luồng nền: không được chết vì lỗi gửi bù
                    self.out(f"  [cảnh báo] Lỗi khi xử lý kết nối lại: {exc}")
        else:
            self.out(
                f"  [{now}] MẤT KẾT NỐI tới server {self.server_url}: server đã đóng hoặc mạng "
                "bị ngắt. Model đã tải vẫn dùng được offline: python -m client.detect"
            )

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            self.poll()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="server-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout=5)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None
