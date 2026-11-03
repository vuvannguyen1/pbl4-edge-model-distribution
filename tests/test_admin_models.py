"""Quản trị model qua /admin-api: route chi tiết đã gộp (Registry + CSDL) và đổi trạng thái.

Mọi test dùng Registry giả trong tmp_path, vì đổi trạng thái ghi vào file Registry.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BIG = 3_000_000  # 3 miếng
SMALL = 1_500_000  # 2 miếng


def register(client: TestClient, device_id: str) -> None:
    resp = client.post(
        "/api/register",
        json={
            "protocol_version": "1.0",
            "device_id": device_id,
            "platform": "windows",
            "cpu_arch": "AMD64",
            "ram_mb": 8192,
            "runtime": "onnxruntime 1.31.0",
            "peer_port": None,
        },
    )
    assert resp.status_code == 200, resp.text


def ask(client: TestClient, device_id: str, **overrides) -> dict:
    body = {
        "device_id": device_id,
        "task": "image_classification",
        "bandwidth_kbps": None,
        "deadline_s": 60,
        "free_storage_mb": None,
        "preferred_variant": None,
        "have_variants": [],
        **overrides,
    }
    return client.post("/api/request", json=body)


def set_status(client: TestClient, variant_id: str, status: str):
    return client.post(f"/admin-api/variants/{variant_id}/status", json={"status": status})


def registry_status(registry_path: Path, variant_id: str) -> str:
    data = json.loads(registry_path.read_text(encoding="utf-8"))
    return next(v["status"] for v in data["variants"] if v["variant_id"] == variant_id)


@pytest.fixture
def two_variants(app_factory, fake_store):
    """App với hai variant: big_fp32 (top-1 cao hơn, được chọn mặc định) và small_int8."""
    registry_path, store = fake_store([("big_fp32", BIG, 0.75), ("small_int8", SMALL, 0.68)])
    app = app_factory(registry_path=registry_path, model_store=store)
    with TestClient(app) as client:
        yield client, registry_path


def test_merged_variant_detail_has_registry_and_db_fields(two_variants) -> None:
    client, _ = two_variants
    resp = client.get("/admin-api/variants/big_fp32")
    assert resp.status_code == 200, resp.text
    v = resp.json()
    # trường mà server/static/js/views/models.js đang dùng
    for key in (
        "status", "distributable", "family", "version", "precision", "file", "size_bytes",
        "sha256", "input_shape", "output_contract", "supported_runtimes", "top1_accuracy",
        "devices_complete", "metrics_count", "warnings", "chunks", "model_card",
    ):  # fmt: skip
        assert key in v, key
    assert v["status"] == "ready" and v["distributable"] is True
    assert len(v["chunks"]) == 3
    # trường mới từ CSDL
    assert v["db_status"] == "ready"
    assert v["artifact"]["sha256"] == v["sha256"]
    assert v["artifact"]["chunk_count"] == 3


def test_merged_variant_detail_unknown_is_404(two_variants) -> None:
    client, _ = two_variants
    resp = client.get("/admin-api/variants/khong_co")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "UNKNOWN_VARIANT"


def test_admin_routes_live_only_in_admin_router(app_factory) -> None:
    app = app_factory()
    # main.py không còn khai báo /admin-api trực tiếp: mọi route quản trị nằm trong admin_api
    direct = [getattr(route, "path", "") or "" for route in app.routes]
    assert not [p for p in direct if p.startswith("/admin-api")]
    paths = app.openapi()["paths"]
    assert set(paths["/admin-api/variants/{variant_id}"]) == {"get"}
    for path, method in (
        ("/admin-api/variants/{variant_id}/status", "post"),
        ("/admin-api/models/upload", "post"),
        ("/admin-api/models", "get"),
        ("/admin-api/feedback-stats", "get"),
    ):
        assert method in paths[path], path


def test_disable_then_enable_changes_selection(two_variants) -> None:
    client, registry_path = two_variants
    register(client, "laptopA")
    assert ask(client, "laptopA").json()["variant"]["id"] == "big_fp32"

    resp = set_status(client, "big_fp32", "disabled")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["new_status"] == "disabled" and body["distributable"] is False
    assert registry_status(registry_path, "big_fp32") == "disabled"
    assert client.get("/admin-api/variants/big_fp32").json()["db_status"] == "disabled"
    # không cần khởi động lại: lượt xin kế tiếp bỏ qua variant bị tắt
    assert ask(client, "laptopA").json()["variant"]["id"] == "small_int8"
    preferred = ask(client, "laptopA", preferred_variant="big_fp32")
    assert preferred.status_code == 404

    resp = set_status(client, "big_fp32", "ready")
    assert resp.status_code == 200 and resp.json()["distributable"] is True
    assert ask(client, "laptopA").json()["variant"]["id"] == "big_fp32"


def test_disable_does_not_cut_unfinished_download(two_variants) -> None:
    client, _ = two_variants
    register(client, "laptopA")
    first = ask(client, "laptopA").json()
    assert first["variant"]["id"] == "big_fp32"

    assert set_status(client, "big_fp32", "disabled").status_code == 200
    # lượt r-0001 đang tải dở: vẫn lấy được mọi miếng
    for index in range(3):
        resp = client.get(f"/api/chunks/big_fp32/{index}")
        assert resp.status_code == 200, resp.text
    done = client.post(
        "/api/have",
        json={
            "request_id": first["request_id"],
            "device_id": "laptopA",
            "variant_id": "big_fp32",
            "chunks": [0, 1, 2],
            "bad_chunks": [],
            "bandwidth_kbps": None,
            "complete": True,
        },
    )
    assert done.status_code == 200, done.text
    # hết lượt dở thì không giao miếng của variant đã tắt nữa
    assert client.get("/api/chunks/big_fp32/0").status_code == 404


def test_cannot_disable_last_ready_variant(two_variants) -> None:
    client, registry_path = two_variants
    assert set_status(client, "small_int8", "disabled").status_code == 200
    resp = set_status(client, "big_fp32", "retired")
    assert resp.status_code == 409
    assert "cuối cùng" in resp.json()["error"]["message"]
    assert registry_status(registry_path, "big_fp32") == "ready"


def test_invalid_status_is_400(two_variants) -> None:
    client, _ = two_variants
    assert set_status(client, "big_fp32", "xoa").status_code == 400
    assert set_status(client, "khong_co", "disabled").status_code == 404


def test_db_failure_rolls_back_registry(two_variants, monkeypatch) -> None:
    import server.admin_api as admin_api

    client, registry_path = two_variants
    before = registry_path.read_text(encoding="utf-8")

    class BrokenFactory:
        def __call__(self):
            raise RuntimeError("CSDL hỏng")

    monkeypatch.setattr(admin_api, "get_session_factory", lambda: BrokenFactory())
    resp = set_status(client, "big_fp32", "disabled")
    assert resp.status_code == 500
    assert "hoàn tác" in resp.json()["error"]["message"]
    assert registry_path.read_text(encoding="utf-8") == before
    monkeypatch.undo()
    register(client, "laptopA")
    assert ask(client, "laptopA").json()["variant"]["id"] == "big_fp32"


def test_user_feedback_written_to_settings_data_dir(app_factory, tmp_path: Path) -> None:
    with TestClient(app_factory()) as client:
        resp = client.post(
            "/api/user-feedback",
            json={"device_id": "laptopA", "feedback_text": "chạy nhanh, dùng tốt"},
        )
    assert resp.status_code == 200, resp.text
    log = tmp_path / "data" / "user_feedbacks.jsonl"
    assert log.is_file()
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["device_id"] == "laptopA"
