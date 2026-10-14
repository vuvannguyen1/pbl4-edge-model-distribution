"""Kho model trên client: metadata, trạng thái partial/complete/invalid, kiểm hash và runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from client.model_cache import (
    STATUS_COMPLETE,
    STATUS_INVALID,
    STATUS_PARTIAL,
    ModelCache,
    ModelInvalid,
    read_meta,
    save_active,
    verified_variants,
)


def make_variant(variant_id: str = "tiny_fp32", size: int = 3000, top1: float = 0.7) -> tuple:
    data = os.urandom(size)
    variant = {
        "id": variant_id,
        "file": f"{variant_id}.onnx",
        "size_bytes": size,
        "sha256": hashlib.sha256(data).hexdigest(),
        "chunk_size": 1048576,
        "chunk_count": 1,
        "top1_accuracy": top1,
        "input_shape": [1, 3, 224, 224],
        "input_dtype": "float",
        "normalize": {"mean": [0.5, 0.5, 0.5], "std": [0.2, 0.2, 0.2]},
    }
    return variant, data


class Opener:
    """Giả ONNX Runtime: đếm số lần mở, có thể cho lỗi."""

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def __call__(self, path: Path) -> None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("INVALID_PROTOBUF")


def cache_with(tmp_path: Path, opener: Opener | None = None, **env: Any) -> ModelCache:
    env.setdefault("runtime", "onnxruntime 1.0")
    env.setdefault("cpu_arch", "AMD64")
    return ModelCache(tmp_path, opener=opener or Opener(), **env)


def install(cache: ModelCache, variant: dict, data: bytes) -> Path:
    final, _ = cache.paths(variant)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(data)
    return final


def status_of(cache: ModelCache, variant_id: str) -> str:
    meta = read_meta(cache.meta_path(variant_id))
    assert meta is not None
    return meta["status"]


def test_complete_writes_full_metadata(tmp_path: Path) -> None:
    cache = cache_with(tmp_path)
    variant, data = make_variant()
    install(cache, variant, data)
    cache.complete(variant)

    meta = json.loads(cache.meta_path(variant["id"]).read_text(encoding="utf-8"))
    assert meta["status"] == STATUS_COMPLETE
    assert meta["task"] == "image_classification"
    assert meta["version"] == f"sha256:{variant['sha256'][:12]}"
    assert meta["runtime"] == "onnxruntime 1.0" and meta["cpu_arch"] == "AMD64"
    assert meta["variant"] == variant  # tên file, kích thước, hash, input shape, dtype, normalize
    assert set(cache.valid_models()) == {variant["id"]}


def test_partial_is_not_used(tmp_path: Path) -> None:
    cache = cache_with(tmp_path)
    variant, data = make_variant()
    install(cache, variant, data)  # file đủ nhưng chưa xác nhận xong
    cache.mark_partial(variant)
    assert cache.valid_models() == {}
    with pytest.raises(LookupError, match="partial"):
        cache.find_local_model()


def test_tampered_file_is_marked_invalid(tmp_path: Path) -> None:
    cache = cache_with(tmp_path)
    variant, data = make_variant()
    final = install(cache, variant, data)
    cache.complete(variant)
    final.write_bytes(b"\x00" + data[1:])  # cùng kích thước, sai hash

    assert cache.valid_models() == {}
    assert status_of(cache, variant["id"]) == STATUS_INVALID
    with pytest.raises(LookupError, match="invalid.*SHA-256"):
        cache.find_local_model()
    # sửa lại file đúng cũng không tự thành complete: phải tải lại và kiểm lại
    final.write_bytes(data)
    assert cache.valid_models() == {}
    cache.complete(variant)
    assert set(cache.valid_models()) == {variant["id"]}


def test_wrong_size_or_missing_file_is_invalid(tmp_path: Path) -> None:
    cache = cache_with(tmp_path)
    variant, data = make_variant()
    final = install(cache, variant, data)
    cache.complete(variant)
    final.write_bytes(data[:-1])
    assert cache.get(variant["id"]) is None
    assert "kích thước" in (read_meta(cache.meta_path(variant["id"])) or {})["reason"]

    other, other_data = make_variant("other_fp32")
    install(cache, other, other_data)
    cache.complete(other)
    cache.paths(other)[0].unlink()
    assert cache.get(other["id"]) is None
    assert status_of(cache, other["id"]) == STATUS_INVALID


def test_runtime_cannot_open_marks_invalid(tmp_path: Path) -> None:
    cache = cache_with(tmp_path, Opener(fail=True))
    variant, data = make_variant()
    install(cache, variant, data)
    with pytest.raises(ModelInvalid, match="ONNX Runtime không mở được"):
        cache.complete(variant)
    assert status_of(cache, variant["id"]) == STATUS_INVALID
    assert cache.valid_models() == {}


def test_runtime_change_reopens_once(tmp_path: Path) -> None:
    variant, data = make_variant()
    first = cache_with(tmp_path)
    install(first, variant, data)
    first.complete(variant)

    same = Opener()
    assert set(cache_with(tmp_path, same).valid_models()) == {variant["id"]}
    assert same.calls == 0, "runtime không đổi thì không mở lại"

    upgraded = Opener()
    cache = cache_with(tmp_path, upgraded, runtime="onnxruntime 2.0")
    assert set(cache.valid_models()) == {variant["id"]}
    assert upgraded.calls == 1
    assert read_meta(cache.meta_path(variant["id"]))["runtime"] == "onnxruntime 2.0"
    cache.valid_models()
    assert upgraded.calls == 1, "đã ghi runtime mới, không mở lại lần nữa"

    broken = cache_with(tmp_path, Opener(fail=True), runtime="onnxruntime 3.0")
    assert broken.valid_models() == {}
    assert status_of(broken, variant["id"]) == STATUS_INVALID


def test_other_task_is_skipped_not_invalid(tmp_path: Path) -> None:
    variant, data = make_variant()
    cache = cache_with(tmp_path)
    install(cache, variant, data)
    cache.complete(variant)
    detection = cache_with(tmp_path, task="object_detection")
    assert detection.valid_models() == {}
    assert status_of(cache, variant["id"]) == STATUS_COMPLETE


def test_legacy_metadata_is_accepted(tmp_path: Path) -> None:
    """variant.json kiểu cũ (chỉ khối variant) vẫn dùng được nếu hash khớp."""
    variant, data = make_variant()
    cache = cache_with(tmp_path)
    install(cache, variant, data)
    cache.meta_path(variant["id"]).write_text(json.dumps(variant), encoding="utf-8")
    assert set(verified_variants(tmp_path)) == {variant["id"]}
    assert set(cache.valid_models()) == {variant["id"]}  # mở thử 1 lần rồi ghi dạng mới
    assert read_meta(cache.meta_path(variant["id"]))["runtime"] == "onnxruntime 1.0"


def test_find_local_model_prefers_requested_then_active(tmp_path: Path) -> None:
    cache = cache_with(tmp_path)
    for variant_id, top1 in (("small_int8", 0.6), ("big_fp32", 0.8)):
        variant, data = make_variant(variant_id, top1=top1)
        install(cache, variant, data)
        cache.complete(variant)
    assert cache.find_local_model()[0]["id"] == "big_fp32"
    save_active(tmp_path, "small_int8")
    assert cache.find_local_model()[0]["id"] == "small_int8"
    assert cache.find_local_model("big_fp32")[0]["id"] == "big_fp32"
    with pytest.raises(LookupError, match="khong_co"):
        cache.find_local_model("khong_co")


def test_entries_lists_every_status(tmp_path: Path) -> None:
    cache = cache_with(tmp_path)
    a, a_data = make_variant("a_fp32")
    b, _ = make_variant("b_fp32")
    install(cache, a, a_data)
    cache.complete(a)
    cache.mark_partial(b)
    statuses = {e.variant_id: e.status for e in cache.entries()}
    assert statuses == {"a_fp32": STATUS_COMPLETE, "b_fp32": STATUS_PARTIAL}
