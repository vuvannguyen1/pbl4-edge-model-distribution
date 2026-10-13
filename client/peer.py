"""Peer service: phục vụ miếng model cho máy khác ở `peer_port`.

Route duy nhất: `GET /chunks/{variant_id}/{index}`.
- Chỉ phục vụ variant đã tải đủ **và** đã kiểm SHA-256 toàn file (kho cục bộ
  khóa theo `variant_id`); file đang tải dở không bao giờ được chia sẻ.
- Chưa có variant hoặc index ngoài phạm vi: 404.
- Đọc đúng đoạn byte tại offset, không nạp cả file. Miếng giữa các peer không
  giới hạn tốc độ (protocol mục 4).
Dùng `http.server.ThreadingHTTPServer` của thư viện chuẩn, chạy trong thread nền.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from client.downloader import verified_variants

_ROUTE = re.compile(r"^/chunks/([a-z0-9_]{1,64})/(\d+)$")


@dataclass(frozen=True)
class SharedVariant:
    variant_id: str
    path: Path
    size_bytes: int
    chunk_size: int
    chunk_count: int

    def chunk_range(self, index: int) -> tuple[int, int] | None:
        if not 0 <= index < self.chunk_count:
            return None
        offset = index * self.chunk_size
        return offset, min(self.chunk_size, self.size_bytes - offset)


class PeerService:
    def __init__(
        self,
        cache_dir: Path,
        port: int,
        host: str = "0.0.0.0",
        log: Callable[[str], None] = print,
    ) -> None:
        self.cache_dir = cache_dir
        self.port = port
        self.host = host
        self.log = log
        self._lock = threading.Lock()
        self._shared: dict[str, SharedVariant] = {}
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.served = 0

    # ------------------------------------------------------------ kho chia sẻ

    def share(self, variant: dict[str, Any], path: Path) -> None:
        """Thêm variant vào kho chia sẻ. Chỉ gọi sau khi hash toàn file đã khớp."""
        shared = SharedVariant(
            variant_id=variant["id"],
            path=path,
            size_bytes=int(variant["size_bytes"]),
            chunk_size=int(variant["chunk_size"]),
            chunk_count=int(variant["chunk_count"]),
        )
        with self._lock:
            self._shared[shared.variant_id] = shared

    def refresh(self) -> list[str]:
        """Nạp lại kho từ cache_dir (chỉ variant có hash toàn file khớp)."""
        found = verified_variants(self.cache_dir)
        for variant, path in found.values():
            self.share(variant, path)
        return self.shared_ids()

    def shared_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._shared)

    def lookup(self, variant_id: str) -> SharedVariant | None:
        with self._lock:
            return self._shared.get(variant_id)

    def read_chunk(self, variant_id: str, index: int) -> bytes | None:
        shared = self.lookup(variant_id)
        if shared is None:
            return None
        span = shared.chunk_range(index)
        if span is None:
            return None
        offset, size = span
        with shared.path.open("rb") as f:
            f.seek(offset)
            data = f.read(size)
        return data if len(data) == size else None

    # ------------------------------------------------------------ HTTP

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        service = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                match = _ROUTE.match(self.path)
                data = None
                if match:
                    try:
                        data = service.read_chunk(match.group(1), int(match.group(2)))
                    except OSError as exc:
                        service.log(f"  [peer] lỗi đọc file: {exc}")
                if data is None:
                    body = b"not found"
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                with service._lock:
                    service.served += 1
                service.log(
                    f"  [peer] gửi miếng {match.group(2)} của {match.group(1)} "
                    f"({len(data):,} byte) cho {self.client_address[0]}"
                )

            def log_message(self, *args: Any) -> None:
                pass

        return Handler

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer((self.host, self.port), self._handler())
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="peer-service", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is None:
            return
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._httpd = None
        self._thread = None

    @property
    def running(self) -> bool:
        return self._httpd is not None
