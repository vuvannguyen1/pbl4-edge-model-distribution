"""Tải 4 model ONNX đã chốt vào models/CV/ và in kích thước + SHA-256.

Chạy từ thư mục gốc repo:
    python tools/download_models.py

File .onnx bị .gitignore bỏ qua, nên mỗi thành viên chạy script này
để có đúng bộ model giống nhau thay vì gửi file qua Zalo/Drive.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

BASE_URL = (
    "https://huggingface.co/onnxmodelzoo/legacy_models/resolve/main/"
    "validated/vision/classification"
)

# Tên file lưu cục bộ (theo quy ước của nhóm) -> đường dẫn trên ONNX Model Zoo
MODELS: dict[str, str] = {
    "MobileNetV2_FP32.onnx": "mobilenet/model/mobilenetv2-12.onnx",
    "MobileNetV2_INT8.onnx": "mobilenet/model/mobilenetv2-12-int8.onnx",
    "ResNet50_FP32.onnx": "resnet/model/resnet50-v1-12.onnx",
    "ResNet50_INT8.onnx": "resnet/model/resnet50-v1-12-int8.onnx",
}

# Hash đã biết từ JSON mẫu của nhóm (04_request_response.json)
KNOWN_SHA256: dict[str, str] = {
    "MobileNetV2_INT8.onnx": (
        "cc028fe6cae7bc11a4ff53cfc9b79c920e8be65ce33a904ec3e2a8f66d77f95f"
    ),
}

READ_SIZE = 1024 * 1024  # 1 MiB


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(READ_SIZE):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        while block := resp.read(READ_SIZE):
            out.write(block)
            done += len(block)
            if total:
                print(f"\r  {done / total:6.1%}", end="", flush=True)
    print()
    tmp.replace(dest)  # chỉ đổi tên khi tải xong, tránh file dở dang


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    target_dir = repo_root / "models" / "CV"
    target_dir.mkdir(parents=True, exist_ok=True)

    ok = True
    for name, remote in MODELS.items():
        dest = target_dir / name
        if dest.exists():
            print(f"[bỏ qua] {name} đã có")
        else:
            print(f"[tải] {name}")
            download(f"{BASE_URL}/{remote}", dest)

        digest = sha256_of(dest)
        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"  {size_mb:7.2f} MB  sha256={digest}")

        expected = KNOWN_SHA256.get(name)
        if expected and digest != expected:
            print(f"  [LỖI] SHA-256 không khớp hash đã biết: {expected}")
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())