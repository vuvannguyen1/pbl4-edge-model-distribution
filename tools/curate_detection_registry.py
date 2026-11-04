"""Sinh Model Card cho 10 model phát hiện vật thể trong models/CV/, rồi sinh lại Registry.

Chạy từ thư mục gốc repo (sau `python tools/download_models.py`):
    python tools/curate_detection_registry.py

Model Card ghi đủ thứ client cần để chạy model mà không phải đoán theo tên file:
- `contract.detection`: cách tiền xử lý (letterbox/stretch/giữ tỉ lệ, RGB/BGR, chuẩn hoá) và
  định dạng đầu ra (`yolox`, `ssd_tf`, `faster_rcnn`, `retinanet`), bảng nhãn COCO.
- `quality.map50_95`: mAP COCO val2017 theo công bố gốc (YOLOX README, ONNX Model Zoo), chưa đo lại.
- `compute.gflops`: GFLOPs ở kích thước đầu vào của card, Selector dùng để ước lượng thời gian
  suy luận trên từng lớp thiết bị.
- `requirements.supported_runtimes`: `onnxruntime-web` = chạy được trong trình duyệt (trang /app).
  yolox_l/x và RetinaNet quá nặng cho trình duyệt điện thoại nên chỉ dành cho client Python.

Card đã có trong metadata/model_cards/ bị ghi đè, trừ `lifecycle.status` đặt tay (disabled/retired).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CARDS_DIR = ROOT / "metadata" / "model_cards"

YOLOX_SRC = "https://github.com/Megvii-BaseDetection/YOLOX (ONNX: huggingface Heliosoph/yolox-onnx)"
ZOO = "https://github.com/onnx/models/tree/main/validated/vision/object_detection_segmentation/"
WEB = ["onnxruntime", "onnxruntime-web"]
NATIVE = ["onnxruntime"]

# Nhân 1/255 để client tính (x/255 - mean)/std ra đúng thang 0..255 mà YOLOX/Faster R-CNN cần
UNIT = 1 / 255
IMAGENET = {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]}
RAW_0_255 = {"mean": [0.0, 0.0, 0.0], "std": [UNIT, UNIT, UNIT]}
CAFFE_BGR = {"mean": [102.9801 / 255, 115.9465 / 255, 122.7717 / 255], "std": [UNIT, UNIT, UNIT]}


def yolox(size: int) -> dict[str, Any]:
    return {
        "format": "yolox",
        "input_size": [size, size],
        "resize": "letterbox",
        "layout": "NCHW",
        "color": "BGR",
        "pad_value": 114.0,
        "label_map": "coco80",
        "outputs": ["output"],
    }


SSD = {
    "format": "ssd_tf",
    "input_size": [300, 300],
    "resize": "stretch",
    "layout": "NHWC",
    "color": "RGB",
    "pad_value": None,
    "label_map": "coco91",
    "outputs": ["detection_boxes", "detection_classes", "detection_scores", "num_detections"],
}
FASTER_RCNN = {
    "format": "faster_rcnn",
    "input_size": [512, 512],
    "resize": "keep_ratio",
    "min_side": 512,
    "max_side": 864,
    "size_divisor": 32,
    "layout": "CHW",
    "color": "BGR",
    "pad_value": None,  # đệm 0 sau khi trừ mean
    "label_map": "coco80",
    "class_offset": 1,
    "outputs": ["boxes", "labels", "scores"],
}
RETINANET = {
    "format": "retinanet",
    "input_size": [480, 640],
    "resize": "letterbox",
    "layout": "NCHW",
    "color": "RGB",
    "pad_value": None,  # đệm 0 sau khi chuẩn hoá
    "label_map": "coco80",
    "outputs": ["cls_p3..p7", "box_p3..p7"],
}

# variant_id, file, model_id, họ, precision, level, input_shape, dtype, normalize, detection,
# mAP, GFLOPs, RAM tối thiểu (MB), runtime, nguồn
MODELS: list[tuple[Any, ...]] = [
    (
        "yolox_nano",
        "yolox_nano.onnx",
        "yolox",
        "YOLOX",
        "fp32",
        0,
        [1, 3, 416, 416],
        "float",
        RAW_0_255,
        yolox(416),
        0.258,
        1.08,
        160,
        WEB,
        YOLOX_SRC,
    ),
    (
        "yolox_tiny",
        "yolox_tiny.onnx",
        "yolox",
        "YOLOX",
        "fp32",
        0,
        [1, 3, 416, 416],
        "float",
        RAW_0_255,
        yolox(416),
        0.328,
        6.45,
        280,
        WEB,
        YOLOX_SRC,
    ),
    (
        "yolox_s",
        "yolox_s.onnx",
        "yolox",
        "YOLOX",
        "fp32",
        0,
        [1, 3, 640, 640],
        "float",
        RAW_0_255,
        yolox(640),
        0.405,
        26.8,
        430,
        WEB,
        YOLOX_SRC,
    ),
    (
        "yolox_m",
        "yolox_m.onnx",
        "yolox",
        "YOLOX",
        "fp32",
        0,
        [1, 3, 640, 640],
        "float",
        RAW_0_255,
        yolox(640),
        0.472,
        73.8,
        760,
        WEB,
        YOLOX_SRC,
    ),
    (
        "yolox_l",
        "yolox_l.onnx",
        "yolox",
        "YOLOX",
        "fp32",
        0,
        [1, 3, 640, 640],
        "float",
        RAW_0_255,
        yolox(640),
        0.501,
        155.6,
        1250,
        NATIVE,
        YOLOX_SRC,
    ),
    (
        "yolox_x",
        "yolox_x.onnx",
        "yolox",
        "YOLOX",
        "fp32",
        0,
        [1, 3, 640, 640],
        "float",
        RAW_0_255,
        yolox(640),
        0.515,
        281.9,
        1900,
        NATIVE,
        YOLOX_SRC,
    ),
    (
        "ssd_mobilenet_v1_12",
        "ssd_mobilenet_v1_12.onnx",
        "ssd_mobilenet_v1",
        "SSD-MobileNetV1",
        "fp32",
        0,
        [1, 300, 300, 3],
        "uint8",
        {"mean": [0.0, 0.0, 0.0], "std": [1.0, 1.0, 1.0]},
        SSD,
        0.23,
        2.4,
        200,
        WEB,
        ZOO + "ssd-mobilenetv1",
    ),
    (
        "ssd_mobilenet_v1_12_int8",
        "ssd_mobilenet_v1_12_int8.onnx",
        "ssd_mobilenet_v1",
        "SSD-MobileNetV1",
        "int8",
        1,
        [1, 300, 300, 3],
        "uint8",
        {"mean": [0.0, 0.0, 0.0], "std": [1.0, 1.0, 1.0]},
        SSD,
        0.2297,
        2.4,
        150,
        WEB,
        ZOO + "ssd-mobilenetv1",
    ),
    (
        "fasterrcnn_r50_12_int8",
        "fasterrcnn_r50_12_int8.onnx",
        "faster_rcnn_r50_fpn",
        "Faster R-CNN R-50-FPN",
        "int8",
        1,
        [3, 512, 512],
        "float",
        CAFFE_BGR,
        FASTER_RCNN,
        0.34,
        85.0,
        900,
        WEB,
        ZOO + "faster-rcnn",
    ),
    (
        "retinanet_resnet101",
        "retinanet_resnet101.onnx",
        "retinanet_r101",
        "RetinaNet ResNet-101",
        "fp32",
        0,
        [1, 3, 480, 640],
        "float",
        IMAGENET,
        RETINANET,
        0.376,
        95.0,
        1600,
        NATIVE,
        ZOO + "retinanet",
    ),
]


def card(row: tuple[Any, ...]) -> dict[str, Any]:
    (
        variant_id,
        file,
        model_id,
        family,
        precision,
        level,
        shape,
        dtype,
        normalize,
        detection,
        map50_95,
        gflops,
        ram_mb,
        runtimes,
        source,
    ) = row
    return {
        "model_id": model_id,
        "variant_id": variant_id,
        "version": "1",
        "artifact": {"file": f"CV/{file}"},
        "task": {"id": "object_detection", "group": "CV"},
        "model": {"family": family, "precision": precision, "compression_level": level},
        "contract": {
            "input_shape": shape,
            "input_dtype": dtype,
            "preprocessing": {"normalize": normalize},
            "detection": detection,
        },
        "quality": {
            "top1_accuracy": None,
            "map50_95": map50_95,
            "source": "COCO val2017 theo công bố gốc, chưa đo lại trên thiết bị của nhóm",
        },
        "compute": {"gflops": gflops},
        "requirements": {"supported_runtimes": runtimes, "minimum_ram_mb": ram_mb},
        "provenance": {"source_url": source},
    }


def main() -> int:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    for row in MODELS:
        data = card(row)
        path = CARDS_DIR / f"{data['variant_id']}.json"
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("lifecycle", {}).get("status") in {"disabled", "retired"}:
                data["lifecycle"] = old["lifecycle"]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[card] {path.relative_to(ROOT).as_posix()}")
    return subprocess.call([sys.executable, str(ROOT / "tools" / "build_registry.py")])


if __name__ == "__main__":
    sys.exit(main())
