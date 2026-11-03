"""Tải trước artifact object-detection vào models/CV theo manifest.

Manifest JSON có dạng:

[
  {
    "variant_id": "detector_nano_fp16",
    "url": "https://...",
    "filename": "detector_nano_fp16.onnx",
    "sha256": "...",
    "task_id": "object_detection"
  }
]

Chỉ các entry có task_id=object_detection được tải. File được ghi qua .part và
chỉ đổi tên sau khi tải xong; checksum là bắt buộc để tránh đăng ký file hỏng.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

READ_SIZE = 1024 * 1024


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(READ_SIZE):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, destination: Path) -> None:
    partial = destination.with_name(destination.name + ".part")
    with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as output:
        while block := response.read(READ_SIZE):
            output.write(block)
    partial.replace(destination)


def main(manifest_path: Path) -> int:
    entries: list[dict[str, Any]] = json.loads(manifest_path.read_text(encoding="utf-8"))
    repo_root = manifest_path.resolve().parent.parent
    target_dir = repo_root / "models" / "CV"
    target_dir.mkdir(parents=True, exist_ok=True)
    ok = True

    for entry in entries:
        if entry.get("task_id") != "object_detection":
            continue
        filename = str(entry.get("filename", "")).strip()
        url = str(entry.get("url", "")).strip()
        expected = str(entry.get("sha256", "")).strip().lower()
        if not filename or not url or len(expected) != 64:
            print(f"[LỖI] Manifest không hợp lệ cho {entry.get('variant_id', '<unknown>')}")
            ok = False
            continue
        destination = (target_dir / filename).resolve()
        try:
            destination.relative_to(target_dir.resolve())
        except ValueError:
            print(f"[LỖI] filename nằm ngoài models/CV: {filename}")
            ok = False
            continue
        if not destination.exists() or sha256_of(destination) != expected:
            print(f"[tải] {entry.get('variant_id', filename)}")
            download(url, destination)
        actual = sha256_of(destination)
        print(f"{filename}: {destination.stat().st_size} bytes sha256={actual}")
        if actual != expected:
            print(f"[LỖI] SHA-256 không khớp: expected={expected}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    manifest = root / "data" / "model_sources.json"
    if len(sys.argv) > 1:
        manifest = Path(sys.argv[1]).resolve()
    if not manifest.is_file():
        print(f"Không tìm thấy manifest: {manifest}")
        sys.exit(2)
    sys.exit(main(manifest))
