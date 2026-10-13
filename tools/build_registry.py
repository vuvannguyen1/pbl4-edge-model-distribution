"""Sinh registry/model_registry.json từ models/ và metadata/model_cards/.

Chạy từ thư mục gốc repo:
    python tools/build_registry.py

Script không chứa danh sách model nào. Muốn thêm model mới:
đặt file .onnx vào models/<nhóm tác vụ>/ và thêm một Model Card vào
metadata/model_cards/<variant_id>.json, rồi chạy lại script.

Với mỗi file .onnx tìm được, script:
  1. ghép với Model Card có artifact.file trùng đường dẫn tương đối;
  2. tự tính size_bytes, SHA-256 toàn file và SHA-256 từng chunk 1 MiB;
  3. mở bằng ONNX Runtime, so input với contract trong Model Card;
  4. đặt status = "ready" nếu hợp lệ, ngược lại "validation_failed" kèm lý do.
Registry được ghi nguyên tử (ghi file tạm rồi đổi tên).
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHUNK_SIZE = 1024 * 1024  # 1 MiB, theo Protocol 1.0
REGISTRY_SCHEMA_VERSION = "1.0"

# Trạng thái do người quản lý đặt tay trong Model Card; script không ghi đè
MANUAL_STATUSES = {"disabled", "retired"}

REQUIRED_CARD_FIELDS = [
    ("model_id",),
    ("variant_id",),
    ("version",),
    ("artifact", "file"),
    ("task", "id"),
    ("model", "family"),
    ("model", "precision"),
    ("model", "compression_level"),
    ("contract", "input_shape"),
    ("contract", "input_dtype"),
    ("contract", "preprocessing"),
]

ORT_DTYPES = {"tensor(float)": "float"}


def get_field(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def hash_file(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """Trả về SHA-256 toàn file và danh sách chunk (index, offset, size, sha256)."""
    whole = hashlib.sha256()
    chunks: list[dict[str, Any]] = []
    with path.open("rb") as f:
        index = 0
        while block := f.read(CHUNK_SIZE):
            whole.update(block)
            chunks.append(
                {
                    "index": index,
                    "offset": index * CHUNK_SIZE,
                    "size_bytes": len(block),
                    "sha256": hashlib.sha256(block).hexdigest(),
                }
            )
            index += 1
    return whole.hexdigest(), chunks


def load_cards(cards_dir: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Đọc Model Card, trả về map artifact.file -> card và danh sách lỗi."""
    cards: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    seen_variants: dict[str, Path] = {}
    for card_path in sorted(cards_dir.glob("*.json")):
        try:
            card = json.loads(card_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{card_path.name}: JSON không hợp lệ ({exc})")
            continue

        missing = [".".join(p) for p in REQUIRED_CARD_FIELDS if get_field(card, p) is None]
        if missing:
            errors.append(f"{card_path.name}: thiếu trường {', '.join(missing)}")
            continue

        variant_id = card["variant_id"]
        if variant_id in seen_variants:
            errors.append(
                f"{card_path.name}: trùng variant_id '{variant_id}' với "
                f"{seen_variants[variant_id].name}"
            )
            continue
        seen_variants[variant_id] = card_path

        file_key = Path(card["artifact"]["file"]).as_posix()
        if file_key in cards:
            errors.append(f"{card_path.name}: nhiều Model Card cùng trỏ tới {file_key}")
            continue
        cards[file_key] = card
    return cards, errors


def validate_onnx(path: Path, card: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Mở model bằng ONNX Runtime và so với contract. Trả về (lỗi, thông tin input)."""
    try:
        import onnxruntime as ort
    except ImportError:
        return ["chưa cài onnxruntime nên không kiểm định được"], {}

    try:
        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    except Exception as exc:  # file hỏng, opset không hỗ trợ, ...
        return [f"ONNX Runtime không mở được file: {exc}"], {}

    inputs = session.get_inputs()
    if len(inputs) != 1:
        return [f"model có {len(inputs)} input, contract hiện tại chỉ hỗ trợ 1"], {}

    model_input = inputs[0]
    errors: list[str] = []
    expected_shape = card["contract"]["input_shape"]
    actual_shape = list(model_input.shape)
    if len(actual_shape) != len(expected_shape):
        errors.append(f"input có {len(actual_shape)} chiều, contract cần {len(expected_shape)}")
    else:
        for axis, (actual, expected) in enumerate(zip(actual_shape, expected_shape)):
            # Chiều động (vd. 'N', 'batch_size') chấp nhận mọi giá trị
            if isinstance(actual, int) and actual > 0 and actual != expected:
                errors.append(f"input chiều {axis} = {actual}, contract cần {expected}")

    actual_dtype = ORT_DTYPES.get(model_input.type, model_input.type)
    if actual_dtype != card["contract"]["input_dtype"]:
        errors.append(
            f"input dtype {model_input.type}, contract cần {card['contract']['input_dtype']}"
        )

    info = {
        "input_name": model_input.name,
        "output_names": [o.name for o in session.get_outputs()],
        "onnxruntime_version": ort.__version__,
    }
    return errors, info


def build_entry(
    onnx_path: Path, rel_path: str, card: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    sha256, chunks = hash_file(onnx_path)
    validation_errors, runtime_info = validate_onnx(onnx_path, card)

    card_status = get_field(card, ("lifecycle", "status"))
    if card_status in MANUAL_STATUSES:
        status = card_status
    elif validation_errors:
        status = "validation_failed"
    else:
        status = "ready"

    warnings: list[str] = []
    if get_field(card, ("quality", "top1_accuracy")) is None:
        warnings.append("chưa có top1_accuracy đã xác thực")

    contract = card["contract"]
    entry = {
        "variant_id": card["variant_id"],
        "model_id": card["model_id"],
        "version": card["version"],
        "task_id": card["task"]["id"],
        "file": onnx_path.name,
        "storage_path": rel_path,  # nội bộ, không trả cho client
        "base_model": card["model_id"],
        "family": card["model"]["family"],
        "level": card["model"]["compression_level"],
        "precision": card["model"]["precision"],
        "size_bytes": onnx_path.stat().st_size,
        "sha256": sha256,
        "chunk_size": CHUNK_SIZE,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "top1_accuracy": get_field(card, ("quality", "top1_accuracy")),
        "input_shape": contract["input_shape"],
        "input_dtype": contract["input_dtype"],
        "normalize": contract["preprocessing"].get("normalize"),
        "preprocessing": contract["preprocessing"],
        "output_contract": contract.get("output_contract"),
        "supported_runtimes": get_field(card, ("requirements", "supported_runtimes")),
        "minimum_ram_mb": get_field(card, ("requirements", "minimum_ram_mb")),
        "runtime_info": runtime_info,
        "status": status,
        "validation_errors": validation_errors,
        "warnings": warnings,
    }
    return entry, warnings


def write_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    store_dir = (repo_root / "models").resolve()
    cards_dir = repo_root / "metadata" / "model_cards"
    registry_path = repo_root / "registry" / "model_registry.json"

    cards, card_errors = load_cards(cards_dir)
    for err in card_errors:
        print(f"[card lỗi] {err}")

    variants: list[dict[str, Any]] = []
    used_cards: set[str] = set()
    for onnx_path in sorted(store_dir.rglob("*.onnx")):
        resolved = onnx_path.resolve()
        if not resolved.is_relative_to(store_dir):  # chặn symlink trỏ ra ngoài store
            print(f"[bỏ qua] {onnx_path}: nằm ngoài Model Store")
            continue

        rel_path = resolved.relative_to(store_dir).as_posix()
        card = cards.get(rel_path)
        if card is None:
            print(f"[bỏ qua] {rel_path}: chưa có Model Card, không đưa vào phân phối")
            continue
        used_cards.add(rel_path)

        print(f"[xử lý] {rel_path} -> {card['variant_id']}")
        entry, warnings = build_entry(resolved, rel_path, card)
        print(
            f"  status={entry['status']}  {entry['size_bytes']:,} bytes  "
            f"{entry['chunk_count']} chunk  sha256={entry['sha256'][:12]}…"
        )
        for err in entry["validation_errors"]:
            print(f"  [LỖI] {err}")
        for warn in warnings:
            print(f"  [cảnh báo] {warn}")
        variants.append(entry)

    for missing in sorted(set(cards) - used_cards):
        print(f"[thiếu file] Model Card trỏ tới {missing} nhưng không thấy file trong models/")

    registry = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chunk_size": CHUNK_SIZE,
        "variants": variants,
    }
    write_atomic(registry_path, registry)

    ready = sum(1 for v in variants if v["status"] == "ready")
    print(f"\nĐã ghi {registry_path.relative_to(repo_root).as_posix()}: "
          f"{len(variants)} variant, {ready} ready")
    return 0 if ready == len(variants) and not card_errors else 1


if __name__ == "__main__":
    sys.exit(main())
