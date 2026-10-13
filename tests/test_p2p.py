"""P2P: hai client cùng máy (cùng device_class), khác device_id và peer_port.

- Client B nhận ít nhất một miếng có source "peer" từ client A.
- Tắt peer A: client sau vẫn hoàn tất nhờ fallback về server.
"""

from __future__ import annotations

import hashlib
import socket
from pathlib import Path

import requests
from conftest import fake_entry, make_test_image, requires_models
from fastapi.testclient import TestClient
from validate_examples import schema_errors

from client.agent import Agent, AgentConfig
from client.downloader import sha256_file
from client.peer import PeerService


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_agent(server_url: str, device_id: str, tmp_path: Path, image: Path, **kw) -> Agent:
    config = AgentConfig(
        server=server_url,
        device_id=device_id,
        image=image,
        bandwidth_kbps=600,
        peer_port=kw.pop("peer_port", free_port()),
        cache_dir=tmp_path / f"cache_{device_id}",
        runs=2,
        **kw,
    )
    return Agent(config, out=lambda _: None)


@requires_models
def test_client_b_gets_chunks_from_peer_a_then_fallback(tmp_path: Path, live_server) -> None:
    server = live_server()
    image = make_test_image(tmp_path / "anh.jpg")

    agent_a = make_agent(server.url, "laptopA", tmp_path, image)
    agent_b = make_agent(server.url, "laptopB", tmp_path, image)
    agent_c = make_agent(server.url, "laptopC", tmp_path, image, peer_port=0)
    try:
        result_a = agent_a.run()
        assert result_a.download.chunks_from_peer == 0
        assert agent_a.peer_service is not None and agent_a.peer_service.running

        result_b = agent_b.run()
        assert result_b.variant["id"] == result_a.variant["id"]
        assert result_b.download.chunks_from_peer >= 1
        assert "peer" in result_b.download.sources.values()
        assert sha256_file(result_b.model_path) == result_b.variant["sha256"]
        assert result_b.feedback_payload["chunks_from_peer"] >= 1
        assert agent_a.peer_service.served == result_b.download.chunks_from_peer
        assert "laptopA" in json_reason(server, result_b.request_id)

        # Tắt cả hai peer: C vẫn được giao miếng peer (server chưa biết) nhưng tải lại từ server
        agent_a.stop_peer_service()
        agent_b.stop_peer_service()
        result_c = agent_c.run()
        assert sha256_file(result_c.model_path) == result_c.variant["sha256"]
        assert result_c.download.chunks_from_peer == 0
        assert result_c.download.chunks_from_server == result_c.variant["chunk_count"]
        assert result_c.download.bad_chunks, "miếng peer lỗi phải được báo bad_chunks"
    finally:
        agent_a.stop_peer_service()
        agent_b.stop_peer_service()
        agent_c.stop_peer_service()


def json_reason(server, request_id: str) -> str:
    service = server.app.state.service
    return service.state.get_request(request_id).reason


# ---------------------------------------------------------------- planner phía server


def _register(client: TestClient, device_id: str, peer_port: int | None, **kw) -> None:
    body = {
        "protocol_version": "1.0",
        "device_id": device_id,
        "platform": "android",
        "cpu_arch": "aarch64",
        "ram_mb": 6144,
        "runtime": "onnxruntime 1.25.1",
        "peer_port": peer_port,
        **kw,
    }
    assert client.post("/api/register", json=body).status_code == 200


