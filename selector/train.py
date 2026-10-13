"""
train.py
Huấn luyện AI Model Selector dựa trên LightGBM / Scikit-learn Pipeline
Phân phối tối ưu 4 model ONNX theo Protocol 1.0
"""
import time
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import joblib
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, accuracy_score
from lightgbm import LGBMClassifier


def train_model():
    base_dir = Path(__file__).resolve().parent
    data_path = base_dir / "training_data" / "selector_synthetic_dataset.csv"
    
    if not data_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file {data_path}. Vui lòng chạy generate_dataset.py trước!")

    print(f"[*] Đang nạp dữ liệu từ: {data_path}")
    df = pd.read_csv(data_path)

    features = ["platform", "cpu_arch", "ram_mb", "bandwidth_kbps", "deadline_s", "free_storage_mb"]
    target = "selected_variant"

    X = df[features]
    y = df[target]

    categorical_cols = ["platform", "cpu_arch"]
    numerical_cols = ["ram_mb", "bandwidth_kbps", "deadline_s", "free_storage_mb"]

    # Preprocessing
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), categorical_cols),
            ("num", "passthrough", numerical_cols)
        ]
    )

    # LightGBM Classifier
    clf = LGBMClassifier(
        n_estimators=150,
        learning_rate=0.08,
        num_leaves=31,
        random_state=42,
        verbosity=-1
    )

    pipeline = Pipeline(steps=[
        ("preprocessor", preprocessor),
        ("classifier", clf)
    ])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    print(f"[*] Tập train: {len(X_train):,} mẫu | Tập test: {len(X_test):,} mẫu")
    print("[*] Đang bắt đầu huấn luyện LightGBM...")

    start_time = time.time()
    pipeline.fit(X_train, y_train)
    duration = time.time() - start_time

    print(f"[+] Huấn luyện xong trong: {duration:.2f} giây!\n")

    # Đánh giá
    y_pred = pipeline.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"=== ĐỘ CHÍNH XÁC (TEST ACCURACY): {acc * 100:.2f}% ===")
    print("\nBáo cáo chi tiết theo từng lớp:")
    print(classification_report(y_test, y_pred, digits=4))

    # Lưu model
    artifacts_dir = base_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    model_file = artifacts_dir / "selector_model.joblib"
    joblib.dump(pipeline, model_file)
    print(f"[+] Đã lưu model thành công tại: {model_file}")

    return pipeline


if __name__ == "__main__":
    train_model()
