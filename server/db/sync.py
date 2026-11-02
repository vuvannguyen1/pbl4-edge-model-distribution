"""Đồng bộ dữ liệu từ registry/model_registry.json vào CSDL SQL."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from server.db.models import (
    ModelArtifactRecord,
    ModelChunkRecord,
    ModelFamilyRecord,
    ModelVariantRecord,
    TaskRecord,
)

logger = logging.getLogger("pbl4.server.db.sync")


def sync_registry_to_db(
    session: Session,
    registry_path: Path,
    cards_dir: Path | None = None,
) -> int:
    """Đọc registry JSON và nạp vào database. Trả về số variant đã đồng bộ."""
    if not registry_path.is_file():
        logger.warning("Không tìm thấy file registry tại %s", registry_path)
        return 0

    data = json.loads(registry_path.read_text(encoding="utf-8"))
    variants: list[dict[str, Any]] = data.get("variants", [])

    count = 0
    for v in variants:
        task_id = v.get("task_id", "image_classification")
        model_id = v.get("model_id", v.get("base_model", "unknown"))
        family = v.get("family", model_id)
        variant_id = v["variant_id"]

        # 1. Đảm bảo Task tồn tại
        task = session.get(TaskRecord, task_id)
        if not task:
            task = TaskRecord(
                task_id=task_id,
                name=task_id.replace("_", " ").title(),
                description=f"Tác vụ {task_id}",
            )
            session.add(task)
            session.flush()

        # 2. Đảm bảo Model Family tồn tại
        model = session.get(ModelFamilyRecord, model_id)
        if not model:
            model = ModelFamilyRecord(
                model_id=model_id,
                task_id=task_id,
                name=family,
                family=family,
                description=f"Họ mô hình {family}",
            )
            session.add(model)
            session.flush()

        # 3. Đọc model card json nếu có
        card_json: str | None = None
        if cards_dir and cards_dir.is_dir():
            card_file = cards_dir / f"{variant_id}.json"
            if card_file.is_file():
                card_json = card_file.read_text(encoding="utf-8")

        # 4. Tạo hoặc cập nhật ModelVariantRecord
        variant_rec = session.get(ModelVariantRecord, variant_id)
        if not variant_rec:
            variant_rec = ModelVariantRecord(
                variant_id=variant_id,
                model_id=model_id,
                version=str(v.get("version", "1.0")),
                precision=v.get("precision", "fp32"),
                compression_level=int(v.get("level", 0)),
                top1_accuracy=float(v.get("top1_accuracy", 0.0)),
                status=v.get("status", "ready"),
                model_card_json=card_json,
            )
            session.add(variant_rec)
        else:
            variant_rec.model_id = model_id
            variant_rec.version = str(v.get("version", "1.0"))
            variant_rec.precision = v.get("precision", "fp32")
            variant_rec.compression_level = int(v.get("level", 0))
            variant_rec.top1_accuracy = float(v.get("top1_accuracy", 0.0))
            variant_rec.status = v.get("status", "ready")
            if card_json:
                variant_rec.model_card_json = card_json

        session.flush()

        # 5. Cập nhật Artifact
        artifact_rec = session.execute(
            select(ModelArtifactRecord).where(ModelArtifactRecord.variant_id == variant_id)
        ).scalar_one_or_none()

        if not artifact_rec:
            artifact_rec = ModelArtifactRecord(
                variant_id=variant_id,
                filename=v.get("file", f"{variant_id}.onnx"),
                storage_path=v.get("storage_path", ""),
                size_bytes=int(v["size_bytes"]),
                sha256=v["sha256"],
                chunk_size=int(v.get("chunk_size", 1048576)),
                chunk_count=int(v["chunk_count"]),
            )
            session.add(artifact_rec)
        else:
            artifact_rec.filename = v.get("file", f"{variant_id}.onnx")
            artifact_rec.storage_path = v.get("storage_path", "")
            artifact_rec.size_bytes = int(v["size_bytes"])
            artifact_rec.sha256 = v["sha256"]
            artifact_rec.chunk_size = int(v.get("chunk_size", 1048576))
            artifact_rec.chunk_count = int(v["chunk_count"])

        # 6. Cập nhật Chunks
        session.query(ModelChunkRecord).filter_by(variant_id=variant_id).delete()
        chunks = v.get("chunks", [])
        for chunk in chunks:
            chunk_rec = ModelChunkRecord(
                variant_id=variant_id,
                chunk_index=int(chunk["index"]),
                offset_bytes=int(chunk["offset"]),
                size_bytes=int(chunk["size_bytes"]),
                sha256=str(chunk["sha256"]),
            )
            session.add(chunk_rec)

        count += 1

    session.commit()
    logger.info("Đã đồng bộ %d variant từ %s vào CSDL SQL", count, registry_path)
    return count

