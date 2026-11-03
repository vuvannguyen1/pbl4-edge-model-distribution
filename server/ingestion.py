"""Quy trình tiếp nhận và kiểm định mô hình ONNX mới (Model Ingestion Pipeline).

Hỗ trợ tiếp nhận tệp .onnx mới, thẩm định đồ thị ONNX, chia chunk 1 MiB,
tính SHA-256, ghi vào CSDL SQL và cập nhật Model Registry động.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
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

logger = logging.getLogger("pbl4.server.ingestion")


class IngestionError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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
    if not filename.endswith(".onnx"):
        filename = f"{filename}.onnx"

    # Thư mục đích
    dest_dir = settings.model_store / task_group
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / filename
    storage_path = f"{task_group}/{filename}"

    # Lưu file
    dest_file.write_bytes(model_bytes)

    # Kiểm tra ONNX
    onnx_info = inspect_onnx_model(dest_file)

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

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = registry_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.move(temp_path, registry_path)
