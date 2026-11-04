"""Feedback-driven retraining entry point for the object-detection selector."""
from __future__ import annotations

import json
import logging
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
    """Return the alternative taught for a low-rated feedback event."""
    catalog = load_catalog()
    current = record.get("distributed_variant")
    if current not in catalog:
        return min(catalog, key=lambda key: catalog[key]["size_bytes"])
    row = {
        "ram_mb": record.get("ram_mb", 4096),
        "bandwidth_kbps": record.get("bandwidth_kbps", 2000.0),
        "deadline_s": record.get("deadline_s", 60.0),
        "free_storage_mb": record.get("free_storage_mb", 1024.0),
    }
    return best_alternative(current, row, catalog)


def run_retrain() -> dict:
    if not FEEDBACK_LOG_PATH.is_file():
        return {"status": "skipped", "message": "Chưa có feedback"}
    records = [json.loads(line) for line in FEEDBACK_LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        return {"status": "skipped", "message": "Feedback rỗng"}
    args = type("Args", (), {
        "data_dir": ROOT / "data" / "selector",
        "output": ROOT / "selector" / "artifacts" / "selector_model.joblib",
        "feedback_path": FEEDBACK_LOG_PATH,
        "samples": 30000,
        "seed": 42,
    })()
    train(args)
    return {"status": "success", "feedback_events": len(records), "model_path": str(args.output)}


if __name__ == "__main__":
    print(run_retrain())
