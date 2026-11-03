"""Kiểm thử cho lớp CSDL SQLAlchemy (server.db)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.db import (
    Base,
    DeviceRecord,
    DistributionRequestRecord,
    ModelArtifactRecord,
    ModelChunkRecord,
    ModelVariantRecord,
    PerformanceFeedbackRecord,
    UserFeedbackRecord,
    get_feedback_statistics,
    get_ready_variants,
    record_device,
    record_distribution_request,
    record_performance_feedback,
    record_user_feedback,
    sync_registry_to_db,
)


@pytest.fixture
def db_session():
    """Tạo session SQLite in-memory độc lập cho mỗi test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_sync_registry_to_db(db_session, tmp_path: Path):
    # Tạo fake registry.json
    registry_file = tmp_path / "registry.json"
    data = {
        "schema_version": "1.0",
        "variants": [
            {
                "variant_id": "test_model_v1",
                "model_id": "test_model",
                "task_id": "image_classification",
                "file": "test_v1.onnx",
                "storage_path": "CV/test_v1.onnx",
                "family": "TestFamily",
                "level": 0,
                "precision": "fp32",
                "size_bytes": 2048,
                "sha256": "fakehashwhole",
                "chunk_size": 1024,
                "chunk_count": 2,
                "top1_accuracy": 0.85,
                "status": "ready",
                "chunks": [
                    {"index": 0, "offset": 0, "size_bytes": 1024, "sha256": "hash0"},
                    {"index": 1, "offset": 1024, "size_bytes": 1024, "sha256": "hash1"},
                ],
            }
        ],
    }
    registry_file.write_text(json.dumps(data), encoding="utf-8")

    count = sync_registry_to_db(db_session, registry_file)
    assert count == 1

    ready = get_ready_variants(db_session)
    assert len(ready) == 1
    assert ready[0].variant_id == "test_model_v1"
    assert ready[0].artifact is not None
    assert ready[0].artifact.size_bytes == 2048
    assert len(ready[0].chunks) == 2
    assert ready[0].chunks[0].sha256 == "hash0"


def test_device_and_request_recording(db_session):
    dev = record_device(
        db_session,
        device_id="dev01",
        device_class="classA",
        platform="windows",
        cpu_arch="x86_64",
        ram_mb=8192,
    )
    assert dev.device_id == "dev01"
    assert dev.device_class == "classA"

    # Update device
    dev_updated = record_device(
        db_session,
        device_id="dev01",
        device_class="classA",
        platform="windows",
        cpu_arch="x86_64",
        ram_mb=16384,
        peer_port=9000,
        peer_enabled=True,
    )
    assert dev_updated.ram_mb == 16384
    assert dev_updated.peer_enabled is True

    # Record distribution request
    req = record_distribution_request(
        db_session,
        request_id="req-123",
        device_id="dev01",
        task_id="image_classification",
        variant_id="mobilenetv2_int8",
        bandwidth_kbps=2500.0,
        deadline_s=30.0,
        reason="Lựa chọn tối ưu",
    )
    assert req.request_id == "req-123"
    assert req.device_id == "dev01"


def test_performance_and_user_feedback(db_session):
    # Record performance feedback
    perf = record_performance_feedback(
        db_session,
        request_id="req-123",
        device_id="dev01",
        variant_id="mobilenetv2_int8",
        device_class="classA",
        download_ms=1200.0,
        load_ms=150.0,
        latency_ms_median=45.2,
        action="keep",
        reason="Đạt chuẩn SLO",
    )
    assert perf.id is not None
    assert perf.latency_ms_median == 45.2

    # Record user feedback
    fb1 = record_user_feedback(
        db_session,
        feedback_id="fb-001",
        device_id="dev01",
        variant_id="mobilenetv2_int8",
        feedback_text="Mô hình nhận diện rất nhanh và chuẩn xác",
        sentiment_score=5,
        sentiment_label="Rất tốt",
        flag_for_retrain=False,
    )
    assert fb1.sentiment_score == 5
    assert fb1.flag_for_retrain is False

    # Thử ghi lại cùng feedback_id (chống duplicate)
    fb_duplicate = record_user_feedback(
        db_session,
        feedback_id="fb-001",
        device_id="dev01",
        variant_id="mobilenetv2_int8",
        feedback_text="Nội dung khác nhưng trùng id",
        sentiment_score=1,
        sentiment_label="Tệ",
        flag_for_retrain=True,
    )
    assert fb_duplicate.id == fb1.id
    assert fb_duplicate.sentiment_score == 5

    # Thêm feedback tiêu cực cần retrain
    record_user_feedback(
        db_session,
        feedback_id="fb-002",
        device_id="dev01",
        variant_id="mobilenetv2_int8",
        feedback_text="Dự đoán sai ảnh chó thành mèo, quá chậm",
        sentiment_score=1,
        sentiment_label="Tệ",
        flag_for_retrain=True,
    )

    stats = get_feedback_statistics(db_session)
    assert stats["performance_feedback_count"] == 1
    assert stats["user_feedback_count"] == 2
    assert stats["retrain_flagged_count"] == 1
    assert stats["average_latency_ms"] == 45.2

