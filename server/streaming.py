"""Stream byte cho /api/chunks và /api/probe theo từng khối nhỏ, qua token bucket."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from server.ratelimit import TokenBucket

logger = logging.getLogger("pbl4.server")

# Khối nhỏ để khi giới hạn tốc độ thấp và 4 kết nối song song, mỗi kết nối vẫn nhận
# byte đều đặn (client chờ tối đa 10 s không có byte nào).
STREAM_BLOCK_BYTES = 16 * 1024


async def stream_file_range(
    path: Path, offset: int, size: int, bucket: TokenBucket
) -> AsyncIterator[bytes]:
    """Đọc đúng `size` byte tại `offset` theo từng khối nhỏ; không nạp cả file."""
    with path.open("rb") as f:
        f.seek(offset)
        remaining = size
        while remaining > 0:
            n = min(STREAM_BLOCK_BYTES, remaining)
            await bucket.acquire(n)
            data = f.read(n)
            if not data:
                logger.error("File %s ngắn hơn dự kiến khi stream", path.name)
                return
            remaining -= len(data)
            yield data


async def stream_random(size: int, bucket: TokenBucket) -> AsyncIterator[bytes]:
    remaining = size
    while remaining > 0:
        n = min(STREAM_BLOCK_BYTES, remaining)
        await bucket.acquire(n)
        remaining -= n
        yield os.urandom(n)
