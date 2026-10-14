"""Token bucket dùng chung cho mọi kết nối tải chunk và probe.

Bucket tính theo byte, tốc độ nạp = rate_kbps × 1000 / 8 byte/giây
(1 kbps = 1000 bit/s theo protocol). Mỗi lần gửi một khối, kết nối "đặt chỗ"
số byte của khối đó; nếu bucket thiếu token thì token âm (nợ) và kết nối phải
chờ đến khi nợ được trả. Nhờ vậy tổng tốc độ của mọi kết nối cộng lại không
vượt giới hạn, kể cả khi nhiều kết nối chạy song song.

`burst_s` giới hạn lượng token tích lũy lúc rảnh, tính bằng giây truyền ở tốc độ
giới hạn. Giá trị nhỏ (20 ms) đủ bù việc `sleep` ngủ quá giờ (đồng hồ Windows
chỉ chính xác khoảng 15,6 ms) nhưng không làm sai kết quả đo của /api/probe.
Bucket bắt đầu với 0 token.
"""

from __future__ import annotations

import asyncio
import threading
import time


class TokenBucket:
    def __init__(self, rate_kbps: float, burst_s: float = 0.02) -> None:
        if rate_kbps < 0 or burst_s < 0:
            raise ValueError("rate_kbps và burst_s phải >= 0")
        self.rate_kbps = rate_kbps
        self.capacity = self.bytes_per_second * burst_s
        self._lock = threading.Lock()  # dùng threading.Lock: an toàn với mọi event loop/luồng
        self._tokens = 0.0
        self._last = time.monotonic()

    @property
    def unlimited(self) -> bool:
        return self.rate_kbps == 0

    @property
    def bytes_per_second(self) -> float:
        return self.rate_kbps * 1000 / 8

    def reserve(self, nbytes: int) -> float:
        """Lấy `nbytes` token, trả số giây cần chờ trước khi được gửi."""
        if self.unlimited or nbytes <= 0:
            return 0.0
        rate = self.bytes_per_second
        with self._lock:
            now = time.monotonic()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * rate)
            self._last = now
            self._tokens -= nbytes
            return 0.0 if self._tokens >= 0 else -self._tokens / rate

    async def acquire(self, nbytes: int) -> None:
        wait = self.reserve(nbytes)
        if wait > 0:
            await asyncio.sleep(wait)

    def acquire_sync(self, nbytes: int) -> None:
        wait = self.reserve(nbytes)
        if wait > 0:
            time.sleep(wait)
