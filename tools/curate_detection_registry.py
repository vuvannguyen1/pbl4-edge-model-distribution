"""Regenerate the registry after curating the object-detection model store."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry" / "model_registry.json"
STORE = ROOT / "models" / "CV"
CHUNK = 1024 * 1024


def hashes(path: Path) -> tuple[str, list[dict[str, object]]]:
    whole = hashlib.sha256()
    chunks: list[dict[str, object]] = []
    with path.open("rb") as stream:
        index = 0
        while block := stream.read(CHUNK):
            whole.update(block)
            chunks.append({
                "index": index,
                "offset": index * CHUNK,
                "size_bytes": len(block),
                "sha256": hashlib.sha256(block).hexdigest(),
            })
            index += 1
    return whole.hexdigest(), chunks


def main() -> None:
    old = json.loads(REGISTRY.read_text(encoding="utf-8"))
    variants = [
        entry for entry in old["variants"]
        if entry.get("task_id") == "object_detection"
        and (ROOT / "models" / entry["storage_path"]).is_file()
    ]
    additions = [
        ("ssd_mobilenet_v1_12", "ssd_mobilenet_v1_12.onnx", "SSD-MobileNetV1", "fp32", [1, "height", "width", 3], "uint8", "NHWC", ["detection_boxes", "detection_classes", "detection_scores", "num_detections"]),
        ("ssd_mobilenet_v1_12_int8", "ssd_mobilenet_v1_12_int8.onnx", "SSD-MobileNetV1", "int8", [1, "height", "width", 3], "uint8", "NHWC", ["detection_boxes", "detection_classes", "detection_scores", "num_detections"]),
        ("fasterrcnn_r50_12_int8", "fasterrcnn_r50_12_int8.onnx", "Faster R-CNN R-50-FPN", "int8", [3, "height", "width"], "float", "NCHW", ["boxes", "labels", "scores"]),
        ("retinanet_resnet101", "retinanet_resnet101.onnx", "RetinaNet ResNet101", "fp32", [1, 3, 480, 640], "float", "NCHW", ["classification_heads", "box_regression_heads"]),
    ]
    source_urls = {
        "ssd": "https://github.com/onnx/models/tree/main/validated/vision/object_detection_segmentation/ssd-mobilenetv1",
        "faster": "https://github.com/onnx/models/tree/main/validated/vision/object_detection_segmentation/faster-rcnn",
        "retina": "https://github.com/onnx/models/tree/main/validated/vision/object_detection_segmentation/retinanet",
    }
    for variant_id, filename, family, precision, shape, dtype, layout, outputs in additions:
        path = STORE / filename
        sha256, chunks = hashes(path)
        source = source_urls["ssd" if variant_id.startswith("ssd") else "faster" if variant_id.startswith("faster") else "retina"]
        variants.append({
            "variant_id": variant_id,
            "model_id": variant_id.rsplit("_", 1)[0],
            "version": "1",
            "task_id": "object_detection",
            "task_group": "CV",
            "file": filename,
            "storage_path": f"CV/{filename}",
            "base_model": variant_id.rsplit("_", 1)[0],
            "family": family,
            "level": precision,
            "precision": precision,
            "size_bytes": path.stat().st_size,
            "sha256": sha256,
            "chunk_size": CHUNK,
            "chunk_count": len(chunks),
            "chunks": chunks,
            "top1_accuracy": None,
            "input_shape": shape,
            "input_dtype": dtype,
            "normalize": None,
            "preprocessing": {"layout": layout, "color": "RGB", "scale": "0..255" if dtype == "uint8" else "1/255"},
            "output_contract": {"type": "object_detection", "outputs": outputs, "class_map": "COCO-80"},
            "supported_runtimes": ["onnxruntime"],
            "minimum_ram_mb": None,
            "runtime_info": {"output_names": outputs},
            "status": "ready",
            "validation_errors": [],
            "warnings": ["Benchmark and model-specific postprocessing are still required."],
            "provenance": {"source_url": source},
        })
    old["variants"] = variants
    old["generated_at"] = "2026-11-03T00:00:00+00:00"
    REGISTRY.write_text(json.dumps(old, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("registered object-detection variants:", ", ".join(v["variant_id"] for v in variants))


if __name__ == "__main__":
    main()
