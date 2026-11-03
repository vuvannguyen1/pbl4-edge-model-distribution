"""
predict.py
Module đọc gói tin JSON theo Protocol 1.0 và dự đoán model phù hợp nhất.
Có cơ chế bảo vệ an toàn (Safety Fallback) theo thiết kế hệ thống.
"""
import json
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import joblib
import pandas as pd

from selector.feedback_classifier import classify_user_feedback

# Metadata kích thước và thông số model CV trong catalog
REGISTRY_PATH = Path(__file__).resolve().parent.parent / "registry" / "model_registry.json"

def load_model_specs() -> dict:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    specs = {}
    for entry in registry["variants"]:
        if entry.get("task_id") != "object_detection" or entry.get("status") != "ready":
            continue
        size_mb = float(entry["size_bytes"]) / 1024 / 1024
        specs[entry["variant_id"]] = {
            "file": entry["file"],
            "size_bytes": int(entry["size_bytes"]),
            "peak_ram_mb": float(entry.get("minimum_ram_mb") or max(128.0, size_mb * 1.5)),
            "precision": entry.get("precision", "fp32"),
        }
    if not specs:
        raise ValueError("Registry khÃ´ng cÃ³ object_detection variant ready")
    return specs

_MODEL_PATH = Path(__file__).resolve().parent / "artifacts" / "selector_model.joblib"
_pipeline = None


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        if not _MODEL_PATH.exists():
            raise FileNotFoundError(f"Model chưa được train! Hãy chạy train.py trước.")
        _pipeline = joblib.load(_MODEL_PATH)
    return _pipeline


def select_model(register_data: dict, request_data: dict) -> dict:
    """
    Nhận 2 dict JSON từ Protocol 1.0:
    - register_data: chứa platform, cpu_arch, ram_mb,...
    - request_data: chứa bandwidth_kbps, deadline_s, free_storage_mb, preferred_variant,...
    
    Trả về dict gồm: variant_id, reason, estimated_download_s, confidence
    """
    pipeline = get_pipeline()
    model_specs = load_model_specs()

    # 1. Nếu client chỉ định preferred_variant hợp lệ
    preferred = request_data.get("preferred_variant")
    if preferred and preferred in model_specs:
        est_download_s = round((model_specs[preferred]["size_bytes"] * 8.0) / (request_data["bandwidth_kbps"] * 1000.0), 2)
        return {
            "variant_id": preferred,
            "reason": f"Client yêu cầu rõ biến thể {preferred}.",
            "estimated_download_s": est_download_s,
            "confidence": 1.0,
            "is_preferred": True
        }

    # 2. Chuẩn bị vector đặc trưng
    platform = register_data.get("platform", "android")
    cpu_arch = register_data.get("cpu_arch", "aarch64")
    ram_mb = int(register_data.get("ram_mb", 4096))
    bandwidth_kbps = float(request_data.get("bandwidth_kbps") or 2000.0)
    deadline_s = float(request_data.get("deadline_s", 10.0))
    free_storage_mb = float(request_data.get("free_storage_mb") or 1024.0)

    sentiment_score = request_data.get("sentiment_score", 3)
    sentiment_confidence = request_data.get("sentiment_confidence", 0.0)
    feedback_text = request_data.get("feedback_text")
    if feedback_text and "sentiment_score" not in request_data:
        sentiment = classify_user_feedback(str(feedback_text))
        sentiment_score = sentiment["score"]
        sentiment_confidence = sentiment["confidence"]

    input_df = pd.DataFrame([{
        "platform": platform,
        "cpu_arch": cpu_arch,
        "ram_mb": ram_mb,
        "bandwidth_kbps": bandwidth_kbps,
        "deadline_s": deadline_s,
        "free_storage_mb": free_storage_mb,
        "sentiment_score": float(sentiment_score),
        "sentiment_confidence": float(sentiment_confidence),
    }])

    # 3. AI Inference
    predicted_variant = pipeline.predict(input_df)[0]
    probabilities = pipeline.predict_proba(input_df)[0]
    classes = pipeline.classes_
    confidence = float(max(probabilities))

    # 4. Safety Guard / Fallback theo thiết kế hệ thống
    if predicted_variant == "NO_VARIANT_FITS":
        return {
            "variant_id": None,
            "error_code": "NO_VARIANT_FITS",
            "reason": f"Không có phiên bản nào tải kịp trong {deadline_s}s ở tốc độ {bandwidth_kbps:.0f} kbps.",
            "confidence": confidence
        }

    if predicted_variant not in model_specs:
        return {
            "variant_id": None,
            "error_code": "UNKNOWN_VARIANT",
            "reason": f"Selector trả về model chưa có trong catalog: {predicted_variant}.",
            "confidence": confidence,
        }
    specs = model_specs[predicted_variant]
    est_download_s = round((specs["size_bytes"] * 8.0) / (bandwidth_kbps * 1000.0), 2)

    # Kiểm tra hard-constraint để đảm bảo AI không chọn sai do edge case
    if est_download_s > deadline_s or (ram_mb * 0.7 < specs["peak_ram_mb"]):
        # Fallback về bản nhẹ nhất an toàn: mobilenetv2_int8
        safe_fallback = min(model_specs, key=lambda variant: model_specs[variant]["size_bytes"])
        safe_est_s = round((model_specs[safe_fallback]["size_bytes"] * 8.0) / (bandwidth_kbps * 1000.0), 2)
        if safe_est_s <= deadline_s:
            return {
                "variant_id": safe_fallback,
                "reason": f"AI gợi ý {predicted_variant} nhưng tiềm ẩn rủi ro sát ngưỡng, fallback về {safe_fallback} an toàn hơn.",
                "estimated_download_s": safe_est_s,
                "confidence": 0.99,
                "is_fallback": True
            }
        else:
            return {
                "variant_id": None,
                "error_code": "NO_VARIANT_FITS",
                "reason": f"Mạng quá yếu ({bandwidth_kbps:.0f} kbps), không kịp deadline {deadline_s}s.",
                "confidence": 1.0
            }

    reason = (
        f"AI chọn {predicted_variant} cho thiết bị ({platform}, {cpu_arch}, RAM {ram_mb}MB) "
        f"với mạng {bandwidth_kbps:.0f} kbps (ước tính tải trong {est_download_s}s, deadline {deadline_s}s)."
    )

    return {
        "variant_id": predicted_variant,
        "reason": reason,
        "estimated_download_s": est_download_s,
        "confidence": round(confidence, 4),
        "is_fallback": False
    }


