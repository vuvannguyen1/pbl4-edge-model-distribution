"""Token bucket: tải N byte ở giới hạn kbps mất khoảng N×8/1000/kbps giây."""

from __future__ import annotations

import asyncio
import threading
import time

from fastapi.testclient import TestClient

from server.ratelimit import TokenBucket

BLOCK = 16 * 1024


def _consume(bucket: TokenBucket, total: int) -> None:
    remaining = total
    while remaining > 0:
        n = min(BLOCK, remaining)
        bucket.acquire_sync(n)
        remaining -= n


def test_unlimited_does_not_wait() -> None:
    bucket = TokenBucket(0)
    start = time.perf_counter()
    _consume(bucket, 50 * 1024 * 1024)
    assert time.perf_counter() - start < 0.5


def test_single_consumer_rate() -> None:
    kbps = 2000  # 250 000 byte/s
    total = 200_000
    expected = total * 8 / 1000 / kbps  # 0.8 s
    bucket = TokenBucket(kbps)
    start = time.perf_counter()
    _consume(bucket, total)
    elapsed = time.perf_counter() - start
    assert expected * 0.85 <= elapsed <= expected * 1.35


def test_shared_between_concurrent_consumers() -> None:
    """Hai luồng dùng chung bucket: tổng thời gian theo tổng số byte."""
    kbps = 2000
    per_thread = 100_000
    expected = 2 * per_thread * 8 / 1000 / kbps  # 0.8 s
    bucket = TokenBucket(kbps)
    threads = [threading.Thread(target=_consume, args=(bucket, per_thread)) for _ in range(2)]
    start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - start
    assert expected * 0.85 <= elapsed <= expected * 1.35


def test_async_acquire_rate() -> None:
    kbps = 4000  # 500 000 byte/s
    total = 250_000
    expected = total * 8 / 1000 / kbps  # 0.5 s
    bucket = TokenBucket(kbps)

    async def run() -> float:
        start = time.perf_counter()
        remaining = total
        while remaining > 0:
            n = min(BLOCK, remaining)
            await bucket.acquire(n)
            remaining -= n
        return time.perf_counter() - start

    elapsed = asyncio.run(run())
    assert expected * 0.85 <= elapsed <= expected * 1.35


def test_probe_endpoint_is_rate_limited(app_factory) -> None:
    kbps = 1000
    size_kb = 64
    expected = size_kb * 1024 * 8 / 1000 / kbps  # 0.52 s
    with TestClient(app_factory(rate_limit_kbps=kbps)) as client:
        start = time.perf_counter()
        resp = client.get(f"/api/probe?size_kb={size_kb}")
        elapsed = time.perf_counter() - start
    assert resp.status_code == 200
    assert len(resp.content) == size_kb * 1024
    assert expected * 0.85 <= elapsed <= expected * 1.6
