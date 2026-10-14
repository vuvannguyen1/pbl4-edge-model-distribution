"""device_class = {platform}_{arch}_{ram} theo mục 7 của protocol."""

from __future__ import annotations

import re

import pytest

from server.devices import arch_bucket, compute_device_class, ram_bucket
from server.schemas import DEVICE_CLASS_PATTERN


@pytest.mark.parametrize(
    ("cpu_arch", "expected"),
    [
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("ARM64", "arm64"),
        ("armv7l", "arm32"),
        ("armv8l", "arm32"),
        ("x86_64", "x64"),
        ("AMD64", "x64"),
        ("amd64", "x64"),
        ("i686", None),
        ("riscv64", None),
    ],
)
def test_arch_bucket(cpu_arch: str, expected: str | None) -> None:
    assert arch_bucket(cpu_arch) == expected


@pytest.mark.parametrize(
    ("ram_mb", "expected"),
    [
        (1, "lt4gb"),
        (4095, "lt4gb"),
        (4096, "4-8gb"),
        (6144, "4-8gb"),
        (8192, "4-8gb"),
        (8193, "gt8gb"),
        (32768, "gt8gb"),
    ],
)
def test_ram_bucket(ram_mb: int, expected: str) -> None:
    assert ram_bucket(ram_mb) == expected


@pytest.mark.parametrize(
    ("platform", "cpu_arch", "ram_mb", "expected"),
    [
        ("android", "aarch64", 6144, "android_arm64_4-8gb"),
        ("ios_browser", "arm64", 4096, "ios_browser_arm64_4-8gb"),
        ("android_browser", "armv7l", 2048, "android_browser_arm32_lt4gb"),
        ("windows", "AMD64", 16384, "windows_x64_gt8gb"),
        ("linux", "x86_64", 8192, "linux_x64_4-8gb"),
    ],
)
def test_device_class(platform: str, cpu_arch: str, ram_mb: int, expected: str) -> None:
    result = compute_device_class(platform, cpu_arch, ram_mb)
    assert result == expected
    assert re.search(DEVICE_CLASS_PATTERN, result)


def test_unknown_arch_raises() -> None:
    with pytest.raises(ValueError):
        compute_device_class("linux", "mips", 4096)
