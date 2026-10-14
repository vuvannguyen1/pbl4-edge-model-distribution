from __future__ import annotations
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
"""
user_feedback.py
Client đóng gói feedback của người dùng và gửi về Server.
"""

import argparse
import json
import sys
from typing import Any
import requests

from selector.feedback_classifier import classify_user_feedback


def send_user_feedback(
    server_url: str,
    device_id: str,
    feedback_text: str,
    distributed_variant: str,
    platform: str = "windows",
    cpu_arch: str = "x86_64",
    ram_mb: int = 4096,
    bandwidth_kbps: float = 2000.0,
    deadline_s: float = 60.0,
    free_storage_mb: float = 1024.0,
) -> dict[str, Any]:
    """Phân loại feedback và gửi JSON về server."""
    nlp_res = classify_user_feedback(feedback_text)

    payload = {
        "device_id": device_id,
        "platform": platform,
        "cpu_arch": cpu_arch,
        "ram_mb": ram_mb,
        "bandwidth_kbps": bandwidth_kbps,
        "deadline_s": deadline_s,
        "free_storage_mb": free_storage_mb,
        "distributed_variant": distributed_variant,
        "feedback_text": feedback_text,
        "score": nlp_res["score"],
        "label": nlp_res["label"],
    }

    url = f"{server_url.rstrip('/')}/api/user-feedback"
    resp = requests.post(url, json=payload, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Gửi đánh giá feedback khách hàng về server")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="Địa chỉ server")
    parser.add_argument("--device-id", default="laptopA", help="ID thiết bị")
    parser.add_argument("--variant", default="resnet50_fp32", help="Mô hình đã được phân phối")
    parser.add_argument("--comment", required=True, help="Nội dung đánh giá của khách hàng")
    parser.add_argument("--ram", type=int, default=4096, help="RAM MB")
    parser.add_argument("--bandwidth", type=float, default=1500.0, help="Băng thông kbps")
    args = parser.parse_args()

    print(f"Đang phân tích feedback: '{args.comment}'...")
    res = send_user_feedback(
        server_url=args.server,
        device_id=args.device_id,
        feedback_text=args.comment,
        distributed_variant=args.variant,
        ram_mb=args.ram,
        bandwidth_kbps=args.bandwidth,
    )
    print("Kết quả từ server:", json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
