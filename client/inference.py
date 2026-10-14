"""Chạy model ONNX phân loại ảnh: tiền xử lý, Top-5, đo load/latency/RAM.

`Classifier` nạp model một lần rồi phân loại nhiều ảnh; `run_inference` là
cách gọi gọn cho một ảnh.

Tiền xử lý lấy từ khối `variant` (input_shape, input_dtype, normalize):
RGB -> resize cạnh ngắn (bilinear, giữ tỉ lệ) -> cắt giữa -> /255 -> chuẩn hóa
mean/std -> NCHW float32 có chiều batch. Với ảnh 224x224, cạnh ngắn resize về 256.
"""

from __future__ import annotations

import ctypes
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

WARMUP_RUNS = 3
RESIZE_RATIO = 256 / 224  # resize cạnh ngắn 256 rồi cắt 224, theo Model Card


@dataclass(frozen=True)
class Prediction:
    index: int
    label: str
    probability: float


@dataclass(frozen=True)
class InferenceResult:
    input_name: str
    load_ms: float
    latency_ms_median: float
    runs: int
    peak_ram_mb: float | None
    top5: list[Prediction]


def load_labels(path: Path) -> list[str]:
    """Đọc synset: dòng `n01440764 tench, Tinca tinca` -> nhãn `tench`."""
    labels: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        _, _, names = line.strip().partition(" ")
        labels.append(names.split(",")[0].strip())
    return labels


def preprocess(image_path: Path, variant: dict[str, Any]) -> np.ndarray:
    shape = variant["input_shape"]
    height, width = int(shape[-2]), int(shape[-1])
    with Image.open(image_path) as img:
        img = img.convert("RGB")
        short_target = int(round(min(height, width) * RESIZE_RATIO))
        scale = short_target / min(img.size)
        new_size = (max(width, round(img.width * scale)), max(height, round(img.height * scale)))
        img = img.resize(new_size, Image.BILINEAR)
        left = (img.width - width) // 2
        top = (img.height - height) // 2
        img = img.crop((left, top, left + width, top + height))
        array = np.asarray(img, dtype=np.float32)  # HWC, 0..255

    if variant.get("input_dtype") == "uint8":
        tensor = array.astype(np.uint8)
    else:
        mean = np.asarray(variant["normalize"]["mean"], dtype=np.float32)
        std = np.asarray(variant["normalize"]["std"], dtype=np.float32)
        tensor = ((array / 255.0) - mean) / std
    tensor = np.transpose(tensor, (2, 0, 1))[np.newaxis, ...]  # NCHW
    return np.ascontiguousarray(
        tensor, dtype=np.uint8 if variant.get("input_dtype") == "uint8" else np.float32
    )


def softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits)
    exp = np.exp(shifted)
    return exp / np.sum(exp)


def top_k(probabilities: np.ndarray, labels: list[str], k: int = 5) -> list[Prediction]:
    order = np.argsort(probabilities)[::-1][:k]
    return [
        Prediction(
            index=int(i),
            label=labels[i] if i < len(labels) else f"class_{i}",
            probability=float(probabilities[i]),
        )
        for i in order
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def peak_ram_mb() -> float | None:
    """RAM đỉnh của tiến trình (MB). None nếu không đo được."""
    try:
        if sys.platform.startswith("win"):
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            process = kernel32.GetCurrentProcess()
            psapi.GetProcessMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(_ProcessMemoryCounters),
                ctypes.c_ulong,
            ]
            if psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):
                return round(counters.PeakWorkingSetSize / 1024 / 1024, 1)
            return None
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)  # KiB -> MB
    except (OSError, AttributeError, ImportError, ValueError):
        return None


class Classifier:
    """Nạp model ONNX một lần rồi phân loại nhiều ảnh (warm-up chỉ ở ảnh đầu tiên)."""

    def __init__(self, model_path: Path, variant: dict[str, Any], labels: list[str]) -> None:
        import onnxruntime as ort

        start = time.perf_counter()
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.load_ms = (time.perf_counter() - start) * 1000
        # mobilenetv2: "input", resnet50: "data"
        self.input_name = self.session.get_inputs()[0].name
        self.variant = variant
        self.labels = labels
        self._warmed_up = False

    def classify(self, image_path: Path, runs: int = 20) -> InferenceResult:
        feeds = {self.input_name: preprocess(image_path, self.variant)}
        if not self._warmed_up:
            for _ in range(WARMUP_RUNS):
                self.session.run(None, feeds)
            self._warmed_up = True
        latencies: list[float] = []
        outputs: list[Any] = []
        for _ in range(max(1, runs)):
            t0 = time.perf_counter()
            outputs = self.session.run(None, feeds)
            latencies.append((time.perf_counter() - t0) * 1000)

        logits = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        probabilities = softmax(logits)
        return InferenceResult(
            input_name=self.input_name,
            load_ms=self.load_ms,
            latency_ms_median=statistics.median(latencies),
            runs=len(latencies),
            peak_ram_mb=peak_ram_mb(),
            top5=top_k(probabilities, self.labels, 5),
        )


def run_inference(
    model_path: Path,
    image_path: Path,
    variant: dict[str, Any],
    labels: list[str],
    runs: int = 20,
) -> InferenceResult:
    return Classifier(model_path, variant, labels).classify(image_path, runs)


def format_top5(top5: list[Prediction]) -> list[str]:
    """Bảng Top-5 để in ra console (dùng chung cho agent và detect)."""
    lines = [
        "   #  Nhãn                              Xác suất",
        "  ──  ────────────────────────────────  ────────",
    ]
    for rank, pred in enumerate(top5, start=1):
        lines.append(f"  {rank:>2}  {pred.label[:32]:<32}  {pred.probability * 100:7.2f} %")
    return lines
