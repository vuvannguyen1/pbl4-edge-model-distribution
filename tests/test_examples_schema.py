"""9 file mẫu trong docs/examples/ phải hợp lệ theo $defs tương ứng của schema."""

from __future__ import annotations

import json

import pytest
from validate_examples import EXAMPLE_DEFS, example_files, schema_errors


def test_all_nine_examples_present() -> None:
    prefixes = {path.name[:2] for path, _ in example_files()}
    assert prefixes == set(EXAMPLE_DEFS)


@pytest.mark.parametrize(("path", "def_name"), example_files(), ids=lambda v: getattr(v, "name", v))
def test_example_matches_schema(path, def_name) -> None:
    instance = json.loads(path.read_text(encoding="utf-8"))
    assert schema_errors(def_name, instance) == []


def test_schema_rejects_extra_field() -> None:
    """Đảm bảo validator thật sự chặn trường thừa (additionalProperties: false)."""
    path = next(p for p, d in example_files() if d == "RegisterRequest")
    instance = json.loads(path.read_text(encoding="utf-8"))
    instance["ip"] = "10.0.0.1"
    assert schema_errors("RegisterRequest", instance)
