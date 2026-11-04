"""Tải đủ model ONNX trong data/model_sources.json vào models/CV/.

Chạy từ thư mục gốc repo:
    python tools/download_models.py

File .onnx bị .gitignore bỏ qua, nên mỗi thành viên chạy script này để có
đúng bộ model giống nhau. Mỗi file được kiểm tra kích thước và SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

READ_SIZE = 1024 * 1024  # 1 MiB


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(READ_SIZE):
            digest.update(block)
    return digest.hexdigest()


def download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "pbl4-edge-model-distribution/1.0"})
    with urllib.request.urlopen(request, timeout=180) as resp, tmp.open("wb") as out:
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    repo_root = Path(__file__).resolve().parent.parent
    target_dir = repo_root / "models" / "CV"
    target_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = repo_root / "data" / "model_sources.json"
    entries: list[dict[str, Any]] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if len(entries) != 10:
        print(f"[LỖI] Manifest phải có đúng 10 model CV, hiện có {len(entries)}")
        return 1

    ok = True
    for entry in entries:
        if entry.get("task_group") != "CV":
            continue
        name = str(entry["filename"])
        url = str(entry["url"])
        expected = str(entry["sha256"]).lower()
        dest = (target_dir / name).resolve()
        if dest.parent != target_dir.resolve():
            print(f"[LỖI] filename không nằm trong models/CV: {name}")
            ok = False
            continue
        if dest.exists() and sha256_of(dest) == expected:
            print(f"[bỏ qua] {name} đã có và đúng SHA-256")
        else:
            print(f"[tải] {entry.get('variant_id', name)}")
            download(url, dest)

        digest = sha256_of(dest)
        size_mb = dest.stat().st_size / 1024 / 1024
        print(f"  {size_mb:7.2f} MB  sha256={digest}")

        expected_size = int(entry.get("size_bytes", 0))
        if digest != expected:
            print(f"  [LỖI] SHA-256 không khớp: {expected}")
            ok = False
        if expected_size and dest.stat().st_size != expected_size:
            print(f"  [LỖI] kích thước không khớp: {expected_size} bytes")
            ok = False

    print(f"Đã kiểm tra {len(entries)} model CV")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
