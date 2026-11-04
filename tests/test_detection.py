"""client.detection: chọn khối detection, NMS, đổi id COCO 91 -> 80, giải mã từng định dạng."""

from __future__ import annotations

import numpy as np

from client.detection import (
    COCO91_TO_80,
    COCO_LABELS,
    decode,
    detection_spec,
    format_detections,
)


def test_spec_from_variant_or_fallback_by_id() -> None:
    spec = {"format": "yolox", "input_size": [416, 416]}
    assert detection_spec({"id": "x", "detection": spec}) is spec
    assert detection_spec({"id": "yolox_tiny"})["input_size"] == [416, 416]
    assert detection_spec({"id": "yolox_m"})["input_size"] == [640, 640]
    assert detection_spec({"id": "ssd_mobilenet_v1_12_int8"})["format"] == "ssd_tf"
    assert detection_spec({"id": "mobilenetv2_int8"}) is None  # model phân loại


def test_coco91_mapping() -> None:
    assert len(COCO_LABELS) == 80
    assert COCO_LABELS[COCO91_TO_80[1]] == "person"
    assert COCO_LABELS[COCO91_TO_80[18]] == "dog"
    assert COCO_LABELS[COCO91_TO_80[90]] == "toothbrush"
    assert COCO91_TO_80[12] == -1  # id bỏ trống


def test_yolox_decode_grid_and_nms() -> None:
    size = 64  # lưới 8x8 + 4x4 + 2x2
    anchors = (size // 8) ** 2 + (size // 16) ** 2 + (size // 32) ** 2
    out = np.zeros((1, anchors, 85), dtype=np.float32)
    # hai dự đoán chồng nhau cùng lớp "dog" ở ô (2, 3) của lưới stride 8, một bản yếu hơn
    for row, obj in ((3 * 8 + 2, 0.9), (3 * 8 + 3, 0.6)):
        out[0, row, :4] = [0.5, 0.5, np.log(4.0), np.log(4.0)]  # tâm lệch nửa ô, rộng 4 ô
        out[0, row, 4] = obj
        out[0, row, 5 + 16] = 1.0
    meta = {"r": 1.0, "width": size, "height": size, "size": (size, size)}
    found = decode([out], {"format": "yolox"}, meta, threshold=0.3)
    assert len(found) == 1  # NMS bỏ bản trùng
    assert found[0].label == "dog" and abs(found[0].score - 0.9) < 1e-6
    assert found[0].box == (4.0, 12.0, 36.0, 44.0)  # tâm (20, 28), cạnh 32


def test_ssd_decode_scales_boxes_and_maps_ids() -> None:
    boxes = np.array([[[0.1, 0.2, 0.5, 0.6], [0, 0, 1, 1]]], dtype=np.float32)
    classes = np.array([[18.0, 12.0]], dtype=np.float32)  # dog, id bỏ trống
    scores = np.array([[0.8, 0.9]], dtype=np.float32)
    count = np.array([2.0], dtype=np.float32)
    meta = {"width": 200, "height": 100}
    found = decode([boxes, classes, scores, count], {"format": "ssd_tf"}, meta, 0.5)
    assert [(d.label, d.box) for d in found] == [("dog", (40.0, 10.0, 120.0, 50.0))]


def test_faster_rcnn_decode_divides_by_scale() -> None:
    boxes = np.array([[10, 20, 30, 40]], dtype=np.float32)
    labels = np.array([17], dtype=np.int64)  # 1..80 -> dog
    scores = np.array([0.7], dtype=np.float32)
    meta = {"scale": 2.0, "width": 100, "height": 100}
    found = decode([boxes, labels, scores], {"format": "faster_rcnn", "class_offset": 1}, meta, 0.5)
    assert found[0].label == "dog" and found[0].box == (5.0, 10.0, 15.0, 20.0)


def test_format_detections() -> None:
    assert "không thấy" in format_detections([])[0]