if __name__ == "__main__":
    # Test mẫu bằng chính gói tin JSON trong Protocol 1.0
    sample_reg = {
        "protocol_version": "1.0",
        "device_id": "phoneB",
        "platform": "android",
        "cpu_arch": "aarch64",
        "ram_mb": 6144,
        "runtime": "onnxruntime 1.25.1",
        "peer_port": 9000
    }

    test_cases = [
        {"name": "Trường hợp 1: Mạng yếu (1000 kbps), deadline 10s", "req": {"bandwidth_kbps": 1000, "deadline_s": 10, "free_storage_mb": 2048}},
        {"name": "Trường hợp 2: Mạng mạnh (40000 kbps), deadline 30s, RAM 6GB", "req": {"bandwidth_kbps": 40000, "deadline_s": 30, "free_storage_mb": 2048}},
        {"name": "Trường hợp 3: Mạng cực yếu (300 kbps), deadline 3s (quá gấp)", "req": {"bandwidth_kbps": 300, "deadline_s": 3, "free_storage_mb": 500}},
        {"name": "Trường hợp 4: Mạng trung bình (8000 kbps), deadline 15s", "req": {"bandwidth_kbps": 8000, "deadline_s": 15, "free_storage_mb": 1024}},
    ]

    print("=== CHẠY THỬ NGHIỆM DỰ ĐOÁN TỪ JSON PROTOCOL 1.0 ===")
    for case in test_cases:
        print(f"\n---> {case['name']}")
        res = select_model(sample_reg, case["req"])
        print(json.dumps(res, indent=2, ensure_ascii=False))
