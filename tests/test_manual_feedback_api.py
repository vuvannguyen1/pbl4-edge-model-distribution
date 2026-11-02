"""POST /api/feedback nhận đánh giá thủ công (có feedback_id): đánh giá, log, chống trùng."""

from __future__ import annotations

import json

import pytest
from conftest import load_example
from fastapi.testclient import TestClient
from validate_examples import schema_errors

from server.feedback import VariantStats, evaluate_manual


def manual(**overrides) -> dict:
    return {**load_example("10"), **overrides}


def manual_lines(client: TestClient) -> list[dict]:
    settings = client.app.state.settings
    lines: list[dict] = []
    for path in sorted(settings.feedback_dir.glob("manual-feedback-*.jsonl")):
        lines += [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    return lines


def test_manual_feedback_accepted_without_register(client: TestClient) -> None:
    """Không cần register/request_id: client có thể gửi bù sau khi server khởi động lại."""
    resp = client.post("/api/feedback", json=manual())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert schema_errors("ManualFeedbackResponse", body) == []
    assert body["status"] == "accepted" and body["feedback_id"] == "fb-0001"
    assert body["evaluation"]["verdict"] == "positive"
    assert body["evaluation"]["variant_feedback_count"] == 1

    [record] = manual_lines(client)
    assert record["feedback_id"] == "fb-0001"
    assert record["rating"] == 5 and record["correct"] is True
    assert record["device_class"] is None  # chưa đăng ký
    assert record["variant_ready"] is True
    assert record["processing_status"] == "evaluated"
    assert record["evaluation"] == body["evaluation"]
    assert record["received_at"].endswith("+00:00")
    # feedback số đo của agent ghi file riêng, không lẫn
    settings = client.app.state.settings
    assert not list(settings.feedback_dir.glob("feedback-*.jsonl"))


def test_manual_feedback_duplicate_is_not_written_twice(client: TestClient) -> None:
    first = client.post("/api/feedback", json=manual()).json()
    again = client.post("/api/feedback", json=manual(comment="gửi lại sau lỗi mạng")).json()
    assert schema_errors("ManualFeedbackResponse", again) == []
    assert again["status"] == "duplicate"
    assert again["evaluation"] == first["evaluation"]
    assert again["received_at"] == first["received_at"]
    assert len(manual_lines(client)) == 1


def test_manual_feedback_dedup_survives_server_restart(app_factory) -> None:
    with TestClient(app_factory()) as client:
        assert client.post("/api/feedback", json=manual()).json()["status"] == "accepted"
    with TestClient(app_factory()) as client:  # cùng data_dir, server mới
        assert client.post("/api/feedback", json=manual()).json()["status"] == "duplicate"
        body = client.post("/api/feedback", json=manual(feedback_id="fb-0002")).json()
        assert body["status"] == "accepted"
        assert body["evaluation"]["variant_feedback_count"] == 2  # thống kê đọc lại từ file
        assert len(manual_lines(client)) == 2


def test_manual_feedback_records_device_class_when_registered(client: TestClient) -> None:
    client.post("/api/register", json={**load_example("01"), "device_id": "phoneA"})
    client.post("/api/feedback", json=manual(correct=False, rating=2, expected_label="cat"))
    [record] = manual_lines(client)
    assert record["device_class"] == "android_arm64_4-8gb"
    assert record["evaluation"]["verdict"] == "negative"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(rating=6),
        lambda b: b.update(rating="5"),  # strict: không ép kiểu
        lambda b: b.update(correct="yes"),
        lambda b: b.update(feedback_id="có dấu"),
        lambda b: b.update(created_at="hôm nay"),
        lambda b: b.update(comment="x" * 1001),
        lambda b: b.update(extra=1),
        lambda b: b.pop("rating"),
    ],
)
def test_manual_feedback_invalid_is_400(client: TestClient, mutate) -> None:
    body = manual()
    mutate(body)
    resp = client.post("/api/feedback", json=body)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    assert manual_lines(client) == []


def test_evaluate_manual_verdicts_and_review() -> None:
    def verdict(correct: bool, rating: int) -> str:
        stats = VariantStats().add(correct, rating)
        return evaluate_manual(variant_id="v", correct=correct, rating=rating, stats=stats)[
            "verdict"
        ]

    assert verdict(True, 5) == "positive"
    assert verdict(True, 3) == "neutral"
    assert verdict(True, 2) == "negative"
    assert verdict(False, 5) == "negative"

    stats = VariantStats()
    for correct in (False, False, False, True, False):
        stats = stats.add(correct, 2)
    result = evaluate_manual(variant_id="v", correct=False, rating=2, stats=stats)
    assert result["needs_review"] is True
    assert result["variant_correct_rate"] == 0.2
    assert "cần xem lại" in result["reason"]
    few = evaluate_manual(
        variant_id="v", correct=False, rating=1, stats=VariantStats().add(False, 1)
    )
    assert few["needs_review"] is False  # chưa đủ 5 lượt
