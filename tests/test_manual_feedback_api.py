"""POST /api/feedback nhận đánh giá thủ công (có feedback_id): đánh giá, log, chống trùng."""

from __future__ import annotations

import json

import pytest
from conftest import load_example
from fastapi.testclient import TestClient
from validate_examples import schema_errors

from server.feedback import ManualFeedbackStore, VariantStats, evaluate_manual, final_score


def manual(**overrides) -> dict:
    return {**load_example("10"), **overrides}


def manual_lines(client: TestClient) -> list[dict]:
    settings = client.app.state.settings
    lines: list[dict] = []
    for path in sorted(settings.feedback_dir.glob("manual-feedback-*.jsonl")):
        lines += [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
    return lines


def test_manual_feedback_accepted_without_register(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Không cần register/request_id: client có thể gửi bù sau khi server khởi động lại."""
    fake_phobert(monkeypatch, 5)
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


def text_lines(client: TestClient) -> list[dict]:
    log = client.app.state.settings.user_feedback_log
    if not log.is_file():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]


def test_manual_feedback_comment_is_classified_once(client: TestClient) -> None:
    """Nhận xét kèm đánh giá (kể cả gửi bù) vào tab Nhận xét văn bản đúng một lần."""
    client.post("/api/feedback", json=manual(comment="Khá tệ", rating=2, correct=False))
    client.post("/api/feedback", json=manual(comment="Khá tệ", rating=2, correct=False))
    [record] = text_lines(client)
    assert record["feedback_text"] == "Khá tệ"
    assert record["device_id"] == manual()["device_id"]
    assert record["distributed_variant"] == manual()["variant_id"]
    assert 1 <= record["score"] <= 5 and record["label"]

    client.post("/api/feedback", json=manual(feedback_id="fb-0002", comment=None))
    client.post("/api/feedback", json=manual(feedback_id="fb-0003", comment=""))
    assert len(text_lines(client)) == 1  # không có nhận xét thì không ghi


def test_manual_feedback_survives_comment_classifier_error(client: TestClient, monkeypatch) -> None:
    def broken(*_a, **_k):
        raise OSError("đĩa đầy")

    monkeypatch.setattr("server.user_feedback_handler.record_user_feedback", broken)
    resp = client.post("/api/feedback", json=manual(comment="ổn"))
    assert resp.status_code == 200 and resp.json()["status"] == "accepted"
    assert len(manual_lines(client)) == 1


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


# ---------------------------------------------------------------- điểm tổng hợp


def fake_phobert(monkeypatch: pytest.MonkeyPatch, score: int) -> list[str]:
    calls: list[str] = []

    def classify(text: str) -> dict:
        calls.append(text)
        return {"score": score, "label": "x", "confidence": 0.9, "text": text, "engine": "neural"}

    monkeypatch.setattr("server.service.classify_user_feedback", classify)
    return calls


def test_final_score_uses_discrete_phobert_score() -> None:
    assert final_score(4, 5) == 4.8
    assert final_score(5, 1) == 1.8  # bình luận chiếm 80 %
    assert final_score(1, 5) == 4.2
    assert final_score(3, None) == 3.0  # không có bình luận: bằng số sao


def test_comment_decides_verdict_and_is_stored(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_phobert(monkeypatch, 1)
    body = client.post("/api/feedback", json=manual(rating=5, comment="sai hết")).json()
    ev = body["evaluation"]
    assert schema_errors("ManualFeedbackResponse", body) == []
    assert ev["final_score"] == 1.8 and ev["comment_score"] == 1
    assert ev["comment_engine"] == "neural"
    assert ev["verdict"] == "negative"  # 5 sao nhưng bình luận rất xấu
    assert "5 sao, bình luận 1/5, điểm 1.8" in ev["reason"]
    assert manual_lines(client)[0]["evaluation"]["final_score"] == 1.8


def test_no_comment_scores_stars_without_phobert(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = fake_phobert(monkeypatch, 1)
    ev = client.post("/api/feedback", json=manual(rating=4, comment=None)).json()["evaluation"]
    assert calls == []
    assert ev["final_score"] == 4.0 and ev["comment_score"] is None
    assert ev["verdict"] == "positive"


def test_duplicate_is_not_scored_again(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_phobert(monkeypatch, 5)
    first = client.post("/api/feedback", json=manual(rating=4)).json()
    again = client.post("/api/feedback", json=manual(rating=4)).json()
    assert len(calls) == 1
    assert again["status"] == "duplicate"
    assert again["evaluation"]["final_score"] == first["evaluation"]["final_score"] == 4.8


def test_rating_without_correct_or_detection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Trang /app: chấm sao không cần đúng/sai, không cần khung hình nào."""
    fake_phobert(monkeypatch, 4)
    body = manual(rating=5, prediction=None, confidence=None, request_id=None, detected_at=None)
    del body["correct"]
    assert schema_errors("ManualFeedbackRequest", body) == []
    resp = client.post("/api/feedback", json=body)
    assert resp.status_code == 200, resp.text
    assert schema_errors("ManualFeedbackResponse", resp.json()) == []
    ev = resp.json()["evaluation"]
    assert ev["final_score"] == 4.2 and ev["verdict"] == "positive"
    assert ev["variant_correct_rate"] is None and ev["variant_avg_score"] == 4.2


def test_low_average_score_needs_review(monkeypatch: pytest.MonkeyPatch) -> None:
    stats = VariantStats()
    for _ in range(5):
        stats = stats.add(None, 5, 1.8)  # 5 sao nhưng bình luận rất xấu
    result = evaluate_manual(variant_id="v", correct=None, rating=5, stats=stats, comment_score=1)
    assert result["needs_review"] is True and result["variant_avg_score"] == 1.8
    assert "cần xem lại" in result["reason"]


def test_old_records_are_reloaded(tmp_path) -> None:
    """Bản ghi cũ có `correct`, không có điểm tổng hợp: thống kê vẫn đúng sau khi khởi động lại."""
    (tmp_path / "manual-feedback-20261103.jsonl").write_text(
        json.dumps({"feedback_id": "a", "variant_id": "v", "rating": 4, "correct": True}) + "\n"
        + json.dumps(
            {
                "feedback_id": "b",
                "variant_id": "v",
                "rating": 2,
                "correct": None,
                "evaluation": {"final_score": 3.0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    store = ManualFeedbackStore(tmp_path)
    stats = store._stats["v"]
    assert stats.count == 2 and stats.judged == 1 and stats.correct_rate == 1.0
    assert stats.avg_score == 3.5
