"""Lớp truy xuất dữ liệu (Repository) cho Server."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session, joinedload

from server.db.models import (
    DeviceRecord,
    DistributionRequestRecord,
    ModelArtifactRecord,
    ModelChunkRecord,
    ModelFamilyRecord,
    ModelVariantRecord,
    PerformanceFeedbackRecord,
    TaskRecord,
    UserFeedbackRecord,
    SelectorWeightRecord,
)


def get_ready_variants(session: Session) -> Sequence[ModelVariantRecord]:
    """Lấy tất cả các variant có status='ready' cùng artifact và chunks."""
    stmt = (
        select(ModelVariantRecord)
        .options(
            joinedload(ModelVariantRecord.model).joinedload(ModelFamilyRecord.task),
            joinedload(ModelVariantRecord.artifact),
            joinedload(ModelVariantRecord.chunks),
        )
        .where(ModelVariantRecord.status == "ready")
    )
    return session.scalars(stmt).unique().all()


def record_device(
    session: Session,
    device_id: str,
    device_class: str,
    platform: str,
    cpu_arch: str,
    ram_mb: int,
    runtime: str | None = None,
    peer_port: int | None = None,
    peer_enabled: bool = False,
    last_ip: str | None = None,
) -> DeviceRecord:
    """Ghi nhận hoặc cập nhật thiết bị biên."""
    device = session.get(DeviceRecord, device_id)
    now = datetime.now(timezone.utc)
    if not device:
        device = DeviceRecord(
            device_id=device_id,
            device_class=device_class,
            platform=platform,
            cpu_arch=cpu_arch,
            ram_mb=ram_mb,
            runtime=runtime,
            peer_port=peer_port,
            peer_enabled=peer_enabled,
            last_ip=last_ip,
            last_seen=now,
        )
        session.add(device)
    else:
        device.device_class = device_class
        device.platform = platform
        device.cpu_arch = cpu_arch
        device.ram_mb = ram_mb
        device.runtime = runtime
        device.peer_port = peer_port
        device.peer_enabled = peer_enabled
        device.last_ip = last_ip
        device.last_seen = now

    session.commit()
    return device


def record_distribution_request(
    session: Session,
    request_id: str,
    device_id: str,
    task_id: str,
    variant_id: str,
    selector_version: str | None = None,
    bandwidth_kbps: float | None = None,
    deadline_s: float | None = None,
    free_storage_mb: float | None = None,
    reason: str | None = None,
) -> DistributionRequestRecord:
    """Ghi nhận một lượt cấp phát mô hình."""
    record = DistributionRequestRecord(
        request_id=request_id,
        device_id=device_id,
        task_id=task_id,
        variant_id=variant_id,
        selector_version=selector_version,
        bandwidth_kbps=bandwidth_kbps,
        deadline_s=deadline_s,
        free_storage_mb=free_storage_mb,
        reason=reason,
    )
    session.add(record)
    session.commit()
    return record


def record_performance_feedback(
    session: Session,
    request_id: str | None,
    device_id: str | None,
    variant_id: str | None,
    device_class: str | None,
    download_ms: float | None = None,
    load_ms: float | None = None,
    latency_ms_median: float | None = None,
    runs: int | None = None,
    peak_ram_mb: float | None = None,
    chunks_from_server: int | None = None,
    chunks_from_peer: int | None = None,
    action: str | None = None,
    reason: str | None = None,
) -> PerformanceFeedbackRecord:
    """Lưu số đo hiệu năng thực tế từ Edge Agent vào database."""
    record = PerformanceFeedbackRecord(
        request_id=request_id,
        device_id=device_id,
        variant_id=variant_id,
        device_class=device_class,
        download_ms=download_ms,
        load_ms=load_ms,
        latency_ms_median=latency_ms_median,
        runs=runs,
        peak_ram_mb=peak_ram_mb,
        chunks_from_server=chunks_from_server,
        chunks_from_peer=chunks_from_peer,
        action=action,
        reason=reason,
    )
    session.add(record)
    session.commit()
    return record


def record_user_feedback(
    session: Session,
    device_id: str,
    variant_id: str,
    feedback_text: str,
    sentiment_score: int,
    sentiment_label: str,
    flag_for_retrain: bool,
    feedback_id: str | None = None,
    request_id: str | None = None,
    rating: int | None = None,
    is_correct: bool | None = None,
    actual_label: str | None = None,
    sentiment_confidence: float | None = None,
    sentiment_engine: str | None = None,
    feedback_reason: str | None = None,
) -> UserFeedbackRecord:
    """Lưu đánh giá người dùng (NLP sentiment / CLI rating) vào database."""
    if feedback_id:
        existing = session.execute(
            select(UserFeedbackRecord).where(UserFeedbackRecord.feedback_id == feedback_id)
        ).scalar_one_or_none()
        if existing:
            return existing

    record = UserFeedbackRecord(
        feedback_id=feedback_id,
        request_id=request_id,
        device_id=device_id,
        variant_id=variant_id,
        rating=rating if rating is not None else sentiment_score,
        is_correct=is_correct,
        actual_label=actual_label,
        feedback_text=feedback_text,
        sentiment_score=sentiment_score,
        sentiment_label=sentiment_label,
        flag_for_retrain=flag_for_retrain,
        sentiment_confidence=sentiment_confidence,
        sentiment_engine=sentiment_engine,
        feedback_reason=feedback_reason,
    )
    session.add(record)
    session.commit()
    return record


def apply_selector_feedback(
    session: Session,
    *,
    task_id: str,
    variant_id: str,
    device_class: str | None,
    sentiment_score: int,
    feedback_id: str | None,
    reward: float = 0.25,
    penalty: float = 0.25,
    minimum: float = -5.0,
    maximum: float = 5.0,
) -> SelectorWeightRecord:
    """Cập nhật trọng số selector theo score 1..5, có clamp và chống lặp feedback."""
    if sentiment_score not in {1, 2, 3, 4, 5}:
        raise ValueError("sentiment_score phải nằm trong khoảng 1..5")
    if feedback_id:
        duplicate = session.execute(
            select(SelectorWeightRecord).where(
                SelectorWeightRecord.last_feedback_id == feedback_id
            )
        ).scalar_one_or_none()
        if duplicate is not None:
            return duplicate

    record = session.execute(
        select(SelectorWeightRecord)
        .where(
            SelectorWeightRecord.task_id == task_id,
            SelectorWeightRecord.variant_id == variant_id,
            SelectorWeightRecord.device_class == device_class,
        )
        .order_by(desc(SelectorWeightRecord.version))
    ).scalars().first()
    if record is None:
        record = SelectorWeightRecord(
            task_id=task_id,
            variant_id=variant_id,
            device_class=device_class,
            weight=0.0,
            version=1,
        )
        session.add(record)
        session.flush()

    delta = 0.0 if sentiment_score == 3 else (
        reward if sentiment_score >= 4 else -penalty
    )
    record.weight = max(minimum, min(maximum, record.weight + delta))
    record.version += 1
    record.last_feedback_id = feedback_id
    session.commit()
    return record


def get_feedback_statistics(session: Session) -> dict[str, Any]:
    """Thống kê tổng hợp phục vụ dashboard hoặc giám sát drift."""
    perf_count = session.scalar(select(func.count(PerformanceFeedbackRecord.id))) or 0
    avg_latency = session.scalar(select(func.avg(PerformanceFeedbackRecord.latency_ms_median))) or 0.0
    user_count = session.scalar(select(func.count(UserFeedbackRecord.id))) or 0
    retrain_needed_count = (
        session.scalar(
            select(func.count(UserFeedbackRecord.id)).where(UserFeedbackRecord.flag_for_retrain.is_(True))
        )
        or 0
    )

    return {
        "performance_feedback_count": perf_count,
        "average_latency_ms": round(float(avg_latency), 2),
        "user_feedback_count": user_count,
        "retrain_flagged_count": retrain_needed_count,
    }
