"""Đọc registry/model_registry.json và ánh xạ sang khối Variant của protocol.

Registry là danh mục động: server không biết trước có những variant nào.
Chỉ bản ghi `status == "ready"`, đủ metadata cho khối `Variant` và có
`storage_path` nằm bên trong Model Store mới được xem là có thể phân phối.
File Registry được tự nạp lại khi thời điểm sửa đổi thay đổi, nên chạy lại
`tools/build_registry.py` không cần khởi động lại server.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from server.config import CHUNK_SIZE
from server.schemas import Variant

logger = logging.getLogger("pbl4.server.registry")

READY = "ready"


@dataclass(frozen=True)
class ChunkInfo:
    index: int
    offset: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RegistryVariant:
    """Một bản ghi Registry đã kiểm tra, sẵn sàng phân phối."""

    variant_id: str
    task_id: str
    storage_path: str  # nội bộ, không bao giờ trả cho client
    size_bytes: int
    sha256: str
    chunk_count: int
    chunks: tuple[ChunkInfo, ...]
    top1_accuracy: float
    protocol_variant: Variant  # đúng 13 trường của $defs/Variant

    @property
    def file(self) -> str:
        return self.protocol_variant.file


def to_protocol_variant(entry: dict[str, Any]) -> Variant:
    """Ánh xạ bản ghi Registry sang khối Variant (đúng 13 trường, không thêm)."""
    return Variant(
        id=entry["variant_id"],
        file=entry["file"],
        base_model=entry["base_model"],
        level=entry["level"],
        precision=entry["precision"],
        size_bytes=entry["size_bytes"],
        sha256=entry["sha256"],
        chunk_size=entry["chunk_size"],
        chunk_count=entry["chunk_count"],
        top1_accuracy=entry["top1_accuracy"],
        input_shape=entry["input_shape"],
        input_dtype=entry["input_dtype"],
        normalize=entry["normalize"],
    )


def _check_chunks(entry: dict[str, Any]) -> tuple[ChunkInfo, ...]:
    chunks = tuple(
        ChunkInfo(
            index=int(c["index"]),
            offset=int(c["offset"]),
            size_bytes=int(c["size_bytes"]),
            sha256=str(c["sha256"]),
        )
        for c in entry["chunks"]
    )
    if len(chunks) != entry["chunk_count"]:
        raise ValueError(f"chunk_count={entry['chunk_count']} nhưng có {len(chunks)} chunk")
    if entry["chunk_size"] != CHUNK_SIZE:
        raise ValueError(f"chunk_size={entry['chunk_size']}, protocol cần {CHUNK_SIZE}")
    total = 0
    for expected_index, chunk in enumerate(chunks):
        if chunk.index != expected_index or chunk.offset != expected_index * CHUNK_SIZE:
            raise ValueError(f"chunk {expected_index} sai index/offset")
        if not 1 <= chunk.size_bytes <= CHUNK_SIZE:
            raise ValueError(f"chunk {expected_index} có size_bytes={chunk.size_bytes}")
        total += chunk.size_bytes
    if total != entry["size_bytes"]:
        raise ValueError(f"tổng kích thước chunk {total} khác size_bytes {entry['size_bytes']}")
    return chunks


def safe_storage_path(model_store: Path, storage_path: str) -> Path | None:
    """Trả đường dẫn thật bên trong Model Store, hoặc None nếu có dấu hiệu path traversal."""
    if not storage_path or "\\" in storage_path:
        return None
    rel = PurePosixPath(storage_path)
    if rel.is_absolute() or ".." in rel.parts or ":" in rel.parts[0]:
        return None
    store = model_store.resolve()
    candidate = store.joinpath(*rel.parts).resolve()
    if not candidate.is_relative_to(store):
        return None
    return candidate


class Registry:
    """Danh mục variant đọc từ file JSON, an toàn khi nhiều luồng cùng đọc."""

    def __init__(self, registry_path: Path, model_store: Path) -> None:
        self.registry_path = registry_path
        self.model_store = model_store
        self._lock = threading.Lock()
        self._mtime_ns: int | None = None
        self._ready: dict[str, RegistryVariant] = {}
        self._all_ids: set[str] = set()
        self.load()

    # ------------------------------------------------------------ nạp file

    def load(self) -> None:
        """Đọc lại toàn bộ Registry. Bản ghi lỗi bị bỏ qua và ghi log."""
        try:
            stat = self.registry_path.stat()
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Không đọc được Registry %s: %s", self.registry_path, exc)
            with self._lock:
                self._ready, self._all_ids, self._mtime_ns = {}, set(), None
            return

        ready: dict[str, RegistryVariant] = {}
        all_ids: set[str] = set()
        for entry in raw.get("variants", []):
            variant_id = entry.get("variant_id")
            if not isinstance(variant_id, str):
                logger.warning("Bỏ qua bản ghi Registry không có variant_id")
                continue
            all_ids.add(variant_id)
            status = entry.get("status")
            if status != READY:
                logger.info("Registry: %s ở trạng thái %s, không phân phối", variant_id, status)
                continue
            try:
                chunks = _check_chunks(entry)
                protocol_variant = to_protocol_variant(entry)
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                logger.warning(
                    "Registry: %s thiếu/sai metadata, không phân phối: %s", variant_id, exc
                )
                continue
            if safe_storage_path(self.model_store, entry.get("storage_path", "")) is None:
                logger.warning("Registry: %s có storage_path không an toàn, bỏ qua", variant_id)
                continue
            ready[variant_id] = RegistryVariant(
                variant_id=variant_id,
                task_id=entry["task_id"],
                storage_path=entry["storage_path"],
                size_bytes=entry["size_bytes"],
                sha256=entry["sha256"],
                chunk_count=entry["chunk_count"],
                chunks=chunks,
                top1_accuracy=float(entry["top1_accuracy"]),
                protocol_variant=protocol_variant,
            )

        with self._lock:
            self._ready = ready
            self._all_ids = all_ids
            self._mtime_ns = stat.st_mtime_ns
        logger.info(
            "Đã nạp Registry: %d variant, %d ready (%s)",
            len(all_ids),
            len(ready),
            ", ".join(sorted(ready)) or "không có",
        )

    def refresh_if_changed(self) -> None:
        """Nạp lại nếu file Registry đã được sinh lại kể từ lần đọc trước."""
        try:
            mtime_ns = self.registry_path.stat().st_mtime_ns
        except OSError:
            return
        if mtime_ns != self._mtime_ns:
            self.load()

    # ------------------------------------------------------------ truy vấn

    def ready_variants(self, task_id: str | None = None) -> list[RegistryVariant]:
        with self._lock:
            variants = list(self._ready.values())
        if task_id is not None:
            variants = [v for v in variants if v.task_id == task_id]
        return sorted(variants, key=lambda v: v.variant_id)

    def get_ready(self, variant_id: str) -> RegistryVariant | None:
        with self._lock:
            return self._ready.get(variant_id)

    def file_path(self, variant: RegistryVariant) -> Path | None:
        return safe_storage_path(self.model_store, variant.storage_path)
