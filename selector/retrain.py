"""
retrain.py
Module tự động đọc các feedback bị đánh giá thấp từ khách hàng,
hiệu chỉnh nhãn sai sót và huấn luyện lại LightGBM Model Selector.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
import joblib
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("selector.retrain")

REPO_ROOT = Path(__file__).resolve().parent.parent
FEEDBACK_LOG_PATH = REPO_ROOT / "data" / "user_feedbacks.jsonl"
DATASET_PATH = REPO_ROOT / "selector" / "training_data" / "selector_synthetic_dataset.csv"
MODEL_PATH = REPO_ROOT / "selector" / "artifacts" / "selector_model.joblib"


def correct_variant(record: dict) -> str:
    """Tự động điều chỉnh nhãn cho các lượt phân phối bị đánh giá thấp (score <= 2)."""
    current = record.get("distributed_variant", "")
    ram = record.get("ram_mb", 4096)
    bw = record.get("bandwidth_kbps", 2000.0)
    comment = record.get("feedback_text", "").lower()

    # Nếu model nặng nhưng máy lag, đơ, tải chậm -> Đổi sang mobilenetv2_int8
    if "resnet" in current or "fp32" in current:
        if bw < 3500 or ram < 6000 or any(w in comment for w in ["chậm", "lag", "đơ", "lâu", "nặng"]):
            return "mobilenetv2_int8"

    # Nếu model nhẹ mà nhận diện sai trong khi máy mạnh -> Đổi sang resnet50_fp32
    if "mobilenet" in current:
        if bw >= 5000 and ram >= 8192 and any(w in comment for w in ["sai", "chuẩn", "chính xác"]):
            return "resnet50_fp32"

    # Mặc định an toàn nhất cho Edge
    return "mobilenetv2_int8"


def run_retrain() -> dict:
    if not FEEDBACK_LOG_PATH.exists():
        logger.warning("Chưa có file log feedback tại %s", FEEDBACK_LOG_PATH)
        return {"status": "skipped", "message": "Chưa có feedback nào"}

    # Đọc các feedback cần retrain (score <= 2)
    bad_records = []
    with open(FEEDBACK_LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("flag_for_retrain", False) or rec.get("score", 5) <= 2:
                bad_records.append(rec)

    if not bad_records:
        logger.info("Không có feedback xấu nào cần retrain (tất cả đánh giá đều tốt/khá).")
        return {"status": "ok", "message": "Không có feedback xấu"}

    logger.info("Phát hiện %d lượt phân phối bị đánh giá thấp cần học lại.", len(bad_records))

    # Đọc dataset gốc (hoặc tự tạo nếu chưa có)
    if not DATASET_PATH.exists():
        logger.info("Chưa có dataset gốc, đang tự động tạo tại %s...", DATASET_PATH)
        try:
            from selector.generate_dataset import generate_dataset
            generate_dataset()
        except Exception as e:
            logger.warning("Không gọi được generate_dataset (%s), nạp dataset trống", e)

    if DATASET_PATH.exists():
        df_original = pd.read_csv(DATASET_PATH)
    else:
        logger.error("Không tìm thấy dataset gốc tại %s", DATASET_PATH)
        return {"status": "error", "message": "Không tìm thấy dataset"}

    # Tạo các mẫu hiệu chỉnh (corrected samples)
    corrected_rows = []
    for rec in bad_records:
        new_target = correct_variant(rec)
        logger.info(
            "Cấu hình (RAM=%sMB, BW=%skbps): phân phối '%s' bị đánh giá '%s' -> Học lại đổi sang: '%s'",
            rec.get("ram_mb"), rec.get("bandwidth_kbps"), rec.get("distributed_variant"),
            rec.get("label"), new_target
        )
        corrected_rows.append({
            "platform": rec.get("platform", "windows"),
            "cpu_arch": rec.get("cpu_arch", "x86_64"),
            "ram_mb": rec.get("ram_mb", 4096),
            "bandwidth_kbps": rec.get("bandwidth_kbps", 2000.0),
            "deadline_s": rec.get("deadline_s", 60.0),
            "free_storage_mb": rec.get("free_storage_mb", 1024.0),
            "selected_variant": new_target,
        })

    df_corrected = pd.DataFrame(corrected_rows)
    # Nhân bản mẫu sửa lỗi với trọng số cao hơn để model ghi nhớ bài học
    df_boosted = pd.concat([df_corrected] * 10, ignore_index=True)
    df_combined = pd.concat([df_original, df_boosted], ignore_index=True)

    # Huấn luyện lại Pipeline LightGBM
    features = ["platform", "cpu_arch", "ram_mb", "bandwidth_kbps", "deadline_s", "free_storage_mb"]
    X = df_combined[features]
    y = df_combined["selected_variant"]

    cat_cols = ["platform", "cpu_arch"]
    num_cols = ["ram_mb", "bandwidth_kbps", "deadline_s", "free_storage_mb"]

    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), cat_cols),
            ("num", "passthrough", num_cols),
        ]
    )

    clf = LGBMClassifier(
        n_estimators=100,
        learning_rate=0.05,
        random_state=42,
        verbosity=-1
    )

    pipeline = Pipeline([
        ("preprocessor", preprocessor),
        ("classifier", clf)
    ])

    logger.info("Đang huấn luyện lại mô hình Selector với %d mẫu dữ liệu...", len(df_combined))
    pipeline.fit(X, y)

    # Lưu lại model mới
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, MODEL_PATH)
    logger.info("Đã cập nhật mô hình phân phối thành công tại %s!", MODEL_PATH)

    return {
        "status": "success",
        "retrained_samples": len(bad_records),
        "total_dataset_size": len(df_combined),
        "model_path": str(MODEL_PATH),
    }


if __name__ == "__main__":
    res = run_retrain()
    print("Kết quả Retrain:", res)
