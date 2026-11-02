"""
generate_dataset.py
Tạo tập dữ liệu mô phỏng (synthetic dataset) để huấn luyện AI Model Selector
cho bài toán phân phối 4 model (PBL4 Protocol 1.0).
"""
import json
import random
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
import numpy as np

# 1. Định nghĩa thông số kỹ thuật của 4 model ONNX khởi tạo
MODELS = {
    "mobilenetv2_int8": {
        "size_bytes": 3655033,       # ~3.49 MB
        "peak_ram_mb": 110,          # RAM khi load & run
        "top1_accuracy": 0.683,
        "base_latency_ms": 18.0,     # Độ trễ chuẩn trên arm64/x64
        "load_time_s": 0.15,
        "precision": "int8"
    },
    "mobilenetv2_fp32": {
        "size_bytes": 14260000,      # ~13.6 MB
        "peak_ram_mb": 170,
        "top1_accuracy": 0.718,
        "base_latency_ms": 25.0,
        "load_time_s": 0.25,
        "precision": "fp32"
    },
    "resnet50_int8": {
        "size_bytes": 25500000,       # ~24.3 MB
        "peak_ram_mb": 260,
        "top1_accuracy": 0.753,
        "base_latency_ms": 45.0,
        "load_time_s": 0.35,
        "precision": "int8"
    },
    "resnet50_fp32": {
        "size_bytes": 98200000,       # ~93.6 MB
        "peak_ram_mb": 420,
        "top1_accuracy": 0.761,
        "base_latency_ms": 85.0,
        "load_time_s": 0.60,
        "precision": "fp32"
    }
}

PLATFORMS = ["android", "android_browser", "ios_browser", "windows", "linux"]
CPU_ARCHS = ["aarch64", "x86_64", "arm32"]


def evaluate_candidate(variant_id, specs, platform, cpu_arch, ram_mb, bandwidth_kbps, deadline_s, free_storage_mb):
    """
    Kiểm tra hard constraints và tính hàm Utility (Score).
    Nếu vi phạm ràng buộc cứng -> trả về -9999 (loại).
    """
    size_mb = specs["size_bytes"] / (1024 * 1024)
    
    # Ràng buộc 1: Bộ nhớ trống
    if free_storage_mb is not None and free_storage_mb < size_mb + 20:
        return -9999, "Không đủ dung lượng lưu trữ"
        
    # Ràng buộc 2: RAM thiết bị (để tránh OOM, RAM model không được vượt quá 70% RAM máy)
    if ram_mb * 0.70 < specs["peak_ram_mb"]:
        return -9999, "Không đủ RAM, nguy cơ tràn bộ nhớ"

    # Ràng buộc 3: Thời gian tải ước tính (1 kbps = 1000 bps)
    # est_download_s = (size_bytes * 8) / (bandwidth_kbps * 1000)
    est_download_s = (specs["size_bytes"] * 8.0) / (bandwidth_kbps * 1000.0)
    total_time_s = est_download_s + specs["load_time_s"]
    
    if total_time_s > deadline_s:
        return -9999, f"Quá deadline ({total_time_s:.1f}s > {deadline_s:.1f}s)"

    # Điều chỉnh latency theo platform / cpu
    latency_factor = 1.0
    if cpu_arch == "arm32":
        latency_factor *= 2.0
    elif platform in ["android_browser", "ios_browser"]:
        latency_factor *= 1.3

    eff_latency = specs["base_latency_ms"] * latency_factor

    # Khi mạng cực mạnh và máy khủng (Workstation/PC x86_64, RAM >= 16GB, 50Mbps+, deadline >= 30s):
    # Người dùng ưu tiên FP32 nguyên bản (bản gốc không bị loss lượng tử hóa)
    is_ultra_high_end = (ram_mb >= 16384 and bandwidth_kbps >= 50000 and deadline_s >= 30)
    is_high_end = (ram_mb >= 8192 and bandwidth_kbps >= 20000 and deadline_s >= 15)
    is_mid_range = (ram_mb >= 4096 and bandwidth_kbps >= 8000 and deadline_s >= 8)

    if is_ultra_high_end:
        w_acc = 50.0   # Cực kỳ chú trọng độ chính xác cao nhất (FP32)
        w_lat = 0.2
        w_time = 0.2
    elif is_high_end:
        w_acc = 20.0
        w_lat = 0.5
        w_time = 0.5
    elif is_mid_range:
        w_acc = 12.0
        w_lat = 1.0
        w_time = 0.8
    else:
        w_acc = 6.0
        w_lat = 2.0
        w_time = 1.5

    score = (
        w_acc * specs["top1_accuracy"]
        - w_lat * (eff_latency / 100.0)
        - w_time * (total_time_s / deadline_s)
    )

    return score, "OK"


