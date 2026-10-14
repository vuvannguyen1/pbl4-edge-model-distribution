"""
Unit test cho luồng User Feedback 5 nhãn và Auto-Retrain Selector.
"""
from __future__ import annotations

import json
from pathlib import Path
from fastapi.testclient import TestClient

from selector.feedback_classifier import classify_user_feedback
from selector.retrain import correct_variant


def test_nlp_feedback_classifier_labels() -> None:
    # 1: Tệ
    r1 = classify_user_feedback("Ứng dụng quá chậm, giật lag đơ máy không dùng được")
    assert r1["score"] == 1
    assert r1["label"] == "Tệ"

    # 2: Trung bình
    r2 = classify_user_feedback("Tải hơi lâu, nhận diện chưa chuẩn")
    assert r2["score"] == 2
    assert r2["label"] == "Trung bình"

    # 3: Khá
    r3 = classify_user_feedback("Dùng bình thường, tạm ổn")
    assert r3["score"] == 3
    assert r3["label"] == "Khá"

    # 4: Tốt
    r4 = classify_user_feedback("Chạy nhanh, ổn định, dùng tốt")
    assert r4["score"] == 4
    assert r4["label"] == "Tốt"

    # 5: Rất tốt
    r5 = classify_user_feedback("Tuyệt vời, siêu nhanh và quá mượt")
    assert r5["score"] == 5
    assert r5["label"] == "Rất tốt"


def test_user_feedback_endpoint_records_log(client: TestClient) -> None:
    payload = {
        "device_id": "test_device_1",
        "platform": "windows",
        "cpu_arch": "x86_64",
        "ram_mb": 4096,
        "bandwidth_kbps": 1500,
        "deadline_s": 60,
        "distributed_variant": "resnet50_fp32",
        "feedback_text": "Máy yếu mà tải model nặng quá, giật lag kinh khủng",
    }

    resp = client.post("/api/user-feedback", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    assert data["score"] == 1
    assert data["label"] == "Tệ"
    assert data["flag_for_retrain"] is True


def test_retrain_correction_logic() -> None:
    bad_record = {
        "distributed_variant": "resnet50_fp32",
        "ram_mb": 4096,
        "bandwidth_kbps": 1500,
        "feedback_text": "Tải quá lâu, máy bị giật lag đơ cứng",
        "score": 1,
    }
    corrected = correct_variant(bad_record)
    assert corrected == "mobilenetv2_int8"
