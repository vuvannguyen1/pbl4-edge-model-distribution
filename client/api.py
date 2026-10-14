"""Gọi REST API của server và dựng các gói JSON theo docs/protocol.md.

Các hàm `build_*` là nơi duy nhất tạo payload client gửi lên, để test có thể
kiểm mọi gói theo docs/protocol.schema.json.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

PROTOCOL_VERSION = "1.0"
TASK = "image_classification"


class ApiError(Exception):
    """Server trả error envelope, hoặc không kết nối được."""

    def __init__(self, status: int | None, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------- payload


def build_register(
    *,
    device_id: str,
    platform: str,
    cpu_arch: str,
    ram_mb: int,
    runtime: str,
    peer_port: int | None,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "device_id": device_id,
        "platform": platform,
        "cpu_arch": cpu_arch,
        "ram_mb": int(ram_mb),
        "runtime": runtime,
        "peer_port": peer_port or None,
    }


def build_model_request(
    *,
    device_id: str,
    bandwidth_kbps: float | None,
    deadline_s: float,
    free_storage_mb: float | None,
    preferred_variant: str | None,
    have_variants: Iterable[str],
) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "task": TASK,
        "bandwidth_kbps": round(bandwidth_kbps, 1) if bandwidth_kbps else None,
        "deadline_s": deadline_s,
        "free_storage_mb": free_storage_mb,
        "preferred_variant": preferred_variant,
        "have_variants": sorted(set(have_variants)),
    }


def build_have(
    *,
    device_id: str,
    request_id: str,
    variant_id: str,
    chunks: Iterable[int],
    complete: bool,
    bandwidth_kbps: float | None,
    bad_chunks: Sequence[tuple[int, str]],
) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "request_id": request_id,
        "variant_id": variant_id,
        "chunks": sorted(set(int(i) for i in chunks)),
        "complete": bool(complete),
        "bandwidth_kbps": round(bandwidth_kbps, 1) if bandwidth_kbps else None,
        "bad_chunks": [{"index": int(i), "source_url": url} for i, url in bad_chunks],
    }


def build_feedback(
    *,
    device_id: str,
    request_id: str,
    variant_id: str,
    download_ms: float,
    chunks_from_peer: int,
    chunks_from_server: int,
    load_ms: float,
    latency_ms_median: float,
    runs: int,
    peak_ram_mb: float | None,
    top1_label: str | None,
    top1_confidence: float | None,
) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "request_id": request_id,
        "variant_id": variant_id,
        "download_ms": round(max(0.0, download_ms), 1),
        "chunks_from_peer": int(chunks_from_peer),
        "chunks_from_server": int(chunks_from_server),
        "load_ms": round(max(0.0, load_ms), 1),
        "latency_ms_median": round(max(0.0, latency_ms_median), 2),
        "runs": int(runs),
        "peak_ram_mb": round(peak_ram_mb, 1) if peak_ram_mb is not None else None,
        "top1_label": top1_label,
        "top1_confidence": (
            round(min(1.0, max(0.0, top1_confidence)), 4) if top1_confidence is not None else None
        ),
    }


# ---------------------------------------------------------------- HTTP


def new_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False  # mạng LAN/hotspot: không đi qua proxy của hệ thống
    return session


def normalize_server_url(url: str) -> str:
    """Bỏ "/" cuối và đổi `localhost` thành `127.0.0.1`.

    Trên Windows, `localhost` thử IPv6 (::1) trước; uvicorn `--host 0.0.0.0` chỉ
    nghe IPv4 nên mỗi kết nối mới bị trễ khoảng 2 giây.
    """
    parts = urlsplit(url.strip().rstrip("/"))
    if parts.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


class ApiClient:
    def __init__(self, server_url: str, timeout: float = 30.0) -> None:
        self.server_url = normalize_server_url(server_url)
        self.timeout = timeout
        self.session = new_session()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self.session.post(f"{self.server_url}{path}", json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ApiError(
                None, "CONNECTION", f"Không kết nối được {self.server_url}: {exc}"
            ) from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: requests.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code != 200:
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                err = body["error"]
                raise ApiError(resp.status_code, str(err.get("code")), str(err.get("message")))
            raise ApiError(resp.status_code, "HTTP", f"HTTP {resp.status_code}: {resp.text[:200]}")
        if not isinstance(body, dict):
            raise ApiError(resp.status_code, "HTTP", "Server trả nội dung không phải JSON object")
        return body

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/register", payload)

    def request_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/request", payload)

    def have(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/have", payload)

    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/api/feedback", payload)

    def probe(self, size_kb: int = 256) -> tuple[int, float]:
        """Tải /api/probe, trả (số byte, số giây). Băng thông = bytes × 8 / 1000 / giây."""
        url = f"{self.server_url}/api/probe?size_kb={size_kb}"
        start = time.perf_counter()
        try:
            with self.session.get(url, stream=True, timeout=self.timeout) as resp:
                if resp.status_code != 200:
                    self._parse(resp)
                total = sum(len(block) for block in resp.iter_content(64 * 1024))
        except requests.RequestException as exc:
            raise ApiError(None, "CONNECTION", f"Không đo được băng thông: {exc}") from exc
        return total, time.perf_counter() - start