def generate_samples(n_samples=30000, seed=42):
    random.seed(seed)
    np.random.seed(seed)

    rows = []
    
    for _ in range(n_samples):
        platform = random.choice(PLATFORMS)
        if platform in ["windows", "linux"]:
            cpu_arch = random.choice(["x86_64", "x86_64", "aarch64"])
            ram_mb = random.choice([2048, 4096, 8192, 16384, 32768])
        elif platform == "ios_browser":
            cpu_arch = "aarch64"
            ram_mb = random.choice([3072, 4096, 6144, 8192])
        else: # android
            cpu_arch = random.choice(["aarch64", "aarch64", "arm32"])
            ram_mb = random.choice([1024, 2048, 3072, 4096, 6144, 8192, 12288])

        # Băng thông log-normal hoặc phân phối thực tế từ yếu (300 kbps) đến cực nhanh (100,000 kbps)
        bandwidth_kbps = float(np.random.choice([
            random.uniform(300, 800),      # 3G yếu / nghẽn mạng
            random.uniform(800, 2500),     # 3G/4G cơ bản
            random.uniform(2500, 8000),    # 4G tốt
            random.uniform(8000, 25000),   # Wifi gia đình
            random.uniform(25000, 80000),  # Wifi mạnh / 5G
        ]))
        
        # Deadline: từ rất gấp (3-5s) đến thoải mái (15-60s)
        deadline_s = float(random.choice([3, 5, 8, 10, 15, 20, 30, 45, 60]))

        # Bộ nhớ trống
        free_storage_mb = float(random.choice([100, 300, 1024, 4096, 20480]))

        # Đánh giá 4 ứng viên
        best_variant = None
        best_score = -9999
        
        for variant_id, specs in MODELS.items():
            score, reason = evaluate_candidate(
                variant_id, specs, platform, cpu_arch, ram_mb,
                bandwidth_kbps, deadline_s, free_storage_mb
            )
            if score > best_score:
                best_score = score
                best_variant = variant_id

        # Nếu không có model nào kịp hoặc đủ RAM
        if best_variant is None or best_score <= -9999:
            best_variant = "NO_VARIANT_FITS"

        rows.append({
            "platform": platform,
            "cpu_arch": cpu_arch,
            "ram_mb": ram_mb,
            "bandwidth_kbps": round(bandwidth_kbps, 1),
            "deadline_s": round(deadline_s, 1),
            "free_storage_mb": round(free_storage_mb, 1),
            "selected_variant": best_variant
        })

    df = pd.DataFrame(rows)
    return df


if __name__ == "__main__":
    out_dir = Path(__file__).resolve().parent / "training_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "selector_synthetic_dataset.csv"

    print("Đang tạo tập dữ liệu mô phỏng 30,000 mẫu...")
    df = generate_samples(30000)
    df.to_csv(out_file, index=False, encoding="utf-8")
    print(f"Đã lưu thành công tại: {out_file}")
    print("\nPhân phối nhãn (selected_variant):")
    print(df["selected_variant"].value_counts(normalize=True).apply(lambda x: f"{x*100:.2f}%"))
