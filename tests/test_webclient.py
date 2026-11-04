"""Giao diện client /app và phần server cho trình duyệt: trang tĩnh, link/QR, /api/announce,
signaling WebRTC, peer WebRTC trong plan, chọn model theo chế độ dùng, học tốc độ từ feedback."""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient
from validate_examples import schema_errors

WEB_RUNTIME = "onnxruntime-web 1.20.1"


def register_browser(
    client: TestClient,
    device_id: str,
    *,
    platform: str = "android_browser",
    threads: int = 1,
    ram_mb: int = 4096,
    p2p: str = "webrtc",
) -> dict:
    body = {
        "protocol_version": "1.0",
        "device_id": device_id,
        "platform": platform,
        "cpu_arch": "arm64" if platform != "web_desktop" else "x86_64",
        "ram_mb": ram_mb,
        "runtime": WEB_RUNTIME,
        "peer_port": None,
        "capabilities": {"threads": threads, "webgpu": False, "camera": True, "p2p": p2p},
    }
    assert schema_errors("RegisterRequest", body) == []
    resp = client.post("/api/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def ask(client: TestClient, device_id: str, **overrides) -> dict:
    body = {
        "device_id": device_id,
        "task": "object_detection",
        "bandwidth_kbps": 100_000,
        "deadline_s": 60,
        "free_storage_mb": None,
        "preferred_variant": None,
        "have_variants": [],
        "mode": "live",
        "target_fps": 5,
        **overrides,
    }
    assert schema_errors("ModelRequest", body) == []
    resp = client.post("/api/request", json=body)
    assert resp.status_code == 200, resp.text
    assert schema_errors("ModelResponse", resp.json()) == []
    return resp.json()


def go_online(client: TestClient, device_id: str, seconds: float = 3.0) -> threading.Thread:
    """Giả lập trang /app đang mở: một long-poll /api/signal chạy nền."""
    thread = threading.Thread(target=lambda: client.get(f"/api/signal/{device_id}?wait={seconds}"))
    thread.start()
    time.sleep(0.3)
    return thread


def announce_nano(client: TestClient, device_id: str) -> None:
    sha = ask(client, device_id)["variant"]["sha256"]
    body = {"device_id": device_id, "variants": [{"variant_id": "yolox_nano", "sha256": sha}]}
    assert schema_errors("AnnounceRequest", body) == []
    resp = client.post("/api/announce", json=body)
    assert resp.json() == {"ok": True, "accepted": ["yolox_nano"], "rejected": []}
    assert schema_errors("AnnounceResponse", resp.json()) == []


# ---------------------------------------------------------------- trang tĩnh


def test_client_page_is_isolated_for_multithreaded_wasm(client: TestClient) -> None:
    resp = client.get("/app/")
    assert resp.status_code == 200 and "text/html" in resp.headers["content-type"]
    assert resp.headers["cross-origin-opener-policy"] == "same-origin"
    assert resp.headers["cross-origin-embedder-policy"] == "require-corp"
    assert resp.headers["cache-control"] == "no-cache"
    for path in ("/app/js/app.js", "/app/js/worker.js", "/app/js/p2p.js", "/app/css/app.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cross-origin-embedder-policy"] == "require-corp", path
    assert "cross-origin-embedder-policy" not in client.get("/ui/").headers  # dashboard không đổi


def test_short_and_legacy_links_redirect(client: TestClient) -> None:
    for path in ("/app", "/m", "/m/", "/m/index.html"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (302, 307), path
        assert resp.headers["location"] == "/app/"


def test_links_json_and_admin_links(app_factory) -> None:
    with TestClient(app_factory(https_port=8443, http_port=8000)) as client:
        assert client.get("/app/links.json").json() == {"https_port": 8443}
        data = client.get("/admin-api/links").json()
    assert data["https_port"] == 8443 and data["http_port"] == 8000
    kinds = [link["kind"] for link in data["links"]]
    assert kinds[0] == "https" and "http" in kinds
    assert all(link["url"].endswith("/app/") for link in data["links"])


# ---------------------------------------------------------------- chọn model theo chế độ


def test_phone_live_gets_fast_model_and_photo_gets_better(client: TestClient) -> None:
    register_browser(client, "phoneA")
    live = ask(client, "phoneA")
    assert live["variant"]["id"] == "yolox_nano"
    assert live["variant"]["detection"]["format"] == "yolox"
    assert "camera trực tiếp ngân sách 200 ms" in live["reason"]
    assert "yolox_x vì không chạy được trên onnxruntime-web" in live["reason"]
    photo = ask(client, "phoneA", mode="photo", target_fps=None)
    assert photo["variant"]["id"] == "yolox_tiny"


def test_multithreaded_laptop_gets_bigger_model(client: TestClient) -> None:
    register_browser(client, "laptopW", platform="web_desktop", threads=4, ram_mb=8192)
    assert ask(client, "laptopW")["variant"]["id"] == "yolox_tiny"
    assert ask(client, "laptopW", mode="photo", target_fps=None)["variant"]["id"] == "yolox_m"


def test_feedback_teaches_class_speed(client: TestClient) -> None:
    register_browser(client, "phoneA")
    first = ask(client, "phoneA")
    body = {
        "device_id": "phoneA",
        "request_id": first["request_id"],
        "variant_id": "yolox_nano",
        "download_ms": 900.0,
        "chunks_from_peer": 0,
        "chunks_from_server": 4,
        "load_ms": 600.0,
        "latency_ms_median": 21.6,  # 20 ms/GFLOP: máy nhanh hơn số ước lượng 140
        "runs": 10,
        "peak_ram_mb": None,
        "top1_label": "dog",
        "top1_confidence": 0.87,
        "fps": 14.5,
        "detections": 1,
    }
    assert schema_errors("FeedbackRequest", body) == []
    resp = client.post("/api/feedback", json=body).json()
    # dư sức: đề xuất nâng cấp lên bản tốt hơn vẫn trong 80 % ngân sách 200 ms
    assert resp["action"] == "switch" and resp["new_variant_id"] == "yolox_tiny"
    assert "nâng lớp" in resp["reason"]
    speeds = client.get("/admin-api/summary").json()["class_speeds"]
    assert speeds["android_browser_arm64_4-8gb"]["ms_per_gflop"] == pytest.approx(20.0)
    again = ask(client, "phoneA")
    assert "đo thực tế" in again["reason"]


# ---------------------------------------------------------------- announce + P2P WebRTC


def test_announce_rejects_wrong_hash_and_unknown_variant(client: TestClient) -> None:
    register_browser(client, "phoneA")
    resp = client.post(
        "/api/announce",
        json={
            "device_id": "phoneA",
            "variants": [
                {"variant_id": "yolox_nano", "sha256": "0" * 64},
                {"variant_id": "khong_co", "sha256": "0" * 64},
            ],
        },
    ).json()
    assert resp == {"ok": True, "accepted": [], "rejected": ["yolox_nano", "khong_co"]}
    assert (
        client.post("/api/announce", json={"device_id": "ghost", "variants": []}).status_code == 404
    )


def test_webrtc_peer_only_when_online(client: TestClient) -> None:
    register_browser(client, "phoneA")
    announce_nano(client, "phoneA")
    register_browser(client, "phoneB")
    # phoneA chưa mở trang (không long-poll): tải hết từ server
    assert {p["source"] for p in ask(client, "phoneB")["plan"]} == {"server"}

    thread = go_online(client, "phoneA")
    plan = ask(client, "phoneB")["plan"]
    thread.join()
    peers = [p for p in plan if p["source"] == "peer"]
    assert [p["url"] for p in peers] == [
        "webrtc://phoneA/chunks/yolox_nano/0",
        "webrtc://phoneA/chunks/yolox_nano/1",
    ]
    assert all(p["fallback_url"].endswith(f"/api/chunks/yolox_nano/{p['index']}") for p in peers)


def test_browser_and_python_peers_never_mix(client: TestClient) -> None:
    register_browser(client, "phoneA")
    announce_nano(client, "phoneA")
    # client Python cùng "lớp" giả định: platform khác nên lớp khác, và không nói WebRTC
    py = {
        "protocol_version": "1.0",
        "device_id": "pyA",
        "platform": "android",
        "cpu_arch": "arm64",
        "ram_mb": 4096,
        "runtime": "onnxruntime 1.31.0",
        "peer_port": None,
    }
    assert client.post("/api/register", json=py).status_code == 200
    thread = go_online(client, "phoneA")
    plan = ask(client, "pyA", mode=None, target_fps=None, bandwidth_kbps=600)["plan"]
    thread.join()
    assert all(p["source"] == "server" for p in plan)


def test_bad_webrtc_chunk_bans_that_browser(client: TestClient) -> None:
    register_browser(client, "phoneA")
    announce_nano(client, "phoneA")
    register_browser(client, "phoneB")
    thread = go_online(client, "phoneA")
    resp = ask(client, "phoneB")
    thread.join()
    have = {
        "device_id": "phoneB",
        "request_id": resp["request_id"],
        "variant_id": "yolox_nano",
        "chunks": [2, 3],
        "complete": False,
        "bandwidth_kbps": None,
        "bad_chunks": [{"index": 0, "source_url": "webrtc://phoneA/chunks/yolox_nano/0"}],
    }
    assert schema_errors("HaveRequest", have) == []
    assert client.post("/api/have", json=have).status_code == 200
    assert "webrtc://phoneA" in client.get("/admin-api/summary").json()["alerts"][0]["message"]
    thread = go_online(client, "phoneA")
    assert {p["source"] for p in ask(client, "phoneB")["plan"]} == {"server"}
    thread.join()


# ---------------------------------------------------------------- signaling


def test_signal_relay_and_offline(client: TestClient) -> None:
    register_browser(client, "phoneA")
    register_browser(client, "phoneB")
    msg = {
        "from_device": "phoneB",
        "to_device": "phoneA",
        "type": "offer",
        "session": "s1",
        "data": {"sdp": {"type": "offer", "sdp": "v=0"}},
    }
    assert schema_errors("SignalMessage", msg) == []
    # phoneA chưa mở trang: không giao được
    assert client.post("/api/signal", json=msg).json() == {"ok": True, "delivered": False}

    received: list = []
    thread = threading.Thread(
        target=lambda: received.append(client.get("/api/signal/phoneA?wait=3").json())
    )
    thread.start()
    time.sleep(0.3)
    assert client.post("/api/signal", json=msg).json()["delivered"] is True
    thread.join()
    assert received[0]["messages"][0]["session"] == "s1"
    devices = {d["device_id"]: d for d in client.get("/admin-api/devices").json()["devices"]}
    assert devices["phoneA"]["online"] is True and devices["phoneA"]["p2p_transport"] == "webrtc"

    # trang tự báo đóng: hết online ngay, không chờ 12 giây
    bye = {"from_device": "phoneA", "to_device": "phoneA", "type": "bye", "session": "offline"}
    client.post("/api/signal", json=bye)
    assert client.post("/api/signal", json=msg).json()["delivered"] is False
    assert client.get("/admin-api/summary").json()["p2p_webrtc"]["offers"] == 1


def test_signal_requires_registered_devices(client: TestClient) -> None:
    msg = {"from_device": "ghost", "to_device": "x", "type": "offer", "session": "s1", "data": None}
    assert client.post("/api/signal", json=msg).status_code == 404
    assert client.get("/api/signal/ghost?wait=0").status_code == 404
    register_browser(client, "phoneA")
    bad = {**msg, "from_device": "phoneA", "session": "có dấu"}
    assert client.post("/api/signal", json=bad).status_code == 400
