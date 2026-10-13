"""Tải nhãn ImageNet và ảnh demo vào samples/.

Chạy từ thư mục gốc repo:
    python tools/download_assets.py

- samples/imagenet_synset.txt: 1000 dòng dạng "n01440764 tench, Tinca tinca".
- Ảnh demo (giấy phép tự do, Wikimedia Commons). Nếu không tải được ảnh,
  người dùng tự chép một ảnh .jpg vào samples/ (xem samples/README.md).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import requests
from PIL import Image

SYNSET_URL = (
    "https://raw.githubusercontent.com/onnx/models/main/"
    "validated/vision/classification/synset.txt"
)
EXPECTED_LINES = 1000

# Ảnh mẫu trên Wikimedia Commons (giấy phép tự do). Tên file lưu -> URL.
DEMO_IMAGES: dict[str, str] = {
    "cat.jpg": (
        "https://upload.wikimedia.org/wikipedia/commons/3/3a/Cat03.jpg"
    ),
    "dog.jpg": (
        "https://upload.wikimedia.org/wikipedia/commons/2/26/"
        "YellowLabradorLooking_new.jpg"
    ),
}

USER_AGENT = "pbl4-edge-model-distribution/1.0 (educational project)"


def fetch(url: str, timeout: float = 30.0) -> bytes:
    # requests dùng bộ chứng chỉ certifi, tránh lỗi chứng chỉ hệ thống cũ trên Windows
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def check_synset(path: Path) -> list[str]:
    """Trả về danh sách lỗi; rỗng nếu file đúng 1000 dòng hợp lệ."""
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    errors: list[str] = []
    if len(lines) != EXPECTED_LINES:
        errors.append(f"có {len(lines)} dòng, cần {EXPECTED_LINES}")
    for no, line in enumerate(lines, start=1):
        code, _, names = line.partition(" ")
        if not (code.startswith("n") and code[1:].isdigit() and names.strip()):
            errors.append(f"dòng {no} sai dạng: {line!r}")
            break
    return errors


def download_synset(dest: Path) -> bool:
    if dest.exists() and not check_synset(dest):
        print(f"[bỏ qua] {dest.name} đã có và hợp lệ")
        return True
    print(f"[tải] {SYNSET_URL}")
    try:
        data = fetch(SYNSET_URL)
    except requests.RequestException as exc:
        print(f"  [LỖI] không tải được nhãn: {exc}")
        return False
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_bytes(data)
    errors = check_synset(tmp)
    if errors:
        tmp.unlink()
        print(f"  [LỖI] file nhãn không hợp lệ: {'; '.join(errors)}")
        return False
    tmp.replace(dest)
    print(f"  OK: {EXPECTED_LINES} nhãn -> {dest.as_posix()}")
    return True


def download_images(target_dir: Path) -> int:
    count = 0
    for name, url in DEMO_IMAGES.items():
        dest = target_dir / name
        if dest.exists():
            print(f"[bỏ qua] {name} đã có")
            count += 1
            continue
        print(f"[tải] {name}")
        try:
            data = fetch(url)
            Image.open(io.BytesIO(data)).verify()  # chắc chắn là ảnh, không phải trang lỗi
            dest.write_bytes(data)
            count += 1
            print(f"  OK: {dest.stat().st_size:,} bytes")
        except (requests.RequestException, OSError) as exc:
            print(f"  [cảnh báo] không tải được {name}: {exc}")
    return count


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    repo_root = Path(__file__).resolve().parent.parent
    samples = repo_root / "samples"
    samples.mkdir(parents=True, exist_ok=True)

    ok = download_synset(samples / "imagenet_synset.txt")
    if download_images(samples) == 0:
        print("[cảnh báo] chưa có ảnh demo, hãy tự chép một ảnh .jpg vào samples/")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
