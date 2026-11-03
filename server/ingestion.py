"""Quy trình tiếp nhận và kiểm định mô hình ONNX mới (Model Ingestion Pipeline).

Hỗ trợ tiếp nhận tệp .onnx mới, thẩm định đồ thị ONNX, chia chunk 1 MiB,
tính SHA-256, ghi vào CSDL SQL và cập nhật Model Registry động.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import onnxruntime as ort
from sqlalchemy import select
from sqlalchemy.orm import Session

from server.config import CHUNK_SIZE, Settings
from server.db.models import (
    ModelArtifactRecord,
    ModelChunkRecord,
    ModelFamilyRecord,
    ModelVariantRecord,
    TaskRecord,
)
from server.registry import safe_storage_path, write_registry_atomic

logger = logging.getLogger("pbl4.server.ingestion")


class IngestionError(Exception):
    """`code` là mã lỗi /admin-api: BAD_REQUEST (400) hoặc CONFLICT (409, trùng file)."""

    def __init__(self, message: str, code: str = "BAD_REQUEST") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


VARIANT_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")  # $defs/VariantId của protocol
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.onnx$")
TASK_GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PRECISIONS = ("fp32", "fp16", "int8")


def safe_filename(filename: str) -> str:
    """Chỉ nhận tên file trần (không thư mục, không '..'), thêm đuôi .onnx nếu thiếu."""
    name = filename.strip()
    if not name.lower().endswith(".onnx"):
        name = f"{name}.onnx"
    if "/" in name or "\\" in name or ".." in name or not FILENAME_RE.match(name):
        raise IngestionError(
            f"Tên file '{filename}' không hợp lệ: chỉ dùng chữ, số, '.', '_', '-', đuôi .onnx, "
            "không chứa thư mục."
        )
    return name


def _storage_owner(registry_path: Path, storage_path: str) -> str | None:
    """variant_id trong Registry đang dùng `storage_path` (so không phân biệt hoa thường)."""
    try:
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in raw.get("variants", []):
        if str(entry.get("storage_path", "")).lower() == storage_path.lower():
            return entry.get("variant_id")
    return None


def hash_content_chunks(content: bytes) -> tuple[str, list[dict[str, Any]]]:
    """Tính SHA-256 toàn bộ nội dung và danh sách từng chunk 1 MiB."""
    whole = hashlib.sha256(content).hexdigest()
    chunks: list[dict[str, Any]] = []
    total_len = len(content)
    index = 0
    offset = 0

    while offset < total_len:
        block = content[offset : offset + CHUNK_SIZE]
        chunks.append(
            {
                "index": index,
                "offset": offset,
                "size_bytes": len(block),
                "sha256": hashlib.sha256(block).hexdigest(),
            }
        )
        index += 1
        offset += len(block)

    return whole, chunks


def inspect_onnx_model(file_path: Path) -> dict[str, Any]:
    """Mở và kiểm tra cấu trúc mô hình bằng ONNX Runtime."""
    try:
        sess = ort.InferenceSession(str(file_path), providers=["CPUExecutionProvider"])
    except Exception as exc:
        raise IngestionError(f"Không thể mở file ONNX bằng ONNX Runtime: {exc}") from exc

    inputs = sess.get_inputs()
    outputs = sess.get_outputs()
    if not inputs:
        raise IngestionError("Mô hình ONNX không có input tensor nào.")

    first_input = inputs[0]
    return {
        "input_name": first_input.name,
        "input_shape": first_input.shape,
        "input_type": first_input.type,
        "output_count": len(outputs),
    }


def ingest_model(
    *,
    model_bytes: bytes,
    filename: str,
    metadata: dict[str, Any],
    settings: Settings,
    session: Session,
) -> dict[str, Any]:
    """Tiếp nhận, kiểm định và đưa một model ONNX mới vào hệ thống.

    1. Kiểm tra trường bắt buộc trong metadata.
    2. Lưu tệp tạm vào staging, kiểm tra bằng ONNX Runtime.
    3. Di chuyển vào thư mục phân phối `models/<task_group>/`.
    4. Băm SHA-256 toàn file và từng chunk 1 MiB.
    5. Cập nhật các bảng CSDL SQL nguyên tử.
    6. Cập nhật registry/model_registry.json để Selector nhận ngay mà không cần reload.
    """
    variant_id = str(metadata.get("variant_id", "")).strip()
    model_id = str(metadata.get("model_id", "")).strip()
    task_id = str(metadata.get("task_id", "image_classification")).strip()
    family = str(metadata.get("family", model_id)).strip()
    task_group = str(metadata.get("task_group", "CV")).strip()
    precision = str(metadata.get("precision", "fp32")).strip()
    level = int(metadata.get("level", metadata.get("compression_level", 0)))
    top1_accuracy = float(metadata.get("top1_accuracy", 0.0))
    version = str(metadata.get("version", "1.0")).strip()
    source_provider = metadata.get("source_provider")
    source_model_id = metadata.get("source_model_id")
    source_revision = metadata.get("source_revision")
    source_format = metadata.get("source_format")
    distribution_format = metadata.get("distribution_format", "onnx")

    if not variant_id or not model_id:
        raise IngestionError("Metadata phải có 'variant_id' và 'model_id'.")
    if not VARIANT_ID_RE.match(variant_id):
        raise IngestionError(
            f"variant_id '{variant_id}' không hợp lệ: chỉ chữ thường, số, '_' (tối đa 64 ký tự)."
        )
    if not TASK_GROUP_RE.match(task_group):
        raise IngestionError(f"task_group '{task_group}' không hợp lệ.")
    if precision not in PRECISIONS:
        raise IngestionError(f"precision phải là một trong {', '.join(PRECISIONS)}.")
    if not 0.0 <= top1_accuracy <= 1.0 or level < 0:
        raise IngestionError("top1_accuracy phải trong [0, 1] và level ≥ 0.")
    if not model_bytes:
        raise IngestionError("File model rỗng.")
    filename = safe_filename(filename)

    # Thư mục đích: phải nằm trong Model Store
    storage_path = f"{task_group}/{filename}"
    dest_file = safe_storage_path(settings.model_store, storage_path)
    if dest_file is None:
        raise IngestionError(f"Đường dẫn lưu {storage_path} không hợp lệ.")
    owner = _storage_owner(settings.registry_path, storage_path)
    if owner is not None and owner != variant_id:
        raise IngestionError(
            f"File {storage_path} đang là model của variant {owner}; đặt tên file khác.",
            code="CONFLICT",
        )
    if dest_file.exists() and owner != variant_id:
        raise IngestionError(
            f"Model Store đã có file {storage_path}; đặt tên file khác để không ghi đè.",
            code="CONFLICT",
        )

    # Ghi ra file tạm, kiểm tra ONNX xong mới đưa vào chỗ thật; lỗi thì không để lại gì
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    staged = dest_file.with_name(f".{filename}.{uuid.uuid4().hex}.upload")
    try:
        staged.write_bytes(model_bytes)
        onnx_info = inspect_onnx_model(staged)
        os.replace(staged, dest_file)
    finally:
        staged.unlink(missing_ok=True)

    try:
        return _record_model(
            model_bytes=model_bytes,
            filename=filename,
            storage_path=storage_path,
            onnx_info=onnx_info,
            metadata=metadata,
            fields={
                "variant_id": variant_id,
                "model_id": model_id,
                "task_id": task_id,
                "family": family,
                "precision": precision,
                "level": level,
                "top1_accuracy": top1_accuracy,
                "version": version,
            },
            settings=settings,
            session=session,
        )
    except Exception:
        if owner is None:  # file vừa thêm, chưa thuộc ai: gỡ để Model Store khớp Registry
            dest_file.unlink(missing_ok=True)
        raise


def _record_model(
    *,
    model_bytes: bytes,
    filename: str,
    storage_path: str,
    onnx_info: dict[str, Any],
    metadata: dict[str, Any],
    fields: dict[str, Any],
    settings: Settings,
    session: Session,
) -> dict[str, Any]:
    """Băm, ghi CSDL rồi Registry cho một file đã kiểm định và đã nằm trong Model Store."""
    variant_id, model_id, task_id = fields["variant_id"], fields["model_id"], fields["task_id"]
    family, precision, level = fields["family"], fields["precision"], fields["level"]
    top1_accuracy, version = fields["top1_accuracy"], fields["version"]

    # Tính toán băm
    whole_sha256, chunks = hash_content_chunks(model_bytes)
    size_bytes = len(model_bytes)

    # 1. Cập nhật SQL DB
    task = session.get(TaskRecord, task_id)
    if not task:
        task = TaskRecord(
            task_id=task_id,
            name=task_id.replace("_", " ").title(),
            description=f"Tác vụ {task_id}",
        )
        session.add(task)
        session.flush()

    model_family = session.get(ModelFamilyRecord, model_id)
    if not model_family:
        model_family = ModelFamilyRecord(
            model_id=model_id,
            task_id=task_id,
            name=family,
            family=family,
            description=f"Họ mô hình {family}",
        )
        session.add(model_family)
        session.flush()

    variant_rec = session.get(ModelVariantRecord, variant_id)
    if not variant_rec:
        variant_rec = ModelVariantRecord(
            variant_id=variant_id,
            model_id=model_id,
            version=version,
            precision=precision,
            compression_level=level,
            top1_accuracy=top1_accuracy,
            status="ready",
            model_card_json=json.dumps(metadata, ensure_ascii=False),
        )
        session.add(variant_rec)
    else:
        variant_rec.model_id = model_id
        variant_rec.version = version
        variant_rec.precision = precision
        variant_rec.compression_level = level
        variant_rec.top1_accuracy = top1_accuracy
        variant_rec.status = "ready"
        variant_rec.model_card_json = json.dumps(metadata, ensure_ascii=False)
    variant_rec.task_group = task_group
    variant_rec.map50 = metadata.get("map50")
    variant_rec.map50_95 = metadata.get("map50_95")
    variant_rec.peak_ram_mb = metadata.get("peak_ram_mb")
    variant_rec.latency_ms_p50 = metadata.get("latency_ms_p50")
    variant_rec.latency_ms_p95 = metadata.get("latency_ms_p95")
    variant_rec.fps = metadata.get("fps")
    session.flush()

    artifact_rec = session.execute(
        select(ModelArtifactRecord).where(ModelArtifactRecord.variant_id == variant_id)
    ).scalar_one_or_none()
    if not artifact_rec:
        artifact_rec = ModelArtifactRecord(
            variant_id=variant_id,
            filename=filename,
            storage_path=storage_path,
            size_bytes=size_bytes,
            sha256=whole_sha256,
            chunk_size=CHUNK_SIZE,
            chunk_count=len(chunks),
            source_provider=source_provider,
            source_model_id=source_model_id,
            source_revision=source_revision,
            source_format=source_format,
            distribution_format=distribution_format,
        )
        session.add(artifact_rec)
    else:
        artifact_rec.filename = filename
        artifact_rec.storage_path = storage_path
        artifact_rec.size_bytes = size_bytes
        artifact_rec.sha256 = whole_sha256
        artifact_rec.chunk_size = CHUNK_SIZE
        artifact_rec.chunk_count = len(chunks)
        artifact_rec.source_provider = source_provider
        artifact_rec.source_model_id = source_model_id
        artifact_rec.source_revision = source_revision
        artifact_rec.source_format = source_format
        artifact_rec.distribution_format = distribution_format

    session.query(ModelChunkRecord).filter_by(variant_id=variant_id).delete()
    for c in chunks:
        session.add(
            ModelChunkRecord(
                variant_id=variant_id,
                chunk_index=c["index"],
                offset_bytes=c["offset"],
                size_bytes=c["size_bytes"],
                sha256=c["sha256"],
            )
        )
    session.commit()

    # 2. Cập nhật file registry/model_registry.json nguyên tử
    _update_registry_json(
        registry_path=settings.registry_path,
        entry={
            "variant_id": variant_id,
            "model_id": model_id,
            "version": version,
            "task_id": task_id,
            "task_group": task_group,
            "file": filename,
            "storage_path": storage_path,
            "base_model": model_id,
            "family": family,
            "level": level,
            "precision": precision,
            "size_bytes": size_bytes,
            "sha256": whole_sha256,
            "chunk_size": CHUNK_SIZE,
            "chunk_count": len(chunks),
            "source_provider": source_provider,
            "source_model_id": source_model_id,
            "source_revision": source_revision,
            "source_format": source_format,
            "distribution_format": distribution_format,
            "top1_accuracy": top1_accuracy,
            "input_shape": metadata.get("input_shape", [1, 3, 224, 224]),
            "input_dtype": metadata.get("input_dtype", "float"),
            "normalize": metadata.get(
                "normalize",
                {
                    "mean": [0.485, 0.456, 0.406],
                    "std": [0.229, 0.224, 0.225],
                },
            ),
            "status": "ready",
            "chunks": chunks,
        },
    )

    logger.info(
        "Đã tiếp nhận model %s (%s, %d bytes, %d chunks) vào CSDL và Registry",
        variant_id,
        filename,
        size_bytes,
        len(chunks),
    )

    return {
        "status": "success",
        "variant_id": variant_id,
        "filename": filename,
        "storage_path": storage_path,
        "size_bytes": size_bytes,
        "sha256": whole_sha256,
        "chunk_count": len(chunks),
        "onnx_info": onnx_info,
    }


def _update_registry_json(registry_path: Path, entry: dict[str, Any]) -> None:
    """Cập nhật bản ghi variant vào model_registry.json một cách nguyên tử."""
    data: dict[str, Any] = {"schema_version": "1.0", "chunk_size": CHUNK_SIZE, "variants": []}
    if registry_path.is_file():
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    variants: list[dict[str, Any]] = data.get("variants", [])
    vid = entry["variant_id"]
    new_variants = [v for v in variants if v.get("variant_id") != vid]
    new_variants.append(entry)
    data["variants"] = new_variants
    data["generated_at"] = datetime.now(timezone.utc).isoformat()

    write_registry_atomic(registry_path, data)
