"""Mọi gói JSON client tạo ra phải hợp lệ theo protocol.schema.json."""

from __future__ import annotations

import re

import pytest
from validate_examples import schema_errors

from client.api import (
    build_feedback,
    build_have,
    build_model_request,
    build_register,
    normalize_server_url,
)
from client.device_info import collect_device_info, default_device_id, sanitize_device_id


def test_register_payload_from_real_device_info(tmp_path) -> None:
    info = collect_device_info(tmp_path / "cache")
    assert info.platform in {"windows", "linux"}
    assert info.ram_mb >= 1
    assert info.runtime.startswith("onnxruntime ")
    assert info.free_storage_mb is None or info.free_storage_mb >= 0
    for port in (9000, None):
        payload = build_register(
            device_id=default_device_id(),
            platform=info.platform,
            cpu_arch=info.cpu_arch,
            ram_mb=info.ram_mb,
            runtime=info.runtime,
            peer_port=port,
        )
        assert schema_errors("RegisterRequest", payload) == []


def test_register_peer_port_zero_becomes_null() -> None:
    payload = build_register(
        device_id="a", platform="linux", cpu_arch="x86_64", ram_mb=1, runtime="r", peer_port=0
    )
    assert payload["peer_port"] is None


@pytest.mark.parametrize("bandwidth", [None, 600, 1234.5678])
@pytest.mark.parametrize("storage", [None, 0, 2048.5])
def test_model_request_payload(bandwidth, storage) -> None:
    payload = build_model_request(
        device_id="laptopA",
        bandwidth_kbps=bandwidth,
        deadline_s=60,
        free_storage_mb=storage,
        preferred_variant=None,
        have_variants=["mobilenetv2_int8", "mobilenetv2_int8"],
    )
    assert schema_errors("ModelRequest", payload) == []
    assert payload["have_variants"] == ["mobilenetv2_int8"]


def test_model_request_with_preferred() -> None:
    payload = build_model_request(
        device_id="laptopA",
        bandwidth_kbps=5000,
        deadline_s=30.5,
        free_storage_mb=100,
        preferred_variant="resnet50_int8",
        have_variants=[],
    )
    assert schema_errors("ModelRequest", payload) == []


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("bandwidth", [None, 1870.25])
def test_have_payload(complete, bandwidth) -> None:
    payload = build_have(
        device_id="laptopB",
        request_id="r-0002",
        variant_id="mobilenetv2_int8",
        chunks=[3, 1, 1, 0],
        complete=complete,
        bandwidth_kbps=bandwidth,
        bad_chunks=[(2, "http://192.168.137.12:9000/chunks/mobilenetv2_int8/2")],
    )
    assert schema_errors("HaveRequest", payload) == []
    assert payload["chunks"] == [0, 1, 3]


@pytest.mark.parametrize("ram", [None, 140.234])
@pytest.mark.parametrize("label,conf", [(None, None), ("backpack", 0.97123), ("x", 1.0000001)])
def test_feedback_payload(ram, label, conf) -> None:
    payload = build_feedback(
        device_id="laptopB",
        request_id="r-0002",
        variant_id="mobilenetv2_int8",
        download_ms=6840.123,
        chunks_from_peer=2,
        chunks_from_server=2,
        load_ms=210.44,
        latency_ms_median=18.512,
        runs=20,
        peak_ram_mb=ram,
        top1_label=label,
        top1_confidence=conf,
    )
    assert schema_errors("FeedbackRequest", payload) == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("DESKTOP-ABC123", "DESKTOP-ABC123"),
        ("máy của Vũ", "m-y-c-a-V"),
        ("a.b.local", "a-b-local"),
        ("x" * 40, "x" * 32),
        ("...", "device"),
    ],
)
def test_sanitize_device_id(name: str, expected: str) -> None:
    result = sanitize_device_id(name)
    assert result == expected
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", result)


def test_default_device_id_is_valid() -> None:
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", default_device_id())


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8000/", "http://127.0.0.1:8000"),
        ("http://localhost", "http://127.0.0.1"),
        ("http://192.168.137.1:8000", "http://192.168.137.1:8000"),
    ],
)
def test_normalize_server_url(url: str, expected: str) -> None:
    assert normalize_server_url(url) == expected
