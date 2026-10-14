"""
user_feedback_handler.py
Xử lý lưu trữ feedback của khách hàng và đánh dấu các lượt phân phối bị đánh giá thấp.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selector.feedback_classifier import classify_user_feedback

logger = logging.getLogger("server.user_feedback")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FEEDBACK_LOG_PATH = DATA_DIR / "user_feedbacks.jsonl"


def record_user_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    text = payload.get("feedback_text", "").strip()
    score = payload.get("score")
    label = payload.get("label")

    # Nếu client chưa phân loại, server chạy NLP classifier
    if score is None or not label:
        nlp_res = classify_user_feedback(text)
        score = nlp_res["score"]
        label = nlp_res["label"]

    flag_for_retrain = int(score) <= 2  # Nhãn 'Tệ' (1) hoặc 'Trung bình' (2)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "device_id": payload.get("device_id", "unknown"),
        "platform": payload.get("platform", "windows"),
        "cpu_arch": payload.get("cpu_arch", "x86_64"),
        "ram_mb": int(payload.get("ram_mb", 4096)),
        "bandwidth_kbps": float(payload.get("bandwidth_kbps", 2000.0)),
        "deadline_s": float(payload.get("deadline_s", 60.0)),
        "free_storage_mb": float(payload.get("free_storage_mb", 1024.0)),
        "distributed_variant": payload.get("distributed_variant", "mobilenetv2_int8"),
        "feedback_text": text,
        "score": int(score),
        "label": str(label),
        "flag_for_retrain": flag_for_retrain,
    }

    with open(FEEDBACK_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info("Đã ghi nhận user feedback: %s (%s điểm) - flag_retrain=%s", label, score, flag_for_retrain)

    return {
        "status": "success",
        "message": "Feedback đã được ghi nhận thành công",
        "score": score,
        "label": label,
        "flag_for_retrain": flag_for_retrain,
    }
