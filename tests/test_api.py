"""Từng endpoint qua TestClient: response hợp lệ theo schema, lỗi đúng status và code."""

from __future__ import annotations

import hashlib
import json

import pytest
from conftest import load_example, requires_models
from fastapi.testclient import TestClient
from validate_examples import schema_errors


def register(client: TestClient, device_id: str = "phoneB", **overrides) -> dict:
    body = {**load_example("01"), "device_id": device_id, **overrides}
    resp = client.post("/api/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def request_model(client: TestClient, device_id: str = "phoneB", **overrides):
    body = {**load_example("03"), "device_id": device_id, **overrides}
    return client.post("/api/request", json=body)


def assert_error(resp, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert schema_errors("Error", body) == []
    assert body["error"]["code"] == code
    text = body["error"]["message"]
    assert "Traceback" not in text and "storage_path" not in text


# ---------------------------------------------------------------- register


def test_register_success(client: TestClient) -> None:
    body = register(client)
    assert schema_errors("RegisterResponse", body) == []
    assert body == load_example("02")


def test_register_again_updates(client: TestClient) -> None:
    register(client)
    body = register(client, ram_mb=16384, peer_port=None)
    assert body["device_class"] == "android_arm64_gt8gb"


def test_register_malformed_json_is_400(client: TestClient) -> None:
    resp = client.post(
        "/api/register", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert_error(resp, 400, "BAD_REQUEST")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.pop("ram_mb"),  # thiếu trường
        lambda b: b.update(ram_mb="6144"),  # sai kiểu
        lambda b: b.update(platform="symbian"),  # ngoài enum
        lambda b: b.update(device_id="tên có dấu"),  # sai pattern
        lambda b: b.update(peer_port=80),  # ngoài khoảng
        lambda b: b.update(cpu_arch="mips"),  # kiến trúc lạ
    ],
)
def test_register_invalid_body_is_400_not_422(client: TestClient, mutate) -> None:
    body = load_example("01")
    mutate(body)
    assert_error(client.post("/api/register", json=body), 400, "BAD_REQUEST")


def test_register_extra_field_is_400(client: TestClient) -> None:
    body = {**load_example("01"), "ip": "192.168.137.12"}
    assert_error(client.post("/api/register", json=body), 400, "BAD_REQUEST")


def test_register_version_mismatch_is_409(client: TestClient) -> None:
    body = {**load_example("01"), "protocol_version": "2.0"}
    assert_error(client.post("/api/register", json=body), 409, "VERSION_MISMATCH")


def test_register_non_object_is_400(client: TestClient) -> None:
    assert_error(client.post("/api/register", json=[1, 2]), 400, "BAD_REQUEST")


# ---------------------------------------------------------------- probe


@pytest.mark.parametrize("size_kb", [16, 256, 1024])
def test_probe_returns_exact_bytes(client: TestClient, size_kb: int) -> None:
    resp = client.get(f"/api/probe?size_kb={size_kb}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert int(resp.headers["content-length"]) == size_kb * 1024
    assert len(resp.content) == size_kb * 1024


def test_probe_default_size(client: TestClient) -> None:
    assert len(client.get("/api/probe").content) == 256 * 1024


@pytest.mark.parametrize("query", ["size_kb=15", "size_kb=1025", "size_kb=0", "size_kb=abc"])
def test_probe_out_of_range_is_400(client: TestClient, query: str) -> None:
    assert_error(client.get(f"/api/probe?{query}"), 400, "BAD_REQUEST")


# ---------------------------------------------------------------- request


def test_request_success_matches_schema(client: TestClient) -> None:
    register(client)
    resp = request_model(client, bandwidth_kbps=600)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert schema_errors("ModelResponse", body) == []
    assert body["request_id"] == "r-0001"
    assert body["variant"]["id"] == "mobilenetv2_int8"
    assert body["estimated_download_s"] == 48.7
    assert "600 kbps" in body["reason"] and "48.7 s" in body["reason"]
    plan = body["plan"]
    assert [p["index"] for p in plan] == list(range(body["variant"]["chunk_count"]))
    for item in plan:
        assert item["source"] == "server" and item["fallback_url"] is None
        assert item["url"] == f"http://testserver/api/chunks/mobilenetv2_int8/{item['index']}"
        assert item["offset"] == item["index"] * 1048576
    assert sum(p["size_bytes"] for p in plan) == body["variant"]["size_bytes"]
    assert "storage_path" not in resp.text and "CV/" not in resp.text


def test_request_ids_increase(client: TestClient) -> None:
    register(client)
    ids = [request_model(client, bandwidth_kbps=600).json()["request_id"] for _ in range(3)]
    assert ids == ["r-0001", "r-0002", "r-0003"]


def test_request_unknown_device_is_404(client: TestClient) -> None:
    assert_error(request_model(client, device_id="ghost"), 404, "UNKNOWN_DEVICE")


def test_request_unknown_preferred_variant_is_404(client: TestClient) -> None:
    register(client)
    resp = request_model(client, preferred_variant="khong_ton_tai")
    assert_error(resp, 404, "UNKNOWN_VARIANT")


def test_request_preferred_variant_bypasses_selector(client: TestClient) -> None:
    register(client)
    resp = request_model(client, bandwidth_kbps=100_000, preferred_variant="mobilenetv2_int8")
    body = resp.json()
    assert body["variant"]["id"] == "mobilenetv2_int8"
    assert len(body["plan"]) == body["variant"]["chunk_count"]


def test_request_bandwidth_too_low_is_422(client: TestClient) -> None:
    register(client)
    resp = request_model(client, bandwidth_kbps=100, deadline_s=60)
    assert_error(resp, 422, "NO_VARIANT_FITS")
    message = resp.json()["error"]["message"]
    assert "60 giây" in message and "100 kbps" in message


def test_request_extra_field_is_400(client: TestClient) -> None:
    register(client)
    assert_error(request_model(client, ip="1.2.3.4"), 400, "BAD_REQUEST")


@pytest.mark.parametrize(
    "overrides",
    [
        {"task": "object_detection"},
        {"bandwidth_kbps": 0},
        {"deadline_s": -1},
        {"have_variants": ["a", "a"]},
        {"preferred_variant": "Có-Dấu"},
    ],
)
def test_request_invalid_values_are_400(client: TestClient, overrides) -> None:
    register(client)
    assert_error(request_model(client, **overrides), 400, "BAD_REQUEST")


def test_request_uses_server_rate_limit_when_bandwidth_null(app_factory) -> None:
    with TestClient(app_factory(rate_limit_kbps=2000)) as client:
        register(client)
        body = request_model(client, bandwidth_kbps=None).json()
    assert body["variant"]["id"] == "mobilenetv2_fp32"
    assert body["estimated_download_s"] == 55.9


def test_request_estimate_null_when_bandwidth_unknown(client: TestClient) -> None:
    register(client)
    body = request_model(client, bandwidth_kbps=None).json()
    assert body["estimated_download_s"] is None
    assert schema_errors("ModelResponse", body) == []


def test_request_public_base_url(app_factory) -> None:
    with TestClient(app_factory(public_base_url="http://192.168.137.1:8000")) as client:
        register(client)
        body = request_model(client, bandwidth_kbps=600).json()
    assert body["plan"][0]["url"].startswith("http://192.168.137.1:8000/api/chunks/")


def test_have_variants_not_trusted_blindly(client: TestClient) -> None:
    register(client)
    body = request_model(client, bandwidth_kbps=600, have_variants=["mobilenetv2_int8"]).json()
    assert len(body["plan"]) == body["variant"]["chunk_count"]


def test_have_variants_trusted_after_complete(client: TestClient) -> None:
    register(client)
    first = request_model(client, bandwidth_kbps=600).json()
    n = first["variant"]["chunk_count"]
    have = {
        "device_id": "phoneB",
        "request_id": first["request_id"],
        "variant_id": "mobilenetv2_int8",
        "chunks": list(range(n)),
        "complete": True,
        "bandwidth_kbps": 600,
        "bad_chunks": [],
    }
    assert client.post("/api/have", json=have).status_code == 200
    second = request_model(client, bandwidth_kbps=600, have_variants=["mobilenetv2_int8"]).json()
    assert second["plan"] == []
    assert schema_errors("ModelResponse", second) == []


# ---------------------------------------------------------------- chunk


@requires_models
def test_chunk_bytes_match_registry(client: TestClient) -> None:
    register(client)
    body = request_model(client, bandwidth_kbps=600).json()
    whole = hashlib.sha256()
    for item in body["plan"]:
        resp = client.get(item["url"].replace("http://testserver", ""))
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"
        assert int(resp.headers["content-length"]) == item["size_bytes"]
        assert len(resp.content) == item["size_bytes"]
        assert hashlib.sha256(resp.content).hexdigest() == item["sha256"]
        whole.update(resp.content)
    assert whole.hexdigest() == body["variant"]["sha256"]
    last = body["plan"][-1]
    assert last["size_bytes"] < 1048576
    assert last["size_bytes"] == body["variant"]["size_bytes"] - last["offset"]


def test_chunk_from_fake_store(app_factory, fake_store) -> None:
    """Không cần .onnx thật: chunk cuối ngắn hơn đúng như Registry."""
    registry_path, store = fake_store([("tiny_fp32", 2 * 1048576 + 1234, 0.5)])
    with TestClient(app_factory(registry_path=registry_path, model_store=store)) as client:
        data = (store / "CV" / "tiny_fp32.onnx").read_bytes()
        sizes = []
        for index in range(3):
            resp = client.get(f"/api/chunks/tiny_fp32/{index}")
            assert resp.status_code == 200
            assert resp.content == data[index * 1048576 : (index + 1) * 1048576]
            sizes.append(len(resp.content))
        assert sizes == [1048576, 1048576, 1234]
        assert_error(client.get("/api/chunks/tiny_fp32/3"), 404, "CHUNK_OUT_OF_RANGE")


@pytest.mark.parametrize("index", [-1, 4, 999])
def test_chunk_out_of_range_is_404(client: TestClient, index: int) -> None:
    assert_error(client.get(f"/api/chunks/mobilenetv2_int8/{index}"), 404, "CHUNK_OUT_OF_RANGE")


def test_chunk_unknown_variant_is_404(client: TestClient) -> None:
    assert_error(client.get("/api/chunks/khong_ton_tai/0"), 404, "UNKNOWN_VARIANT")


def test_chunk_non_integer_index_is_400(client: TestClient) -> None:
    assert_error(client.get("/api/chunks/mobilenetv2_int8/abc"), 400, "BAD_REQUEST")


def test_chunk_not_ready_variant_is_404(app_factory, fake_store) -> None:
    from conftest import fake_entry

    entry = fake_entry("off_fp32", b"x" * 10, 0.5, "CV/off_fp32.onnx")
    entry["status"] = "disabled"
    registry_path, store = fake_store([("on_fp32", 100, 0.5)], extra_entries=[entry])
    (store / "CV" / "off_fp32.onnx").write_bytes(b"x" * 10)
    with TestClient(app_factory(registry_path=registry_path, model_store=store)) as client:
        assert_error(client.get("/api/chunks/off_fp32/0"), 404, "UNKNOWN_VARIANT")
        assert client.get("/api/chunks/on_fp32/0").status_code == 200


def test_chunk_missing_file_is_500_without_path(app_factory, fake_store) -> None:
    registry_path, store = fake_store([("gone_fp32", 100, 0.5)])
    (store / "CV" / "gone_fp32.onnx").unlink()
    with TestClient(app_factory(registry_path=registry_path, model_store=store)) as client:
        resp = client.get("/api/chunks/gone_fp32/0")
    assert_error(resp, 500, "INTERNAL")
    assert str(store) not in resp.text and "gone_fp32.onnx" not in resp.text


# ---------------------------------------------------------------- have


def _have(request_id: str, **overrides) -> dict:
    body = {
        **load_example("05"),
        "request_id": request_id,
        "chunks": [0, 1],
        "complete": False,
    }
    body.update(overrides)
    return body


def test_have_success(client: TestClient) -> None:
    register(client)
    rid = request_model(client, bandwidth_kbps=600).json()["request_id"]
    resp = client.post("/api/have", json=_have(rid))
    assert resp.status_code == 200
    assert schema_errors("HaveResponse", resp.json()) == []
    assert resp.json() == load_example("06")


def test_have_with_bad_chunks_and_null_bandwidth(client: TestClient) -> None:
    register(client)
    rid = request_model(client, bandwidth_kbps=600).json()["request_id"]
    body = _have(
        rid,
        bandwidth_kbps=None,
        bad_chunks=[{"index": 2, "source_url": "http://10.0.0.9:9000/chunks/mobilenetv2_int8/2"}],
    )
    assert client.post("/api/have", json=body).status_code == 200


def test_have_errors(client: TestClient) -> None:
    register(client)
    rid = request_model(client, bandwidth_kbps=600).json()["request_id"]
    assert_error(client.post("/api/have", json=_have(rid, device_id="ghost")), 404, "UNKNOWN_DEVICE")
    assert_error(client.post("/api/have", json=_have("r-9999")), 400, "BAD_REQUEST")
    assert_error(
        client.post("/api/have", json=_have(rid, variant_id="resnet50_fp32")), 404, "UNKNOWN_VARIANT"
    )
    assert_error(
        client.post("/api/have", json=_have(rid, chunks=[99])), 404, "CHUNK_OUT_OF_RANGE"
    )
    assert_error(client.post("/api/have", json=_have(rid, extra=1)), 400, "BAD_REQUEST")
    register(client, device_id="phoneC")
    assert_error(client.post("/api/have", json=_have(rid, device_id="phoneC")), 400, "BAD_REQUEST")


# ---------------------------------------------------------------- feedback


def _feedback(request_id: str, **overrides) -> dict:
    return {**load_example("07"), "request_id": request_id, **overrides}


def test_feedback_keep_and_jsonl(client: TestClient) -> None:
    register(client)
    rid = request_model(client, bandwidth_kbps=600).json()["request_id"]
    resp = client.post("/api/feedback", json=_feedback(rid))
    assert resp.status_code == 200
    body = resp.json()
    assert schema_errors("FeedbackResponse", body) == []
    assert body["action"] == "keep" and body["new_variant_id"] is None
    assert body["applies_to_class"] == "android_arm64_4-8gb"
    assert "18.5 ms" in body["reason"]

    settings = client.app.state.settings
    files = list(settings.feedback_dir.glob("feedback-*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    for key, value in load_example("07").items():
        if key != "request_id":
            assert record[key] == value
    assert record["request_id"] == rid
    assert record["device_class"] == "android_arm64_4-8gb"
    assert record["platform"] == "android"
    assert record["runtime"] == "onnxruntime 1.25.1"
    assert record["selector_version"] == "rule-v1"
    assert record["timestamp"].endswith("+00:00")


def test_feedback_switch_to_smaller_variant_and_remember_class(client: TestClient) -> None:
    register(client)
    first = request_model(client, bandwidth_kbps=100_000).json()
    assert first["variant"]["id"] == "resnet50_fp32"
    resp = client.post(
        "/api/feedback",
        json=_feedback(first["request_id"], variant_id="resnet50_fp32", latency_ms_median=900.0),
    )
    body = resp.json()
    assert schema_errors("FeedbackResponse", body) == []
    assert body["action"] == "switch"
    assert body["new_variant_id"] == "resnet50_int8"  # nhỏ hơn và top-1 cao nhất
    assert "900 ms" in body["reason"] and "500 ms" in body["reason"]

    # lần xin sau của máy cùng lớp ưu tiên variant đã ghi nhớ
    register(client, device_id="phoneD")
    again = request_model(client, device_id="phoneD", bandwidth_kbps=100_000).json()
    assert again["variant"]["id"] == "resnet50_int8"
    assert "ghi nhớ" in again["reason"]


def test_feedback_smallest_variant_keeps(client: TestClient) -> None:
    register(client)
    rid = request_model(client, bandwidth_kbps=600).json()["request_id"]
    body = client.post("/api/feedback", json=_feedback(rid, latency_ms_median=9999)).json()
    assert body["action"] == "keep"


def test_feedback_errors(client: TestClient) -> None:
    register(client)
    rid = request_model(client, bandwidth_kbps=600).json()["request_id"]
    assert_error(
        client.post("/api/feedback", json=_feedback(rid, device_id="ghost")), 404, "UNKNOWN_DEVICE"
    )
    assert_error(client.post("/api/feedback", json=_feedback("r-0404")), 400, "BAD_REQUEST")
    assert_error(
        client.post("/api/feedback", json=_feedback(rid, variant_id="resnet50_int8")),
        404,
        "UNKNOWN_VARIANT",
    )
    assert_error(
        client.post("/api/feedback", json=_feedback(rid, top1_confidence=1.5)), 400, "BAD_REQUEST"
    )
    body = _feedback(rid)
    body.pop("peak_ram_mb")
    assert_error(client.post("/api/feedback", json=body), 400, "BAD_REQUEST")


# ---------------------------------------------------------------- lỗi chung


def test_unknown_route_uses_envelope(client: TestClient) -> None:
    resp = client.get("/api/khong-co")
    assert resp.status_code == 404
    assert schema_errors("Error", resp.json()) == []


def test_wrong_method_uses_envelope(client: TestClient) -> None:
    resp = client.get("/api/register")
    assert resp.status_code == 405
    assert schema_errors("Error", resp.json()) == []


def test_unhandled_exception_is_500_without_trace(app_factory) -> None:
    from server.selector import ModelSelector

    class Exploding(ModelSelector):
        version = "boom"

        def select(self, context, candidates):
            raise RuntimeError("bí mật nội bộ C:\\server\\path")

    with TestClient(app_factory(selector=Exploding()), raise_server_exceptions=False) as client:
        register(client)
        resp = request_model(client, bandwidth_kbps=600)
    assert_error(resp, 500, "INTERNAL")
    assert "bí mật" not in resp.text and "RuntimeError" not in resp.text
