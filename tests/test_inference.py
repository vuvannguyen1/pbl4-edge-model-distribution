"""Tiền xử lý, nhãn ImageNet, softmax/Top-5 và chạy model thật."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import MODEL_STORE, NORMALIZE, REPO_ROOT, make_test_image, real_registry_variants, requires_models

from client.inference import load_labels, peak_ram_mb, preprocess, run_inference, softmax, top_k

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
def test_every_ready_variant_runs_and_returns_top5(tmp_path: Path, variant_id: str) -> None:
    entry = next(v for v in real_registry_variants() if v["variant_id"] == variant_id)
    image = make_test_image(tmp_path / "img.jpg")
    variant = {
        "input_shape": entry["input_shape"],
        "input_dtype": entry["input_dtype"],
        "normalize": entry["normalize"],
    }
    result = run_inference(
        MODEL_STORE / entry["storage_path"], image, variant, load_labels(LABELS), runs=2
    )
    assert result.input_name == entry["runtime_info"]["input_name"]
    assert len(result.top5) == 5
    assert all(0 <= p.probability <= 1 for p in result.top5)
    assert result.load_ms > 0 and result.latency_ms_median > 0 and result.runs == 2
