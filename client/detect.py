"""Nhận diện ảnh offline bằng model đã tải về trước đó, không gọi server.

Chạy:
    python -m client.detect
    python -m client.detect --image samples\\dog.jpg
    python -m client.detect --image samples\\dog.jpg samples\\cat.jpg
    python -m client.detect --image samples\\

Bỏ `--image`: mở hộp thoại chọn ảnh trên máy, nhận diện, rồi hỏi tiếp ảnh khác
(model chỉ nạp một lần) đến khi bấm Cancel.

Model lấy từ kho cục bộ `data/client/<device_id>/` do `client.agent` đã tải:
kiểm SHA-256 toàn file trước khi dùng, nạp một lần rồi chạy cho mọi ảnh.
Chọn model: `--variant` -> model đang dùng (active.json) -> top-1 cao nhất.

Mã thoát: 0 mọi ảnh thành công, 1 có lỗi.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from client.agent import DEFAULT_LABELS, REPO_ROOT
from client.downloader import META_FILE, choose_local, load_active, verified_variants
from client.inference import Classifier, InferenceResult, format_top5, load_labels
from client.picker import pick_images

CLIENT_DATA = REPO_ROOT / "data" / "client"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
EXIT_OK = 0
EXIT_ERROR = 1

Printer = Callable[[str], None]


class DetectError(Exception):
    """Không chạy được: thiếu model, thiếu ảnh, thiếu file nhãn."""


@dataclass
class DetectResult:
    variant: dict[str, Any]
    model_path: Path
    load_ms: float
    results: list[tuple[Path, InferenceResult | None, str | None]]  # (ảnh, kết quả, lỗi)

    @property
    def failures(self) -> int:
        return sum(1 for _, result, _ in self.results if result is None)


def find_cache_dir(cache_dir: Path | None, device_id: str | None, base: Path = CLIENT_DATA) -> Path:
    """`--cache-dir` -> `--device-id` -> thư mục thiết bị duy nhất có model trong `base`."""
    if cache_dir is not None:
        return cache_dir
    if device_id is not None:
        return base / device_id
    devices = (
        sorted(p for p in base.iterdir() if p.is_dir() and any(p.glob(f"*/{META_FILE}")))
        if base.is_dir()
        else []
    )
    if len(devices) == 1:
        return devices[0]
    if not devices:
        raise DetectError(
            f"Chưa có model nào trong {base}. Hãy chạy client.agent một lần khi có kết nối "
            "server để tải model về."
        )
    names = ", ".join(p.name for p in devices)
    raise DetectError(f"Cache có nhiều thiết bị ({names}): chọn một bằng --device-id.")


def collect_images(paths: Sequence[Path]) -> list[Path]:
    """File ảnh giữ nguyên; thư mục thì lấy các ảnh ngay trong đó, theo tên."""
    images: list[Path] = []
    for path in paths:
        if path.is_dir():
            images.extend(
                sorted(
                    f for f in path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES
                )
            )
        elif path.is_file():
            images.append(path)
        else:
            raise DetectError(f"Không thấy ảnh hoặc thư mục {path}")
    if not images:
        raise DetectError("Không có ảnh nào để nhận diện (hỗ trợ .jpg .jpeg .png .bmp .webp).")
    return images


@dataclass
class LoadedModel:
    variant: dict[str, Any]
    model_path: Path
    classifier: Classifier


def open_model(
    cache_dir: Path,
    variant_id: str | None = None,
    labels_path: Path = DEFAULT_LABELS,
    out: Printer = print,
) -> LoadedModel:
    """Chọn model hợp lệ trong cache và nạp vào ONNX Runtime (một lần)."""
    if not labels_path.is_file():
        raise DetectError(f"Thiếu file nhãn {labels_path}.")
    found = verified_variants(cache_dir)
    try:
        variant, model_path, why = choose_local(found, variant_id, load_active(cache_dir))
    except LookupError as exc:
        raise DetectError(
            f"{exc} Cache: {cache_dir}. Hãy chạy client.agent một lần khi có kết nối server."
        ) from exc

    classifier = Classifier(model_path, variant, load_labels(labels_path))
    out(f"Model     : {variant['id']} ({why}), SHA-256 khớp")
    out(f"File      : {model_path}")
    out(f"Nạp model : {classifier.load_ms:.1f} ms (offline, không kết nối server)")
    return LoadedModel(variant, model_path, classifier)


def classify_images(
    model: LoadedModel, images: Sequence[Path], runs: int = 1, out: Printer = print
) -> list[tuple[Path, InferenceResult | None, str | None]]:
    results: list[tuple[Path, InferenceResult | None, str | None]] = []
    for number, image in enumerate(images, start=1):
        out("")
        try:
            result = model.classifier.classify(image, runs)
        except Exception as exc:  # ảnh hỏng/không đọc được: báo và chạy tiếp ảnh sau
            out(f"[{number}/{len(images)}] {image}  [LỖI] {type(exc).__name__}: {exc}")
            results.append((image, None, str(exc)))
            continue
        out(f"[{number}/{len(images)}] {image}  ({result.latency_ms_median:.2f} ms)")
        for line in format_top5(result.top5):
            out(line)
        results.append((image, result, None))
    return results


def detect(
    images: Sequence[Path],
    cache_dir: Path,
    *,
    variant_id: str | None = None,
    labels_path: Path = DEFAULT_LABELS,
    runs: int = 1,
    out: Printer = print,
) -> DetectResult:
    model = open_model(cache_dir, variant_id, labels_path, out)
    results = classify_images(model, images, runs, out)
    return DetectResult(model.variant, model.model_path, model.classifier.load_ms, results)


def detect_interactive(
    cache_dir: Path,
    *,
    variant_id: str | None = None,
    labels_path: Path = DEFAULT_LABELS,
    runs: int = 1,
    out: Printer = print,
    pick: Callable[[], list[Path]] | None = None,
) -> DetectResult:
    """Nạp model một lần, rồi lặp: chọn ảnh trên máy -> nhận diện, đến khi bấm Cancel."""
    pick = pick or pick_images
    model = open_model(cache_dir, variant_id, labels_path, out)
    results: list[tuple[Path, InferenceResult | None, str | None]] = []
    while True:
        out("")
        out("Chọn ảnh trong hộp thoại (chọn được nhiều ảnh); bấm Cancel để thoát.")
        picked = pick()
        if not picked:
            break
        try:
            images = collect_images(picked)
        except DetectError as exc:
            out(f"[LỖI] {exc}")
            continue
        results.extend(classify_images(model, images, runs, out))
    return DetectResult(model.variant, model.model_path, model.classifier.load_ms, results)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m client.detect",
        description="Nhận diện ảnh offline bằng model đã tải về (không cần server).",
    )
    parser.add_argument(
        "--image",
        nargs="+",
        type=Path,
        default=None,
        help="một hoặc nhiều ảnh/thư mục ảnh; bỏ trống thì mở hộp thoại chọn ảnh",
    )
    parser.add_argument("--variant", default=None, help="dùng đúng variant này trong cache")
    parser.add_argument("--device-id", default=None, help="thiết bị đã dùng khi tải model")
    parser.add_argument("--cache-dir", type=Path, default=None, help="mặc định data/client/<id>")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="file synset")
    parser.add_argument("--runs", type=int, default=1, help="số lần chạy mỗi ảnh để lấy trung vị")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs phải >= 1")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    options = {"variant_id": args.variant, "labels_path": args.labels, "runs": args.runs}
    try:
        cache_dir = find_cache_dir(args.cache_dir, args.device_id)
        if args.image is None:
            result = detect_interactive(cache_dir, **options)
        else:
            result = detect(collect_images(args.image), cache_dir, **options)
    except DetectError as exc:
        print(f"[LỖI] {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nĐã dừng (Ctrl+C)")
        return EXIT_ERROR
    except Exception as exc:  # lỗi ONNX/đĩa: báo gọn
        print(f"[LỖI] {type(exc).__name__}: {exc}")
        return EXIT_ERROR
    done = len(result.results) - result.failures
    print("")
    print(f"XONG: {done}/{len(result.results)} ảnh bằng {result.variant['id']}, chạy offline.")
    return EXIT_OK if result.failures == 0 else EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
