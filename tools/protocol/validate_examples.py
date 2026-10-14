"""Kiểm tra các file mẫu trong docs/examples/ khớp docs/protocol.schema.json.

Chạy từ thư mục gốc repo:
    python tools/protocol/validate_examples.py

Module này cũng được test dùng lại (tests/test_examples_schema.py) để kiểm
mọi JSON server trả và client gửi theo đúng $defs của schema.
"""

from __future__ import annotations

import json
import sys
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "docs" / "protocol.schema.json"
EXAMPLES_DIR = REPO_ROOT / "docs" / "examples"

# Tiền tố số của file mẫu -> tên $defs tương ứng
EXAMPLE_DEFS: dict[str, str] = {
    "01": "RegisterRequest",
    "02": "RegisterResponse",
    "03": "ModelRequest",
    "04": "ModelResponse",
    "05": "HaveRequest",
    "06": "HaveResponse",
    "07": "FeedbackRequest",
    "08": "FeedbackResponse",
    "09": "Error",
    "10": "ManualFeedbackRequest",
    "11": "ManualFeedbackResponse",
}


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@cache
def validator_for(def_name: str) -> Draft202012Validator:
    """Validator cho một mục $defs, giữ nguyên $defs để $ref nội bộ hoạt động."""
    root = load_schema()
    if def_name not in root["$defs"]:
        raise KeyError(f"schema không có $defs/{def_name}")
    schema = {
        "$schema": root["$schema"],
        "$defs": root["$defs"],
        "$ref": f"#/$defs/{def_name}",
    }
    return Draft202012Validator(schema)


def schema_errors(def_name: str, instance: Any) -> list[str]:
    """Danh sách lỗi (rỗng nếu hợp lệ) khi kiểm `instance` theo $defs/def_name."""
    errors = sorted(validator_for(def_name).iter_errors(instance), key=lambda e: list(e.path))
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]


def example_files() -> list[tuple[Path, str]]:
    """Các file mẫu kèm $defs tương ứng, sắp theo tên file."""
    result: list[tuple[Path, str]] = []
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        prefix = path.name[:2]
        if prefix in EXAMPLE_DEFS:
            result.append((path, EXAMPLE_DEFS[prefix]))
    return result


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    files = example_files()
    failed = 0
    for path, def_name in files:
        instance = json.loads(path.read_text(encoding="utf-8"))
        errors = schema_errors(def_name, instance)
        status = "OK " if not errors else "SAI"
        print(f"[{status}] {path.name} -> {def_name}")
        for err in errors:
            print(f"      {err}")
        failed += bool(errors)

    missing = set(EXAMPLE_DEFS) - {p.name[:2] for p, _ in files}
    for prefix in sorted(missing):
        print(f"[THIẾU] không thấy file mẫu {prefix}_*.json")
    print(f"\n{len(files) - failed}/{len(EXAMPLE_DEFS)} file mẫu hợp lệ")
    return 0 if failed == 0 and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
