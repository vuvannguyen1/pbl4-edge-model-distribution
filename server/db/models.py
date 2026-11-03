"""Mô hình CSDL SQLAlchemy 2.0 cho PBL4 Edge Model Distribution.

Bao gồm:
- Quản lý danh mục mô hình (Tasks, Models, ModelVariants, ModelArtifacts, ModelChunks).
- Quản lý thiết bị và lịch sử yêu cầu (Devices, Requests).
- Quản lý phản hồi (PerformanceFeedback, UserFeedback).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- Danh mục Model


class TaskRecord(Base):
    """Tác vụ AI mà hệ thống hỗ trợ (CV, NLP, Audio,...)."""

    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_contract: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    models: Mapped[list[ModelFamilyRecord]] = relationship(
        "ModelFamilyRecord", back_populates="task", cascade="all, delete-orphan"
    )


class ModelFamilyRecord(Base):
    """Họ mô hình (vd: MobileNetV2, ResNet50, YOLOv8)."""

    __tablename__ = "models"

    model_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)

    task: Mapped[TaskRecord] = relationship("TaskRecord", back_populates="models")
    variants: Mapped[list[ModelVariantRecord]] = relationship(
        "ModelVariantRecord", back_populates="model", cascade="all, delete-orphan"
    )


class ModelVariantRecord(Base):
    """Biến thể mô hình cụ thể (artifact ONNX với định dạng lượng tử hóa & phiên bản)."""

    __tablename__ = "model_variants"

    variant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("models.model_id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    precision: Mapped[str] = mapped_column(String(32), nullable=False)  # fp32, int8, fp16
    compression_level: Mapped[int] = mapped_column(Integer, default=0)
    top1_accuracy: Mapped[float] = mapped_column(Float, default=0.0)
    task_group: Mapped[str] = mapped_column(String(32), default="CV", index=True)
    map50: Mapped[float | None] = mapped_column(Float, nullable=True)
    map50_95: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_ram_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms_p50: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms_p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), default="ready", index=True
    )  # draft, validating, ready, disabled, retired
    model_card_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    model: Mapped[ModelFamilyRecord] = relationship("ModelFamilyRecord", back_populates="variants")
    artifact: Mapped[ModelArtifactRecord | None] = relationship(
        "ModelArtifactRecord",
        back_populates="variant",
        uselist=False,
        cascade="all, delete-orphan",
    )
    chunks: Mapped[list[ModelChunkRecord]] = relationship(
        "ModelChunkRecord",
        back_populates="variant",
        order_by="ModelChunkRecord.chunk_index",
        cascade="all, delete-orphan",
    )


class ModelArtifactRecord(Base):
    """Thông tin tệp nhị phân ONNX của biến thể."""

    __tablename__ = "model_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    variant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("model_variants.variant_id", ondelete="CASCADE"), unique=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=1048576)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_model_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_format: Mapped[str | None] = mapped_column(String(32), nullable=True)
    distribution_format: Mapped[str] = mapped_column(String(32), default="onnx")

    variant: Mapped[ModelVariantRecord] = relationship(
        "ModelVariantRecord", back_populates="artifact"
    )


class ModelChunkRecord(Base):
    """Bảng băm chi tiết từng miếng 1 MiB của mô hình."""

    __tablename__ = "model_chunks"
    __table_args__ = (
        Index("idx_chunk_variant_index", "variant_id", "chunk_index", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    variant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("model_variants.variant_id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    variant: Mapped[ModelVariantRecord] = relationship(
        "ModelVariantRecord", back_populates="chunks"
    )


# ---------------------------------------------------------------- Thiết bị & Lượt xin tải


class DeviceRecord(Base):
    """Thiết bị biên đã đăng ký với Server."""

    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_class: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    cpu_arch: Mapped[str] = mapped_column(String(64), nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    peer_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peer_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class DistributionRequestRecord(Base):
    """Lịch sử lượt yêu cầu cấp phát mô hình từ Client."""

    __tablename__ = "requests"

    request_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    variant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("model_variants.variant_id", ondelete="SET NULL"), nullable=True, index=True
    )
    selector_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bandwidth_kbps: Mapped[float | None] = mapped_column(Float, nullable=True)
    deadline_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    free_storage_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


# ---------------------------------------------------------------- Feedback & Telemetry


class PerformanceFeedbackRecord(Base):
    """Số đo hiệu năng thực tế từ Edge Client Agent sau khi tải & chạy model."""

    __tablename__ = "performance_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("requests.request_id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True, index=True
    )
    variant_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("model_variants.variant_id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_class: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    download_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    peak_ram_mb: Mapped[float | None] = mapped_column(Float, nullable=True)
    chunks_from_server: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunks_from_peer: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)  # keep, switch
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class UserFeedbackRecord(Base):
    """Đánh giá chất lượng và cảm nhận từ người dùng cuối (CLI / Web feedback)."""

    __tablename__ = "user_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feedback_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    request_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("requests.request_id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="SET NULL"), nullable=True, index=True
    )
    variant_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("model_variants.variant_id", ondelete="SET NULL"), nullable=True, index=True
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5 sao
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    actual_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sentiment_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    sentiment_engine: Mapped[str | None] = mapped_column(String(64), nullable=True)
    feedback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flag_for_retrain: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)


class SelectorWeightRecord(Base):
    """Trọng số thưởng/phạt version hóa cho model selector."""

    __tablename__ = "selector_weights"
    __table_args__ = (
        Index("idx_selector_weight_scope", "task_id", "variant_id", "device_class"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    variant_id: Mapped[str] = mapped_column(String(64), index=True)
    device_class: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    version: Mapped[int] = mapped_column(Integer, default=1, index=True)
    last_feedback_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class DataFileRecord(Base):
    """Snapshot dữ liệu dưới data/ để database có thể backup/tái lập toàn bộ dữ liệu."""

    __tablename__ = "data_files"
    __table_args__ = (Index("idx_data_file_path", "relative_path", unique=True),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    relative_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    modified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
