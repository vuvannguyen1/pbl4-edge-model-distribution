"""API chỉ đọc cho dashboard (`/admin-api/*`) và các trường quan sát mới trong trạng thái."""

from __future__ import annotations

import json

from conftest import load_example
from fastapi.testclient import TestClient

from server.admin_api import parse_log_lines, read_jsonl_newest


def register(client: TestClient, device_id: str = "phoneB", **overrides) -> dict:
    body = {**load_example("01"), "device_id": device_id, **overrides}
    resp = client.post("/api/register", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def request_model(client: TestClient, device_id: str = "phoneB", headers=None, **overrides):
    body = {**load_example("03"), "device_id": device_id, **overrides}
    return client.post("/api/request", json=body, headers=headers)


def have(client: TestClient, request_id: str, chunks: list[int], **overrides):
    body = {
        **load_example("05"),
        "request_id": request_id,
        "chunks": chunks,
        "complete": False,
        **overrides,
    }
    return client.post("/api/have", json=body)


# ---------------------------------------------------------------- trang tĩnh


def test_root_redirects_to_dashboard(client: TestClient) -> None:
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/ui/"


def test_dashboard_page_served(client: TestClient) -> None:
    resp = client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert client.get("/ui/shared/css/tokens.css").status_code == 200
    # trình duyệt phải hỏi lại server, không dùng JS cũ đã cache sau khi cập nhật code
    for path in ("/ui/", "/ui/js/app.js", "/ui/shared/js/ui.js"):
        assert client.get(path).headers["cache-control"] == "no-cache", path


# ---------------------------------------------------------------- tổng quan


def test_summary_empty_server(client: TestClient) -> None:
    body = client.get("/admin-api/summary").json()
    assert body["devices"] == {"registered": 0, "active": 0, "sharing_peers": 0}
    assert body["requests"]["total"] == 0
    assert body["variants"]["total"] == 4
    assert body["server"]["selector_version"] == "rule-v1"
    assert body["chunks_actual"]["reports"] == 0


def test_summary_counts_devices_and_requests(client: TestClient) -> None:
    register(client)
    request_model(client, bandwidth_kbps=600)
    body = client.get("/admin-api/summary").json()
    assert body["devices"]["registered"] == 1
    assert body["devices"]["active"] == 1
    assert body["requests"]["by_status"] == {"downloading": 1}
    assert body["chunks_planned"]["server"] > 0


# ---------------------------------------------------------------- thiết bị


def test_devices_last_seen_and_complete_variants(client: TestClient) -> None:
    register(client, peer_port=9000)
    first = request_model(client, bandwidth_kbps=600).json()
    n = first["variant"]["chunk_count"]
    have(client, first["request_id"], list(range(n)), complete=True)

    body = client.get("/admin-api/devices").json()
    (device,) = body["devices"]
    assert device["device_id"] == "phoneB"
    assert device["last_seen"] is not None
    assert device["last_seen_age_s"] < 5
    assert device["complete_variants"] == [first["variant"]["id"]]
    assert device["sharing"] is True
    assert device["request_count"] == 1


# ---------------------------------------------------------------- Registry


def test_variants_list_and_detail(client: TestClient) -> None:
    rows = client.get("/admin-api/variants").json()["variants"]
    assert [r["variant_id"] for r in rows] == sorted(r["variant_id"] for r in rows)
    assert all(r["distributable"] for r in rows)

    detail = client.get("/admin-api/variants/mobilenetv2_int8").json()
    assert detail["model_card"]["variant_id"] == "mobilenetv2_int8"
    assert len(detail["chunks"]) == detail["chunk_count"]


def test_variant_detail_unknown(client: TestClient) -> None:
    resp = client.get("/admin-api/variants/khong_co")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "UNKNOWN_VARIANT"


# ---------------------------------------------------------------- request


def test_request_records_decision_and_progress(client: TestClient) -> None:
    register(client)
    body = request_model(client, bandwidth_kbps=600, deadline_s=90).json()
    rid = body["request_id"]
    have(
        client,
        rid,
        [0, 1],
        bandwidth_kbps=750,
        bad_chunks=[{"index": 2, "source_url": "http://10.0.0.5:9000/chunks/x/2"}],
    )

    detail = client.get(f"/admin-api/requests/{rid}").json()
    assert detail["requested_bandwidth_kbps"] == 600
    assert detail["deadline_s"] == 90
    assert detail["source"] == "client"
    assert detail["status"] == "downloading"
    assert detail["received_count"] == 2
    assert detail["bad_count"] == 1
    assert detail["server_bandwidth_kbps"] == 750
    assert any("Qua bộ lọc cứng" in line for line in detail["decision_log"])
    received = [p["index"] for p in detail["plan"] if p["received"]]
    assert received == [0, 1]
    assert [p["index"] for p in detail["plan"] if p["bad"]] == [2]

    listing = client.get("/admin-api/requests").json()["requests"]
    assert listing[0]["request_id"] == rid


def test_request_from_dashboard_is_marked_web(client: TestClient) -> None:
    register(client)
    rid = request_model(client, headers={"X-PBL4-Source": "web-ui"}).json()["request_id"]
    detail = client.get(f"/admin-api/requests/{rid}").json()
    assert detail["source"] == "web"
    assert detail["status"] == "planned"


def test_request_detail_unknown(client: TestClient) -> None:
    assert client.get("/admin-api/requests/r-9999").status_code == 404


# ---------------------------------------------------------------- feedback


def test_feedback_metrics_and_manual(client: TestClient) -> None:
    register(client)
    body = request_model(client, bandwidth_kbps=600).json()
    metrics = {
        **load_example("07"),
        "request_id": body["request_id"],
        "variant_id": body["variant"]["id"],
    }
    assert client.post("/api/feedback", json=metrics).status_code == 200
    manual = {**load_example("10"), "variant_id": body["variant"]["id"]}
    assert client.post("/api/feedback", json=manual).status_code == 200

    m = client.get("/admin-api/feedback?kind=metrics").json()
    assert m["total"] == 1 and m["groups"][0]["variant_id"] == body["variant"]["id"]
    u = client.get("/admin-api/feedback?kind=manual").json()
    assert u["total"] == 1 and u["groups"][0]["variant_feedback_count"] == 1


def test_feedback_bad_kind(client: TestClient) -> None:
    resp = client.get("/admin-api/feedback?kind=khac")
    assert resp.status_code == 400


# ---------------------------------------------------------------- log và tiện ích


def test_logs_filter_by_request_id(client: TestClient) -> None:
    register(client)
    rid = request_model(client, bandwidth_kbps=600).json()["request_id"]
    body = client.get(f"/admin-api/logs?q={rid}").json()
    assert body["exists"] is True
    assert body["entries"] and all(e["request_id"] == rid for e in body["entries"])


def test_parse_log_lines_joins_traceback() -> None:
    lines = [
        "10:00:00 INFO    [r-0001] Xin model",
        "10:00:01 ERROR   Lỗi",
        "Traceback (most recent call last):",
        "  File x",
    ]
    entries = list(parse_log_lines(lines))
    assert [e["level"] for e in entries] == ["INFO", "ERROR"]
    assert entries[0]["request_id"] == "r-0001"
    assert entries[1]["message"].endswith("File x")


def test_read_jsonl_newest_skips_broken_lines(tmp_path) -> None:
    old = tmp_path / "feedback-20260101.jsonl"
    new = tmp_path / "feedback-20260102.jsonl"
    old.write_text(json.dumps({"n": 1}) + "\n", encoding="utf-8")
    new.write_text(
        json.dumps({"n": 2}) + "\nkhông phải json\n" + json.dumps({"n": 3}) + "\n",
        encoding="utf-8",
    )
    assert [r["n"] for r in read_jsonl_newest([old, new], 10)] == [3, 2, 1]
    assert [r["n"] for r in read_jsonl_newest([old, new], 2)] == [3, 2]
