"""Chạy model phát hiện vật thể ONNX theo khối `variant.detection` của server.

Khối `detection` (sinh từ Model Card) cho biết cách tiền xử lý và giải mã đầu ra, nên client không
phải đoán theo tên file:

| format        | Đầu vào                                   | Đầu ra                            |
|---------------|-------------------------------------------|-----------------------------------|
| `yolox`       | NCHW, BGR 0..255, letterbox đệm 114       | 1xNx85 thô, giải mã lưới 8/16/32  |
| `ssd_tf`      | NHWC uint8 RGB, kéo giãn về input_size     | hộp chuẩn hoá [y1,x1,y2,x2], 1..90 |
| `faster_rcnn` | CHW BGR trừ mean, giữ tỉ lệ, chia hết 32   | hộp theo pixel, nhãn 1..80        |
| `retinanet`   | NCHW RGB chuẩn hoá ImageNet, đệm 0         | cls/box P3..P7, tự sinh anchor    |

Bản JavaScript cho trình duyệt nằm ở server/webclient/js/worker.js, cùng thuật toán.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
    "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush",
]  # fmt: skip

# SSD (TF Object Detection API) đánh id COCO 1..90, bỏ trống 10 id không thuộc bộ 80 lớp
_COCO91_MISSING = {12, 26, 29, 30, 45, 66, 68, 69, 71, 83}
COCO91_TO_80 = np.full(92, -1, dtype=np.int64)
COCO91_TO_80[[i for i in range(1, 91) if i not in _COCO91_MISSING]] = np.arange(80)

YOLOX_STRIDES = (8, 16, 32)
RETINA_STRIDES = (8, 16, 32, 64, 128)
NMS_IOU = 0.45
MAX_BOXES = 100
DEFAULT_THRESHOLD = 0.35
WARMUP_RUNS = 2

# variant_id -> khối detection, cho variant cũ/thiếu khối này (vd. file mẫu protocol)
_FALLBACK_SPECS: list[tuple[str, dict[str, Any]]] = [
    ("yolox_nano", {"format": "yolox", "input_size": [416, 416], "pad_value": 114.0}),
    ("yolox_tiny", {"format": "yolox", "input_size": [416, 416], "pad_value": 114.0}),
    ("yolox_", {"format": "yolox", "input_size": [640, 640], "pad_value": 114.0}),
    ("ssd_mobilenet", {"format": "ssd_tf", "input_size": [300, 300]}),
    ("fasterrcnn", {"format": "faster_rcnn", "min_side": 512, "max_side": 864, "size_divisor": 32}),
    ("retinanet", {"format": "retinanet", "input_size": [480, 640]}),
]


def detection_spec(variant: dict[str, Any]) -> dict[str, Any] | None:
    """Khối detection của variant; None nếu là model phân loại ảnh."""
    spec = variant.get("detection")
    if spec:
        return spec
    for prefix, fallback in _FALLBACK_SPECS:
        if str(variant.get("id", "")).startswith(prefix):
            return fallback
    return None


@dataclass(frozen=True)
class Detection:
    label: str
    class_index: int
    score: float
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 theo pixel ảnh gốc


@dataclass(frozen=True)
class DetectionResult:
    input_name: str
    load_ms: float
    latency_ms_median: float
    runs: int
    peak_ram_mb: float | None
    detections: list[Detection]


# ---------------------------------------------------------------- tiện ích


def _nms(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray) -> list[int]:
    """NMS theo từng lớp, trả chỉ số giữ lại theo điểm giảm dần."""
    order = np.argsort(-scores)
    keep: list[int] = []
    for i in order:
        ok = True
        for j in keep:
            if classes[i] != classes[j]:
                continue
            x1 = max(boxes[i, 0], boxes[j, 0])
            y1 = max(boxes[i, 1], boxes[j, 1])
            x2 = min(boxes[i, 2], boxes[j, 2])
            y2 = min(boxes[i, 3], boxes[j, 3])
            inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
            area_j = (boxes[j, 2] - boxes[j, 0]) * (boxes[j, 3] - boxes[j, 1])
            if inter / max(area_i + area_j - inter, 1e-9) >= NMS_IOU:
                ok = False
                break
        if ok:
            keep.append(int(i))
            if len(keep) >= MAX_BOXES:
                break
    return keep


def _to_detections(
    boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray, width: int, height: int
) -> list[Detection]:
    if len(scores) == 0:
        return []
    boxes = boxes.astype(np.float32).copy()
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
    result = []
    for i in _nms(boxes, scores, classes):
        cls = int(classes[i])
        result.append(
            Detection(
                label=COCO_LABELS[cls] if 0 <= cls < len(COCO_LABELS) else f"class_{cls}",
                class_index=cls,
                score=float(scores[i]),
                box=tuple(round(float(v), 1) for v in boxes[i]),  # type: ignore[arg-type]
            )
        )
    return result


def _normalize(rgb: np.ndarray, variant: dict[str, Any], color: str) -> np.ndarray:
    """(x/255 - mean)/std theo khối normalize, với kênh xếp theo `color`."""
    pixels = rgb[..., ::-1] if color == "BGR" else rgb
    norm = variant.get("normalize") or {"mean": [0, 0, 0], "std": [1 / 255] * 3}
    mean = np.asarray(norm["mean"], dtype=np.float32)
    std = np.asarray(norm["std"], dtype=np.float32)
    return ((pixels.astype(np.float32) / 255.0) - mean) / std


def _retina_anchors(stride: int) -> np.ndarray:
    """9 anchor (3 scale x 3 tỉ lệ) theo NVIDIA retinanet-examples: scale ở vòng ngoài."""
    anchors = []
    for scale in (4 * 2 ** (i / 3) for i in range(3)):
        for ratio in (1.0, 2.0, 0.5):
            w = np.sqrt(stride * stride / ratio)
            h = w * ratio
            anchors.append(
                [0.5 * (stride - w * scale), 0.5 * (stride - h * scale),
                 0.5 * (stride + w * scale), 0.5 * (stride + h * scale)]
            )  # fmt: skip
    return np.asarray(anchors, dtype=np.float32)


# ---------------------------------------------------------------- tiền xử lý + giải mã


def prepare(
    image: Image.Image, variant: dict[str, Any], spec: dict[str, Any]
) -> tuple[np.ndarray, dict]:
    """Ảnh RGB -> (tensor đầu vào, thông tin để đổi toạ độ về ảnh gốc)."""
    fmt = spec["format"]
    width, height = image.size
    if fmt == "ssd_tf":
        h, w = spec.get("input_size") or [300, 300]
        arr = np.asarray(image.resize((w, h), Image.BILINEAR), dtype=np.uint8)
        return arr[np.newaxis], {"width": width, "height": height}

    if fmt == "faster_rcnn":
        scale = min(
            spec.get("min_side", 512) / min(width, height),
            spec.get("max_side", 864) / max(width, height),
        )
        w, h = round(width * scale), round(height * scale)
        div = spec.get("size_divisor", 32)
        arr = _normalize(
            np.asarray(image.resize((w, h), Image.BILINEAR)), variant, spec.get("color", "BGR")
        )
        padded = np.zeros((-(-h // div) * div, -(-w // div) * div, 3), dtype=np.float32)
        padded[:h, :w] = arr
        return np.ascontiguousarray(padded.transpose(2, 0, 1)), {
            "scale": scale,
            "width": width,
            "height": height,
        }

    # yolox, retinanet: letterbox góc trên trái
    H, W = spec["input_size"]
    r = min(W / width, H / height)
    resized = np.asarray(image.resize((round(width * r), round(height * r)), Image.BILINEAR))
    pad = spec.get("pad_value")
    if pad is not None:  # đệm điểm ảnh thô rồi mới chuẩn hoá (YOLOX: 114)
        canvas = np.full((H, W, 3), pad, dtype=np.float32)
        canvas[: resized.shape[0], : resized.shape[1]] = resized
        arr = _normalize(canvas, variant, spec.get("color", "BGR"))
    else:  # đệm 0 sau khi chuẩn hoá (RetinaNet)
        arr = np.zeros((H, W, 3), dtype=np.float32)
        arr[: resized.shape[0], : resized.shape[1]] = _normalize(
            resized, variant, spec.get("color", "RGB")
        )
    tensor = np.ascontiguousarray(arr.transpose(2, 0, 1)[np.newaxis], dtype=np.float32)
    return tensor, {"r": r, "width": width, "height": height, "size": (H, W)}


def decode(
    outputs: list[np.ndarray], spec: dict[str, Any], meta: dict, threshold: float
) -> list[Detection]:
    fmt = spec["format"]
    width, height = meta["width"], meta["height"]

    if fmt == "yolox":
        out = outputs[0][0]
        H, W = meta["size"]
        grids, strides = [], []
        for stride in YOLOX_STRIDES:
            gy, gx = np.meshgrid(np.arange(H // stride), np.arange(W // stride), indexing="ij")
            grids.append(np.stack([gx.ravel(), gy.ravel()], axis=1))
            strides.append(np.full(gx.size, stride))
        grid = np.concatenate(grids).astype(np.float32)
        stride = np.concatenate(strides).astype(np.float32)[:, None]
        cls = out[:, 5:].argmax(axis=1)
        scores = out[:, 4] * out[np.arange(len(out)), 5 + cls]
        keep = scores >= threshold
        xy = (out[keep, :2] + grid[keep]) * stride[keep]
        wh = np.exp(out[keep, 2:4]) * stride[keep]
        boxes = np.concatenate([xy - wh / 2, xy + wh / 2], axis=1) / meta["r"]
        return _to_detections(boxes, scores[keep], cls[keep], width, height)

    if fmt == "ssd_tf":
        boxes, classes, scores, count = outputs
        n = int(count[0])
        cls = COCO91_TO_80[np.clip(classes[0, :n].round().astype(np.int64), 0, 91)]
        keep = (scores[0, :n] >= threshold) & (cls >= 0)
        b = boxes[0, :n][keep]
        pixel = np.stack(
            [b[:, 1] * width, b[:, 0] * height, b[:, 3] * width, b[:, 2] * height], axis=1
        )
        return _to_detections(pixel, scores[0, :n][keep], cls[keep], width, height)

    if fmt == "faster_rcnn":
        boxes, labels, scores = outputs
        keep = scores >= threshold
        cls = labels[keep].astype(np.int64) - int(spec.get("class_offset", 1))
        return _to_detections(boxes[keep] / meta["scale"], scores[keep], cls, width, height)

    if fmt == "retinanet":
        found_boxes, found_scores, found_cls = [], [], []
        levels = len(outputs) // 2
        heads = zip(outputs[:levels], outputs[levels:], strict=True)
        for level, (cls_head, box_head) in enumerate(heads):
            stride = RETINA_STRIDES[level]
            _, channels, h, w = cls_head.shape
            num_classes = channels // 9
            cls_map = cls_head[0].reshape(9, num_classes, h, w)
            anchor_idx, cls_idx, ys, xs = np.nonzero(cls_map >= threshold)
            if len(xs) == 0:
                continue
            deltas = box_head[0].reshape(9, 4, h, w)[anchor_idx, :, ys, xs]
            grid = np.stack([xs, ys, xs, ys], axis=1).astype(np.float32) * stride
            anchors = grid + _retina_anchors(stride)[anchor_idx]
            awh = anchors[:, 2:] - anchors[:, :2] + 1
            ctr = anchors[:, :2] + 0.5 * awh
            pred_ctr = deltas[:, :2] * awh + ctr
            pred_wh = np.exp(deltas[:, 2:]) * awh
            found_boxes.append(
                np.concatenate([pred_ctr - 0.5 * pred_wh, pred_ctr + 0.5 * pred_wh - 1], axis=1)
            )
            found_scores.append(cls_map[anchor_idx, cls_idx, ys, xs])
            found_cls.append(cls_idx)
        if not found_boxes:
            return []
        return _to_detections(
            np.concatenate(found_boxes) / meta["r"],
            np.concatenate(found_scores),
            np.concatenate(found_cls),
            width,
            height,
        )

    raise ValueError(f"Định dạng đầu ra '{fmt}' chưa được hỗ trợ.")


class Detector:
    """Nạp model phát hiện vật thể một lần rồi chạy trên nhiều ảnh (warm-up ở ảnh đầu)."""

    def __init__(
        self, model_path: Path, variant: dict[str, Any], spec: dict[str, Any] | None = None
    ) -> None:
        import onnxruntime as ort

        self.spec = spec or detection_spec(variant)
        if self.spec is None:
            raise ValueError(f"{variant.get('id')} không phải model phát hiện vật thể.")
        start = time.perf_counter()
        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.load_ms = (time.perf_counter() - start) * 1000
        self.input_name = self.session.get_inputs()[0].name
        self.variant = variant
        self._warmed_up = False

    def detect_image(
        self, image: Image.Image, threshold: float = DEFAULT_THRESHOLD, runs: int = 1
    ) -> tuple[list[Detection], list[float]]:
        tensor, meta = prepare(image.convert("RGB"), self.variant, self.spec)
        feeds = {self.input_name: tensor}
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
        return decode(outputs, self.spec, meta, threshold), latencies

    def detect(
        self, image_path: Path, runs: int = 5, threshold: float = DEFAULT_THRESHOLD
    ) -> DetectionResult:
        from client.inference import peak_ram_mb

        with Image.open(image_path) as img:
            detections, latencies = self.detect_image(img, threshold, runs)
        return DetectionResult(
            input_name=self.input_name,
            load_ms=self.load_ms,
            latency_ms_median=statistics.median(latencies),
            runs=len(latencies),
            peak_ram_mb=peak_ram_mb(),
            detections=detections,
        )


def format_detections(detections: list[Detection], limit: int = 10) -> list[str]:
    """Bảng vật thể để in ra console."""
    if not detections:
        return ["  (không thấy vật thể nào trên ngưỡng tin cậy)"]
    lines = [
        "   #  Vật thể             Tin cậy  Hộp (x1, y1, x2, y2)",
        "  ──  ──────────────────  ───────  ────────────────────────",
    ]
    for rank, d in enumerate(detections[:limit], start=1):
        box = ", ".join(f"{v:.0f}" for v in d.box)
        lines.append(f"  {rank:>2}  {d.label[:18]:<18}  {d.score * 100:6.1f}%  ({box})")
    if len(detections) > limit:
        lines.append(f"  … và {len(detections) - limit} vật thể khác")
    return lines
