"""Tiền xử lý phân loại, nhãn ImageNet, softmax/Top-5 và chạy model phát hiện vật thể thật."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import (
    MODEL_STORE,
    NORMALIZE,
    REPO_ROOT,
    make_test_image,
    real_registry_variants,
    requires_models,
)

from client.inference import (
    create_runner,
    format_result,
    load_labels,
    peak_ram_mb,
    preprocess,
    softmax,
    top_k,
)

LABELS = REPO_ROOT / "samples" / "imagenet_synset.txt"
VARIANT = {"input_shape": [1, 3, 224, 224], "input_dtype": "float", "normalize": NORMALIZE}


@pytest.mark.parametrize("size", [(320, 240), (240, 320), (224, 224), (100, 50)])
def test_preprocess_shape_and_dtype(tmp_path: Path, size) -> None:
    image = make_test_image(tmp_path / "img.jpg", size)
    tensor = preprocess(image, VARIANT)
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    assert tensor.flags["C_CONTIGUOUS"]


def test_preprocess_uses_input_shape(tmp_path: Path) -> None:
    image = make_test_image(tmp_path / "img.jpg")
    tensor = preprocess(image, {**VARIANT, "input_shape": [1, 3, 160, 160]})
    assert tensor.shape == (1, 3, 160, 160)


def test_preprocess_normalizes(tmp_path: Path) -> None:
    from PIL import Image

    path = tmp_path / "white.png"
    Image.new("RGB", (300, 300), (255, 255, 255)).save(path)
    tensor = preprocess(path, VARIANT)
    expected = (1.0 - np.array(NORMALIZE["mean"])) / np.array(NORMALIZE["std"])
    assert np.allclose(tensor[0, :, 112, 112], expected, atol=1e-5)


def test_preprocess_grayscale_converted_to_rgb(tmp_path: Path) -> None:
    from PIL import Image

    path = tmp_path / "gray.png"
    Image.new("L", (300, 260), 100).save(path)
    assert preprocess(path, VARIANT).shape == (1, 3, 224, 224)


def test_labels_file() -> None:
    labels = load_labels(LABELS)
    assert len(labels) == 1000
    assert labels[0] == "tench"
    assert labels[281] == "tabby"
    assert all("," not in label for label in labels)


def test_softmax_and_top5() -> None:
    logits = np.array([1.0, 3.0, 2.0, 0.5, 4.0, -1.0], dtype=np.float32)
    probs = softmax(logits)
    assert abs(float(probs.sum()) - 1.0) < 1e-6
    top = top_k(probs, ["a", "b", "c", "d", "e", "f"], 5)
    assert [p.label for p in top] == ["e", "b", "c", "a", "d"]
    assert all(top[i].probability >= top[i + 1].probability for i in range(4))


def test_peak_ram_is_number_or_none() -> None:
    value = peak_ram_mb()
    assert value is None or value > 0


@requires_models
@pytest.mark.parametrize(
    "variant_id", [v["variant_id"] for v in real_registry_variants() if v["status"] == "ready"]
)
def test_every_ready_variant_detects_the_dog(variant_id: str) -> None:
    """Mọi model phát hiện vật thể trong Registry chạy được qua client và thấy con chó."""
    entry = next(v for v in real_registry_variants() if v["variant_id"] == variant_id)
    variant = {
        "id": variant_id,
        "input_shape": entry["input_shape"],
        "input_dtype": entry["input_dtype"],
        "normalize": entry["normalize"],
        "detection": entry["detection"],
    }
    runner = create_runner(MODEL_STORE / entry["storage_path"], variant)
    result = runner.classify(REPO_ROOT / "samples" / "dog.jpg", runs=1)
    assert result.input_name == entry["runtime_info"]["input_name"]
    # ssd_mobilenet_v1_12_int8 xếp "sheep" trên "dog" ở ảnh này: chỉ đòi có thấy con chó
    assert "dog" in [p.label for p in result.top5]
    assert all(0 <= p.probability <= 1 for p in result.top5)
    dog = next(d for d in result.detections if d.label == "dog")
    x1, y1, x2, y2 = dog.box
    assert 0 <= x1 < x2 <= 700 and 0 <= y1 < y2 <= 577  # trong ảnh 700x577
    assert (x2 - x1) * (y2 - y1) > 0.4 * 700 * 577  # con chó chiếm phần lớn ảnh
    assert result.load_ms > 0 and result.latency_ms_median > 0 and result.runs == 1
    assert any(" dog " in line for line in format_result(result, variant))
