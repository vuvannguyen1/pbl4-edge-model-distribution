"""Kiểm thử cho luồng Admin API và Model Ingestion Pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from server.config import Settings
from server.db import get_session_factory, init_db, sync_registry_to_db
from server.main import create_app


@pytest.fixture
def test_app_and_client(tmp_path: Path):
    db_path = tmp_path / "test.db"
    settings = Settings(
        registry_path=tmp_path / "model_registry.json",
        model_store=tmp_path / "models",
        rate_limit_kbps=0,
        public_base_url="",
        peer_enabled=True,
        latency_slo_ms=500.0,
        data_dir=tmp_path / "data",
    )
    # Khởi tạo fake registry
    repo_root = Path(__file__).resolve().parent.parent
    real_registry = repo_root / "registry" / "model_registry.json"
    real_models = repo_root / "models"
    settings.registry_path.write_text(real_registry.read_text(encoding="utf-8"), encoding="utf-8")

    # Copy real models to test model store
    import shutil
    shutil.copytree(real_models, settings.model_store)

    app = create_app(settings=settings)
    client = TestClient(app)
    return app, client, settings


def test_admin_api_list_and_stats(test_app_and_client):
    _, client, _ = test_app_and_client

    # 1. Thống kê ban đầu
    res = client.get("/admin-api/feedback-stats")
    assert res.status_code == 200
    stats = res.json()
    assert "performance_feedback_count" in stats
    assert "user_feedback_count" in stats

    # 2. Danh sách model
    res = client.get("/admin-api/models")
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 4
    variant_ids = [v["variant_id"] for v in data["variants"]]
    assert "mobilenetv2_int8" in variant_ids

    # 3. Chi tiết variant
    res = client.get("/admin-api/variants/mobilenetv2_int8")
    assert res.status_code == 200
    v_data = res.json()
    assert v_data["variant_id"] == "mobilenetv2_int8"
    assert v_data["artifact"] is not None

    # 4. Cập nhật status
    res = client.post("/admin-api/variants/mobilenetv2_int8/status", json={"status": "disabled"})
    assert res.status_code == 200
    assert res.json()["new_status"] == "disabled"

    res = client.get("/admin-api/variants/mobilenetv2_int8")
    assert res.json()["status"] == "disabled"


def test_admin_upload_model(test_app_and_client):
    _, client, settings = test_app_and_client
    repo_root = Path(__file__).resolve().parent.parent
    src_onnx = repo_root / "models" / "CV" / "MobileNetV2_INT8.onnx"

    payload = {
        "file_path": str(src_onnx),
        "filename": "MobileNetV2_Custom.onnx",
        "metadata": {
            "variant_id": "mobilenetv2_custom",
            "model_id": "mobilenetv2",
            "task_id": "image_classification",
            "family": "MobileNetV2",
            "precision": "int8",
            "level": 1,
            "top1_accuracy": 0.72,
            "version": "2.0",
        },
    }

    res = client.post("/admin-api/models/upload", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["variant_id"] == "mobilenetv2_custom"
    assert data["chunk_count"] > 0

    # Kiểm tra đã xuất hiện trong danh sách admin
    res = client.get("/admin-api/variants/mobilenetv2_custom")
    assert res.status_code == 200
    assert res.json()["status"] == "ready"


def test_performance_feedback_saved_to_db(test_app_and_client):
    _, client, _ = test_app_and_client

    # 1. Đăng ký thiết bị
    reg_payload = {
        "protocol_version": "1.0",
        "device_id": "phone_test",
        "platform": "android",
        "cpu_arch": "aarch64",
        "ram_mb": 4096,
        "runtime": "onnxruntime 1.25.1",
        "peer_port": None,
    }
    res = client.post("/api/register", json=reg_payload)
    assert res.status_code == 200

    # 2. Xin model
    req_payload = {
        "device_id": "phone_test",
        "task": "image_classification",
        "bandwidth_kbps": 2000.0,
        "deadline_s": 30.0,
        "free_storage_mb": 1024.0,
        "preferred_variant": "mobilenetv2_int8",
        "have_variants": [],
    }
    res = client.post("/api/request", json=req_payload)
    assert res.status_code == 200
    req_data = res.json()
    request_id = req_data["request_id"]

    # 3. Gửi feedback
    fb_payload = {
        "request_id": request_id,
        "device_id": "phone_test",
        "variant_id": "mobilenetv2_int8",
        "download_ms": 1250.0,
        "load_ms": 120.0,
        "latency_ms_median": 35.5,
        "runs": 3,
        "peak_ram_mb": 150.0,
        "chunks_from_server": 4,
        "chunks_from_peer": 0,
        "top1_label": "tabby cat",
        "top1_confidence": 0.88,
    }
    res = client.post("/api/feedback", json=fb_payload)
    assert res.status_code == 200
    assert res.json()["action"] in ("keep", "switch")

    # 4. Kiểm tra CSDL qua admin stats
    res = client.get("/admin-api/feedback-stats")
    assert res.status_code == 200
    stats = res.json()
    assert stats["performance_feedback_count"] >= 1
    assert stats["average_latency_ms"] > 0

