"""Downloader: kiểm hash từng miếng, bad_chunks, fallback, tải tiếp, switch.

Dùng server HTTP nhỏ của thư viện chuẩn để giả lập nguồn trả byte đúng/sai.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from validate_examples import schema_errors

from client.downloader import ChunkDownloader, DownloadError, SwitchRequested, variant_paths

CHUNK = 1024 * 1024


class ByteServer:
    """GET <path> -> bytes theo bảng `routes`; path không có -> 404."""

    def __init__(self) -> None:
        self.routes: dict[str, bytes] = {}
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.hits.append(self.path)
                data = outer.routes.get(self.path)
                if data is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args: Any) -> None:
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def byte_servers() -> Iterator[Callable[[], ByteServer]]:
    created: list[ByteServer] = []

    def make() -> ByteServer:
        server = ByteServer()
        created.append(server)
        return server

    yield make
    for server in created:
        server.close()


class FakeApi:
    """Thay ApiClient: ghi lại payload /api/have, kiểm schema từng gói."""

    def __init__(self, switch_after: int | None = None) -> None:
        self.haves: list[dict[str, Any]] = []
        self.switch_after = switch_after

    def have(self, payload: dict[str, Any]) -> dict[str, Any]:
        assert schema_errors("HaveRequest", payload) == []
        self.haves.append(payload)
        if self.switch_after is not None and len(self.haves) >= self.switch_after:
            return {
                "ok": True,
                "action": "switch",
                "new_variant_id": "smaller_int8",
                "reason": "băng thông tụt",
            }
        return {"ok": True, "action": "continue", "new_variant_id": None, "reason": None}


def make_variant(size: int = 3 * CHUNK + 1234) -> tuple[dict[str, Any], bytes]:
    data = os.urandom(size)
    variant = {
        "id": "fake_fp32",
        "file": "Fake_FP32.onnx",
        "size_bytes": size,
        "sha256": hashlib.sha256(data).hexdigest(),
        "chunk_size": CHUNK,
        "chunk_count": (size + CHUNK - 1) // CHUNK,
    }
    return variant, data


def make_plan(
    variant: dict[str, Any],
    data: bytes,
    server: ByteServer,
    peer: ByteServer | None = None,
    peer_indexes: set[int] = frozenset(),
    peer_url: str | None = None,
) -> list[dict[str, Any]]:
    plan = []
    for i in range(variant["chunk_count"]):
        piece = data[i * CHUNK : (i + 1) * CHUNK]
        server_url = f"{server.url}/api/chunks/{variant['id']}/{i}"
        server.routes[f"/api/chunks/{variant['id']}/{i}"] = piece
        if i in peer_indexes:
            base = peer_url or (peer.url if peer else "http://127.0.0.1:9")
            plan.append(
                {
                    "index": i,
                    "offset": i * CHUNK,
                    "size_bytes": len(piece),
                    "sha256": hashlib.sha256(piece).hexdigest(),
                    "source": "peer",
                    "url": f"{base}/chunks/{variant['id']}/{i}",
                    "fallback_url": server_url,
                }
            )
        else:
            plan.append(
                {
                    "index": i,
                    "offset": i * CHUNK,
                    "size_bytes": len(piece),
                    "sha256": hashlib.sha256(piece).hexdigest(),
                    "source": "server",
                    "url": server_url,
                    "fallback_url": None,
                }
            )
    return plan


def downloader(api: FakeApi, **kw: Any) -> ChunkDownloader:
    return ChunkDownloader(api, "laptopB", timeout_s=3, log=lambda _: None, **kw)


def test_download_from_server_verifies_and_reports(tmp_path: Path, byte_servers) -> None:
    server = byte_servers()
    variant, data = make_variant(5 * CHUNK + 77)
    plan = make_plan(variant, data, server)
    api = FakeApi()
    result = downloader(api).download("r-0001", variant, plan, tmp_path)

    assert result.path.read_bytes() == data
    assert not variant_paths(tmp_path, variant)[1].exists(), "file .part đã được đổi tên"
    assert result.chunks_from_server == 6 and result.chunks_from_peer == 0
    reported = sorted(i for h in api.haves for i in h["chunks"])
    assert reported == list(range(6))
    assert api.haves[-1]["complete"] is True
    assert all(not h["complete"] for h in api.haves[:-1])
    assert len(api.haves) >= 2, "báo định kỳ mỗi 4 miếng"
    assert all(h["bandwidth_kbps"] is None or h["bandwidth_kbps"] > 0 for h in api.haves)


def test_bad_peer_bytes_go_to_bad_chunks_and_fallback(tmp_path: Path, byte_servers) -> None:
    server, peer = byte_servers(), byte_servers()
    variant, data = make_variant()
    plan = make_plan(variant, data, server, peer, peer_indexes={0, 1})
    peer.routes[f"/chunks/{variant['id']}/0"] = b"x" * CHUNK  # sai hash
    peer.routes[f"/chunks/{variant['id']}/1"] = data[CHUNK : 2 * CHUNK]  # đúng
    api = FakeApi()
    result = downloader(api).download("r-0002", variant, plan, tmp_path)

    assert result.path.read_bytes() == data
    bad_reported = [b for h in api.haves for b in h["bad_chunks"]]
    assert bad_reported == [{"index": 0, "source_url": plan[0]["url"]}]
    assert result.chunks_from_peer == 1
    assert result.chunks_from_server == 3  # miếng 0 tải lại từ server + 2 miếng server
    assert f"/api/chunks/{variant['id']}/0" in server.hits


def test_dead_peer_falls_back_to_server(tmp_path: Path, byte_servers) -> None:
    server = byte_servers()
    variant, data = make_variant(2 * CHUNK)
    plan = make_plan(variant, data, server, peer_indexes={0, 1}, peer_url="http://127.0.0.1:9")
    api = FakeApi()
    result = downloader(api).download("r-0003", variant, plan, tmp_path)
    assert result.path.read_bytes() == data
    assert result.chunks_from_server == 2 and result.chunks_from_peer == 0
    assert {b["index"] for h in api.haves for b in h["bad_chunks"]} == {0, 1}


def test_peer_404_falls_back(tmp_path: Path, byte_servers) -> None:
    server, peer = byte_servers(), byte_servers()  # peer không có route nào -> 404
    variant, data = make_variant(CHUNK + 10)
    plan = make_plan(variant, data, server, peer, peer_indexes={0})
    result = downloader(FakeApi()).download("r-0004", variant, plan, tmp_path)
    assert result.path.read_bytes() == data
    assert result.chunks_from_server == 2


def test_server_always_wrong_gives_error_and_no_complete(tmp_path: Path, byte_servers) -> None:
    server = byte_servers()
    variant, data = make_variant(2 * CHUNK)
    plan = make_plan(variant, data, server)
    server.routes[f"/api/chunks/{variant['id']}/1"] = b"\0" * CHUNK
    api = FakeApi()
    with pytest.raises(DownloadError):
        downloader(api).download("r-0005", variant, plan, tmp_path)
    final, _ = variant_paths(tmp_path, variant)
    assert not final.exists()
    assert not any(h["complete"] for h in api.haves)
    assert server.hits.count(f"/api/chunks/{variant['id']}/1") == 3, "tối đa 3 lần"


def test_whole_file_hash_mismatch_is_not_complete(tmp_path: Path, byte_servers) -> None:
    server = byte_servers()
    variant, data = make_variant(2 * CHUNK)
    plan = make_plan(variant, data, server)
    variant["sha256"] = "0" * 64  # miếng đúng nhưng hash toàn file sai
    api = FakeApi()
    with pytest.raises(DownloadError):
        downloader(api).download("r-0006", variant, plan, tmp_path)
    assert not variant_paths(tmp_path, variant)[0].exists()
    assert not any(h["complete"] for h in api.haves)


def test_resume_reuses_valid_chunks_in_part_file(tmp_path: Path, byte_servers) -> None:
    server = byte_servers()
    variant, data = make_variant(4 * CHUNK)
    plan = make_plan(variant, data, server)
    final, part = variant_paths(tmp_path, variant)
    part.parent.mkdir(parents=True)
    partial = bytearray(len(data))
    partial[: 2 * CHUNK] = data[: 2 * CHUNK]  # đã có miếng 0, 1
    part.write_bytes(bytes(partial))
    api = FakeApi()
    result = downloader(api).download("r-0007", variant, plan, tmp_path)
    assert final.read_bytes() == data
    assert result.chunks_reused == 2 and result.chunks_from_server == 2
    assert f"/api/chunks/{variant['id']}/0" not in server.hits
    assert sorted(i for h in api.haves for i in h["chunks"]) == [0, 1, 2, 3]


def test_switch_stops_download(tmp_path: Path, byte_servers) -> None:
    server = byte_servers()
    variant, data = make_variant(9 * CHUNK)
    plan = make_plan(variant, data, server)
    with pytest.raises(SwitchRequested) as info:
        downloader(FakeApi(switch_after=1)).download("r-0008", variant, plan, tmp_path)
    assert info.value.new_variant_id == "smaller_int8"
    assert not variant_paths(tmp_path, variant)[0].exists()


def test_at_most_four_parallel(tmp_path: Path, byte_servers) -> None:
    assert downloader(FakeApi(), max_workers=10).max_workers == 4