def _request(client: TestClient, device_id: str) -> dict:
    body = {
        "device_id": device_id,
        "task": "image_classification",
        "bandwidth_kbps": 600,
        "deadline_s": 60,
        "free_storage_mb": None,
        "preferred_variant": None,
        "have_variants": [],
    }
    resp = client.post("/api/request", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _complete(client: TestClient, device_id: str, response: dict) -> None:
    body = {
        "device_id": device_id,
        "request_id": response["request_id"],
        "variant_id": response["variant"]["id"],
        "chunks": list(range(response["variant"]["chunk_count"])),
        "complete": True,
        "bandwidth_kbps": 600,
        "bad_chunks": [],
    }
    assert client.post("/api/have", json=body).status_code == 200


def test_planner_assigns_first_half_to_verified_peer(client: TestClient) -> None:
    _register(client, "phoneA", 9000)
    _complete(client, "phoneA", _request(client, "phoneA"))
    _register(client, "phoneB", 9001)
    response = _request(client, "phoneB")
    assert schema_errors("ModelResponse", response) == []
    plan = response["plan"]
    n = len(plan)
    peer_items = [p for p in plan if p["source"] == "peer"]
    assert [p["index"] for p in peer_items] == list(range((n + 1) // 2))
    for item in peer_items:
        assert item["url"].startswith("http://testclient:9000/chunks/")
        assert item["fallback_url"].endswith(
            f"/api/chunks/{response['variant']['id']}/{item['index']}"
        )
    assert all(p["fallback_url"] is None for p in plan if p["source"] == "server")
    assert "phoneA" in response["reason"]


def test_planner_requires_same_class_peer_port_and_complete(client: TestClient) -> None:
    _register(client, "noPort", None)
    _complete(client, "noPort", _request(client, "noPort"))
    _register(client, "otherClass", 9002, ram_mb=2048)
    _complete(client, "otherClass", _request(client, "otherClass"))
    _register(client, "notDone", 9003)
    _request(client, "notDone")
    _register(client, "phoneB", 9001)
    assert all(p["source"] == "server" for p in _request(client, "phoneB")["plan"])


def test_planner_skips_peer_reported_bad(client: TestClient) -> None:
    _register(client, "phoneA", 9000)
    _complete(client, "phoneA", _request(client, "phoneA"))
    _register(client, "phoneB", 9001)
    response = _request(client, "phoneB")
    peer_item = next(p for p in response["plan"] if p["source"] == "peer")
    have = {
        "device_id": "phoneB",
        "request_id": response["request_id"],
        "variant_id": response["variant"]["id"],
        "chunks": [],
        "complete": False,
        "bandwidth_kbps": None,
        "bad_chunks": [{"index": peer_item["index"], "source_url": peer_item["url"]}],
    }
    assert client.post("/api/have", json=have).status_code == 200
    _register(client, "phoneC", 9004)
    assert all(p["source"] == "server" for p in _request(client, "phoneC")["plan"])


def test_planner_respects_peer_disabled(app_factory) -> None:
    with TestClient(app_factory(peer_enabled=False)) as client:
        _register(client, "phoneA", 9000)
        _complete(client, "phoneA", _request(client, "phoneA"))
        _register(client, "phoneB", 9001)
        assert all(p["source"] == "server" for p in _request(client, "phoneB")["plan"])
        reg = client.post(
            "/api/register",
            json={
                "protocol_version": "1.0",
                "device_id": "phoneB",
                "platform": "android",
                "cpu_arch": "aarch64",
                "ram_mb": 6144,
                "runtime": "x",
                "peer_port": 9001,
            },
        ).json()
        assert reg["peer_enabled"] is False


# ---------------------------------------------------------------- peer service


def test_peer_service_serves_only_verified_variants(tmp_path: Path) -> None:
    import os

    from client.downloader import save_variant_meta, variant_paths

    data = os.urandom(2 * 1024 * 1024 + 500)
    entry = fake_entry("shared_int8", data, 0.5, "CV/x.onnx")
    variant = {k: entry[k] for k in ("size_bytes", "sha256", "chunk_size", "chunk_count")}
    variant.update(id="shared_int8", file="Shared.onnx")
    cache = tmp_path / "cache"
    final, part = variant_paths(cache, variant)
    final.parent.mkdir(parents=True)
    part.write_bytes(data)  # đang tải dở: không được chia sẻ

    peer = PeerService(cache, 0, host="127.0.0.1", log=lambda _: None)
    peer.start()
    try:
        base = f"http://127.0.0.1:{peer.port}"
        assert peer.refresh() == []
        assert requests.get(f"{base}/chunks/shared_int8/0", timeout=5).status_code == 404

        part.rename(final)
        save_variant_meta(cache, variant)
        assert peer.refresh() == ["shared_int8"]
        for i, chunk in enumerate(entry["chunks"]):
            resp = requests.get(f"{base}/chunks/shared_int8/{i}", timeout=5)
            assert resp.status_code == 200
            assert resp.headers["Content-Type"] == "application/octet-stream"
            assert int(resp.headers["Content-Length"]) == chunk["size_bytes"]
            assert hashlib.sha256(resp.content).hexdigest() == chunk["sha256"]
        assert requests.get(f"{base}/chunks/shared_int8/3", timeout=5).status_code == 404
        assert requests.get(f"{base}/chunks/other_int8/0", timeout=5).status_code == 404
        assert requests.get(f"{base}/chunks/../etc/0", timeout=5).status_code == 404

        # file bị sửa sau đó: refresh không còn chia sẻ variant hỏng
        final.write_bytes(b"x" * len(data))
        peer._shared.clear()
        assert peer.refresh() == []
    finally:
        peer.stop()
