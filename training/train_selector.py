"""Train the adaptive object-detection model selector from the live catalog."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.compose import ColumnTransformer
from sklearn.metrics import accuracy_score, classification_report
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

FEATURES = ["platform", "cpu_arch", "ram_mb", "bandwidth_kbps", "deadline_s", "free_storage_mb", "sentiment_score", "sentiment_confidence"]
CATEGORICAL = ["platform", "cpu_arch"]
NUMERICAL = [x for x in FEATURES if x not in CATEGORICAL]
TARGET = "selected_variant"
ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "registry" / "model_registry.json"


def load_catalog() -> dict[str, dict[str, Any]]:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    result = {}
    for item in registry["variants"]:
        if item.get("task_id") != "object_detection" or item.get("status") != "ready":
            continue
        size_mb = float(item["size_bytes"]) / 1024 / 1024
        result[item["variant_id"]] = {
            "size_bytes": int(item["size_bytes"]), "size_mb": size_mb,
            "peak_ram_mb": float(item.get("minimum_ram_mb") or max(128.0, size_mb * 1.5)),
            "latency_ms": max(4.0, size_mb * 0.35), "precision": item.get("precision", "fp32"),
        }
    if len(result) != 10:
        raise ValueError(f"Catalog object_detection cần 10 model ready, hiện có {len(result)}")
    return result


def utility(model: dict[str, Any], ram: float, bandwidth: float, deadline: float, storage: float) -> float:
    download = model["size_bytes"] * 8 / max(bandwidth * 1000, 1)
    if storage < model["size_mb"] + 20 or ram * 0.7 < model["peak_ram_mb"] or download > deadline:
        return -1e9
    return 10.0 - 0.7 * download / max(deadline, 1) - 0.8 * model["latency_ms"] / 100 - 0.02 * model["size_mb"]


def best_alternative(current: str, row: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> str:
    candidates = {k: utility(v, float(row["ram_mb"]), float(row["bandwidth_kbps"]), float(row["deadline_s"]), float(row["free_storage_mb"])) for k, v in catalog.items() if k != current}
    eligible = {k: v for k, v in candidates.items() if v > -1e8}
    return max(eligible, key=eligible.get) if eligible else min(catalog, key=lambda k: catalog[k]["size_bytes"])


def make_synthetic(n: int, catalog: dict[str, dict[str, Any]], seed: int) -> pd.DataFrame:
    rng = random.Random(seed)
    rows = []
    platforms = [("android", "aarch64"), ("android", "arm32"), ("windows", "x86_64"), ("linux", "x86_64"), ("ios_browser", "aarch64")]
    for _ in range(n):
        platform, arch = rng.choice(platforms)
        ram = rng.choice([1024, 2048, 4096, 8192, 16384, 32768])
        bandwidth = 10 ** rng.uniform(2.5, 5.0)
        deadline = rng.choice([3, 5, 8, 10, 15, 20, 30, 45, 60])
        storage = rng.choice([100, 300, 1024, 4096, 20480, 512000])
        scores = {k: utility(v, ram, bandwidth, deadline, storage) for k, v in catalog.items()}
        selected = max(scores, key=scores.get)
        if scores[selected] <= -1e8:
            selected = "NO_VARIANT_FITS"
        rows.append({"platform": platform, "cpu_arch": arch, "ram_mb": ram, "bandwidth_kbps": round(bandwidth, 1), "deadline_s": deadline, "free_storage_mb": storage, "sentiment_score": 3.0, "sentiment_confidence": 0.0, TARGET: selected})
    # Keep every ready variant represented in the supervised target space. These are
    # boundary scenarios used to prevent the classifier from collapsing to only the
    # smallest model when the synthetic utility distribution is imbalanced.
    ordered = sorted(catalog, key=lambda key: catalog[key]["size_bytes"])
    for index, variant_id in enumerate(ordered):
        model = catalog[variant_id]
        rows.append({"platform": "windows", "cpu_arch": "x86_64", "ram_mb": max(32768, int(model["peak_ram_mb"] * 2)), "bandwidth_kbps": 100000.0, "deadline_s": 60.0, "free_storage_mb": max(512000, model["size_mb"] + 100), "sentiment_score": 3.0, "sentiment_confidence": 0.0, TARGET: variant_id})
    rows.append({"platform": "android", "cpu_arch": "arm32", "ram_mb": 512, "bandwidth_kbps": 100.0, "deadline_s": 3.0, "free_storage_mb": 100.0, "sentiment_score": 3.0, "sentiment_confidence": 0.0, TARGET: "NO_VARIANT_FITS"})
    return pd.DataFrame(rows)


def feedback_rows(path: Path, catalog: dict[str, dict[str, Any]]) -> tuple[pd.DataFrame, list[float]]:
    rows, weights = [], []
    if not path.is_file():
        return pd.DataFrame(columns=FEATURES + [TARGET]), weights
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        current = record.get("distributed_variant")
        if current not in catalog:
            continue
        score = int(record.get("score", 3))
        row = {"platform": record.get("platform", "windows"), "cpu_arch": record.get("cpu_arch", "x86_64"), "ram_mb": record.get("ram_mb", 4096), "bandwidth_kbps": record.get("bandwidth_kbps", 2000.0), "deadline_s": record.get("deadline_s", 60.0), "free_storage_mb": record.get("free_storage_mb", 1024.0), "sentiment_score": score, "sentiment_confidence": record.get("sentiment_confidence", 0.0)}
        if score <= 2:
            rows.extend([dict(row, **{TARGET: current}), dict(row, **{TARGET: best_alternative(current, row, catalog)})])
            weights.extend([0.15, 4.0])
        elif score == 3:
            rows.append(dict(row, **{TARGET: current})); weights.append(1.0)
        else:
            rows.append(dict(row, **{TARGET: current})); weights.append(2.0 + (score - 4) * 0.75)
    return pd.DataFrame(rows, columns=FEATURES + [TARGET]), weights


def build_pipeline(seed: int) -> Pipeline:
    preprocessor = ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL), ("num", "passthrough", NUMERICAL)])
    classifier = LGBMClassifier(n_estimators=220, learning_rate=0.06, num_leaves=31, random_state=seed, verbosity=-1)
    return Pipeline([("preprocessor", preprocessor), ("classifier", classifier)])


def train(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    base = make_synthetic(args.samples, catalog, args.seed)
    feedback, feedback_weights = feedback_rows(Path(args.feedback_path), catalog)
    train_df = pd.concat([base, feedback], ignore_index=True)
    sample_weight = np.concatenate([np.ones(len(base)), np.asarray(feedback_weights, dtype=float)])
    model = build_pipeline(args.seed)
    model.fit(train_df[FEATURES], train_df[TARGET], classifier__sample_weight=sample_weight)
    holdout = make_synthetic(max(3000, args.samples // 10), catalog, args.seed + 1)
    predicted = model.predict(holdout[FEATURES])
    metrics = {"catalog_variants": sorted(catalog), "feedback_rows": len(feedback), "feedback_weight_policy": {"1-2": "penalize current, reward alternative", "3": "keep", "4-5": "reward current"}, "accuracy": float(accuracy_score(holdout[TARGET], predicted)), "classification_report": classification_report(holdout[TARGET], predicted, output_dict=True, zero_division=0)}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, output)
    (output.parent / "selector_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (output.parent / "selector_training_config.json").write_text(json.dumps({"features": FEATURES, "catalog": sorted(catalog), "feedback_path": str(args.feedback_path), "samples": args.samples, "seed": args.seed}, indent=2), encoding="utf-8")
    print(json.dumps({"accuracy": metrics["accuracy"], "feedback_rows": len(feedback), "classes": sorted(model.classes_)}, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "selector")
    parser.add_argument("--output", type=Path, default=ROOT / "selector" / "artifacts" / "selector_model.joblib")
    parser.add_argument("--feedback-path", type=Path, default=ROOT / "data" / "user_feedbacks.jsonl")
    parser.add_argument("--samples", type=int, default=30000); parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


if __name__ == "__main__":
    train(parse_args())
