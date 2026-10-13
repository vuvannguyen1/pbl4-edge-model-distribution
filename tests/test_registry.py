"""Registry: ánh xạ khối Variant, hash chunk thật, lọc ready, chặn path traversal."""

from __future__ import annotations

import hashlib
import json
import os
import time

import pytest
from conftest import MODEL_STORE, REGISTRY_PATH, fake_entry, real_registry_variants, requires_models
from validate_examples import schema_errors

from server.registry import Registry, safe_storage_path
from server.selector import SelectionContext, apply_hard_filters

VARIANT_FIELDS = {
    "id",
    "file",
    "base_model",
    "level",
    "precision",
    "size_bytes",
    "sha256",
    "chunk_size",
    "chunk_count",
    "top1_accuracy",
    "input_shape",
    "input_dtype",
    "normalize",
}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry(REGISTRY_PATH, MODEL_STORE)


def test_real_registry_has_ready_variants(registry: Registry) -> None:
    ready_ids = {v["variant_id"] for v in real_registry_variants() if v["status"] == "ready"}
    assert ready_ids
    assert {v.variant_id for v in registry.ready_variants()} == ready_ids


def test_every_ready_variant_maps_to_valid_variant_block(registry: Registry) -> None:
    for variant in registry.ready_variants():
        block = variant.protocol_variant.model_dump(mode="json")
        assert set(block) == VARIANT_FIELDS, "đúng 13 trường, không thêm"
        assert schema_errors("Variant", block) == []
        assert block["id"] == variant.variant_id
        assert "storage_path" not in json.dumps(block)


@requires_models
def test_chunk_hashes_match_real_bytes(registry: Registry) -> None:
    for variant in registry.ready_variants():
        path = registry.file_path(variant)
        assert path is not None and path.stat().st_size == variant.size_bytes
        whole = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in variant.chunks:
                f.seek(chunk.offset)
                data = f.read(chunk.size_bytes)
                assert hashlib.sha256(data).hexdigest() == chunk.sha256, (
                    f"{variant.variant_id} chunk {chunk.index}"
                )
                whole.update(data)
        assert whole.hexdigest() == variant.sha256


@pytest.mark.parametrize(
    "bad_path",
    ["../registry/model_registry.json", "/etc/passwd", "C:/Windows/win.ini", "CV\\..\\..\\x", ""],
)
def test_safe_storage_path_blocks_traversal(tmp_path, bad_path: str) -> None:
    assert safe_storage_path(tmp_path, bad_path) is None


def test_safe_storage_path_accepts_inside(tmp_path) -> None:
    result = safe_storage_path(tmp_path, "CV/a.onnx")
    assert result == (tmp_path / "CV" / "a.onnx").resolve()


def test_only_ready_and_safe_variants_are_distributed(fake_store) -> None:
    data = os.urandom(1000)
    disabled = fake_entry("disabled_fp32", data, 0.9, "CV/disabled_fp32.onnx")
    disabled["status"] = "disabled"
    failed = fake_entry("failed_fp32", data, 0.9, "CV/failed_fp32.onnx")
    failed["status"] = "validation_failed"
    escape = fake_entry("escape_fp32", data, 0.9, "../outside.onnx")
    no_acc = fake_entry("noacc_fp32", data, 0.9, "CV/noacc_fp32.onnx")
    no_acc["top1_accuracy"] = None
    registry_path, store = fake_store(
        [("good_fp32", 3_000_000, 0.7)], extra_entries=[disabled, failed, escape, no_acc]
    )
    registry = Registry(registry_path, store)
    assert [v.variant_id for v in registry.ready_variants()] == ["good_fp32"]
    assert registry.get_ready("disabled_fp32") is None


def test_new_artifact_becomes_candidate_without_code_change(fake_store) -> None:
    """Danh mục động: thêm bản ghi vào Registry -> server nạp lại và xét nó là ứng viên."""
    registry_path, store = fake_store([("alpha_fp32", 2_000_000, 0.70)])
    registry = Registry(registry_path, store)
    assert [v.variant_id for v in registry.ready_variants()] == ["alpha_fp32"]

    data = os.urandom(1_500_000)
    (store / "CV" / "beta_int8.onnx").write_bytes(data)
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    raw["variants"].append(fake_entry("beta_int8", data, 0.80, "CV/beta_int8.onnx"))
    time.sleep(0.01)
    registry_path.write_text(json.dumps(raw), encoding="utf-8")
    os.utime(registry_path, ns=(time.time_ns(), time.time_ns() + 10_000_000))

    registry.refresh_if_changed()
    context = SelectionContext(
        device_id="d",
        device_class="linux_x64_gt8gb",
        platform="linux",
        cpu_arch="x86_64",
        ram_mb=16384,
        runtime="onnxruntime",
        task="image_classification",
        bandwidth_kbps=None,
        deadline_s=60,
        free_storage_mb=None,
    )
    candidates, _ = apply_hard_filters(registry.ready_variants(), context)
    assert {c.variant_id for c in candidates} == {"alpha_fp32", "beta_int8"}


def test_missing_registry_file_gives_empty_catalog(tmp_path) -> None:
    registry = Registry(tmp_path / "khong_co.json", tmp_path)
    assert registry.ready_variants() == []
