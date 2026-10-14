"""Bộ lọc cứng, RuleBasedSelector và FallbackSelector."""

from __future__ import annotations

import math
from collections.abc import Sequence

import pytest
from conftest import MODEL_STORE, REGISTRY_PATH

from server.registry import Registry
from server.selector import (
    Candidate,
    FallbackSelector,
    MLSelectorPlaceholder,
    ModelSelector,
    RuleBasedSelector,
    SelectionContext,
    SelectionResult,
    apply_hard_filters,
    download_seconds,
)

DEADLINE_S = 60


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry(REGISTRY_PATH, MODEL_STORE)


def make_context(bandwidth: float | None, **kw) -> SelectionContext:
    values = dict(
        device_id="laptopA",
        device_class="windows_x64_gt8gb",
        platform="windows",
        cpu_arch="AMD64",
        ram_mb=16384,
        runtime="onnxruntime 1.31.0",
        task="image_classification",
        bandwidth_kbps=bandwidth,
        deadline_s=DEADLINE_S,
        free_storage_mb=None,
    )
    values.update(kw)
    return SelectionContext(**values)


def choose(registry: Registry, bandwidth: float | None, **kw) -> str | None:
    context = make_context(bandwidth, **kw)
    candidates, _ = apply_hard_filters(registry.ready_variants(), context)
    if not candidates:
        return None
    return RuleBasedSelector().select(context, candidates).variant_id


def min_kbps(registry: Registry, variant_id: str) -> float:
    """Băng thông tối thiểu để tải kịp hạn, tính từ size_bytes thật trong Registry."""
    size = registry.get_ready(variant_id).size_bytes
    return size * 8 / 1000 / DEADLINE_S


@pytest.mark.parametrize(
    ("bandwidth", "expected"),
    [
        (100_000, "resnet50_fp32"),
        (5_000, "resnet50_int8"),
        (2_000, "mobilenetv2_fp32"),
        (600, "mobilenetv2_int8"),
        (100, None),  # -> 422 NO_VARIANT_FITS
    ],
)
def test_bandwidth_thresholds_with_real_registry(
    registry: Registry, bandwidth: float, expected: str | None
) -> None:
    assert choose(registry, bandwidth) == expected


def test_thresholds_recomputed_from_sizes(registry: Registry) -> None:
    """Ngay trên/dưới ngưỡng tính từ size_bytes: hệ thống đổi variant đúng chỗ."""
    ranked = sorted(registry.ready_variants(), key=lambda v: -v.top1_accuracy)
    for variant in ranked:
        threshold = min_kbps(registry, variant.variant_id)
        # đủ băng thông cho variant này -> chọn nó hoặc một bản top-1 cao hơn
        chosen = choose(registry, math.ceil(threshold) + 1)
        better = {v.variant_id for v in ranked if v.top1_accuracy >= variant.top1_accuracy}
        assert chosen in better
    smallest = min(registry.ready_variants(), key=lambda v: v.size_bytes)
    assert choose(registry, math.floor(min_kbps(registry, smallest.variant_id)) - 1) is None


def test_unknown_bandwidth_skips_deadline(registry: Registry) -> None:
    best = max(registry.ready_variants(), key=lambda v: v.top1_accuracy)
    assert choose(registry, None) == best.variant_id


def test_storage_filter(registry: Registry) -> None:
    sizes = sorted(v.size_bytes for v in registry.ready_variants())
    # chỉ đủ chỗ cho variant nhỏ nhất
    free_mb = (sizes[0] + 1) / 1024 / 1024
    chosen = choose(registry, 100_000, free_storage_mb=free_mb)
    assert registry.get_ready(chosen).size_bytes == sizes[0]
    assert choose(registry, 100_000, free_storage_mb=0.5) is None


def test_hard_filter_rejections_have_reasons(registry: Registry) -> None:
    candidates, rejections = apply_hard_filters(registry.ready_variants(), make_context(5_000))
    rejected = {r.variant_id: r.reason for r in rejections}
    assert "resnet50_fp32" in rejected
    assert "164.1 s" in rejected["resnet50_fp32"]
    assert all(download_seconds(c.variant.size_bytes, 5_000) <= DEADLINE_S for c in candidates)


def test_reason_mentions_numbers(registry: Registry) -> None:
    context = make_context(5_000)
    candidates, _ = apply_hard_filters(registry.ready_variants(), context)
    result = RuleBasedSelector().select(context, candidates)
    assert "5000 kbps" in result.reason
    assert "41.3 s" in result.reason
    assert "0.7477" in result.reason
    assert result.selector_version == "rule-v1"


def test_class_preference_respected_when_it_fits(registry: Registry) -> None:
    assert choose(registry, 100_000, class_preferred_variant="mobilenetv2_int8") == (
        "mobilenetv2_int8"
    )
    # ghi nhớ nhưng không qua bộ lọc cứng -> quay về top-1 cao nhất
    assert choose(registry, 2_000, class_preferred_variant="resnet50_fp32") == "mobilenetv2_fp32"


def test_tie_breaks_on_smaller_file(registry: Registry) -> None:
    base = registry.ready_variants()[0]
    small = Candidate(base, None)
    from dataclasses import replace

    big = Candidate(replace(base, variant_id="zz_big", size_bytes=base.size_bytes * 2), None)
    result = RuleBasedSelector().select(make_context(None), [big, small])
    assert result.variant_id == base.variant_id


class _Broken(ModelSelector):
    version = "broken"

    def select(self, context, candidates: Sequence[Candidate]) -> SelectionResult:
        raise RuntimeError("model lỗi")


class _Liar(ModelSelector):
    version = "liar"

    def select(self, context, candidates: Sequence[Candidate]) -> SelectionResult:
        return SelectionResult("khong_ton_tai", "bịa", (), self.version)


@pytest.mark.parametrize("primary", [_Broken(), _Liar(), MLSelectorPlaceholder()])
def test_fallback_selector_uses_rule_based(registry: Registry, primary: ModelSelector) -> None:
    context = make_context(5_000)
    candidates, _ = apply_hard_filters(registry.ready_variants(), context)
    selector = FallbackSelector(primary, RuleBasedSelector())
    result = selector.select(context, candidates)
    assert result.variant_id == "resnet50_int8"
    assert result.selector_version == "rule-v1"


def test_fallback_keeps_valid_primary_result(registry: Registry) -> None:
    class _PickSmallest(ModelSelector):
        version = "smallest"

        def select(self, context, candidates):
            c = min(candidates, key=lambda c: c.variant.size_bytes)
            return SelectionResult(c.variant_id, "nhỏ nhất", (), self.version)

    context = make_context(100_000)
    candidates, _ = apply_hard_filters(registry.ready_variants(), context)
    result = FallbackSelector(_PickSmallest(), RuleBasedSelector()).select(context, candidates)
    assert result.variant_id == "mobilenetv2_int8"
    assert result.selector_version == "smallest"
