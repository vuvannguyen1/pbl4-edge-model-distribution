"""Kho model trên client: file, metadata, SHA-256 và trạng thái của từng model.

Mỗi variant một thư mục `<cache_dir>/<variant_id>/` gồm file model và `variant.json`:

    {"status": "complete", "task": "image_classification", "version": "sha256:c0c3f76d93fa",
     "runtime": "onnxruntime 1.20.1", "cpu_arch": "AMD64", "reason": null,
     "updated_at": "2026-10-14T00:00:00+00:00", "variant": {<khối variant của /api/request>}}

Khối `variant` chứa variant_id, tên file, kích thước, SHA-256, input shape, dtype và
normalize. Protocol 1.0 không gửi số phiên bản, nên `version` lấy theo SHA-256 toàn file.

Trạng thái:
- `partial`: đang tải (file `.part`), chưa dùng được;
- `complete`: đủ file, kích thước và SHA-256 khớp, ONNX Runtime mở được;
- `invalid`: một điều kiện trên không đạt; không dùng để detect, lần sau có server thì tải lại.

Mỗi lần tìm model, client kiểm lại kích thước và SHA-256; sai thì đánh `invalid`. Nếu
runtime hoặc kiến trúc CPU khác lúc tải, client mở thử bằng ONNX Runtime trước khi dùng.
`variant.json` dạng cũ (chỉ có khối variant, ghi sau khi hash khớp) vẫn đọc được, coi
như `complete`. `<cache_dir>/active.json` ghi variant đang dùng.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from client.api import TASK

BYTES_PER_MB = 1024 * 1024
META_FILE = "variant.json"  # metadata cạnh file model
ACTIVE_FILE = "active.json"  # variant đang dùng, ở gốc cache_dir

STATUS_PARTIAL = "partial"
STATUS_COMPLETE = "complete"
STATUS_INVALID = "invalid"

Opener = Callable[[Path], None]  # ném lỗi nếu runtime không mở được model
Found = dict[str, tuple[dict[str, Any], Path]]


class ModelInvalid(Exception):
    """Model trên đĩa không dùng được (thiếu file, sai kích thước/hash, runtime không mở được)."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(BYTES_PER_MB):
            digest.update(block)
    return digest.hexdigest()


def variant_paths(cache_dir: Path, variant: dict[str, Any]) -> tuple[Path, Path]:
    """(file hoàn chỉnh, file .part) trong cache_dir/<variant_id>/."""
    folder = cache_dir / variant["id"]
    final = folder / variant["file"]
    return final, final.with_name(final.name + ".part")


def model_version(variant: dict[str, Any]) -> str:
    return str(variant.get("version") or f"sha256:{variant['sha256'][:12]}")


def onnx_opener(path: Path) -> None:
    import onnxruntime as ort

    ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Ghi qua file tạm rồi đổi tên, để tắt máy giữa chừng không để lại JSON hỏng."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_meta(meta_path: Path) -> dict[str, Any] | None:
    """Đọc variant.json (dạng mới hoặc dạng cũ). None nếu file hỏng hoặc thiếu trường."""
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "variant" not in data and "id" in data:
            data = {"status": STATUS_COMPLETE, "task": TASK, "runtime": None, "variant": data}
        variant = data["variant"]
        if not all(key in variant for key in ("id", "file", "size_bytes", "sha256")):
            return None
        data.setdefault("version", model_version(variant))
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_active(cache_dir: Path, variant_id: str) -> None:
    """Ghi variant đang dùng, để lần chạy sau chọn đúng model này."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / ACTIVE_FILE).write_text(json.dumps({"variant_id": variant_id}), encoding="utf-8")


def load_active(cache_dir: Path) -> str | None:
    """variant_id trong active.json, None nếu chưa có hoặc file hỏng."""
    try:
        data = json.loads((cache_dir / ACTIVE_FILE).read_text(encoding="utf-8"))
        variant_id = data["variant_id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return variant_id if isinstance(variant_id, str) else None


def choose_local(
    found: Found,
    requested: str | None = None,
    active: str | None = None,
) -> tuple[dict[str, Any], Path, str]:
    """Chọn model trong kho cục bộ (kết quả của `ModelCache.valid_models`).

    Thứ tự: `requested` (bắt buộc phải có) -> `active` -> top-1 cao nhất, bằng nhau
    thì file nhỏ hơn. Trả (khối variant, file, lý do chọn). Không có model nào phù
    hợp thì ném LookupError.
    """
    if requested is not None:
        if requested not in found:
            have = ", ".join(sorted(found)) or "không có"
            raise LookupError(f"Cache không có {requested} hợp lệ (đang có: {have}).")
        variant, path = found[requested]
        return variant, path, "theo yêu cầu"
    if not found:
        raise LookupError("Cache chưa có model nào hợp lệ.")
    if active is not None and active in found:
        variant, path = found[active]
        return variant, path, "model đang dùng, ghi trong active.json"
    variant, path = min(
        found.values(), key=lambda vp: (-vp[0]["top1_accuracy"], vp[0]["size_bytes"], vp[0]["id"])
    )
    return variant, path, "top-1 cao nhất trong cache"


@dataclass(frozen=True)
class CacheEntry:
    variant_id: str
    status: str
    reason: str | None


class ModelCache:
    """Quản lý kho model của một thiết bị.

    `runtime`/`cpu_arch` là môi trường hiện tại; để None thì bỏ qua bước so môi trường
    (ví dụ peer service chỉ cần file đúng hash để chia sẻ).
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        task: str = TASK,
        runtime: str | None = None,
        cpu_arch: str | None = None,
        opener: Opener = onnx_opener,
    ) -> None:
        self.cache_dir = cache_dir
        self.task = task
        self.runtime = runtime
        self.cpu_arch = cpu_arch
        self.opener = opener

    # ------------------------------------------------------------ metadata

    def paths(self, variant: dict[str, Any]) -> tuple[Path, Path]:
        return variant_paths(self.cache_dir, variant)

    def meta_path(self, variant_id: str) -> Path:
        return self.cache_dir / variant_id / META_FILE

    def _write(
        self,
        variant: dict[str, Any],
        status: str,
        reason: str | None = None,
        *,
        runtime: str | None = None,
        cpu_arch: str | None = None,
    ) -> None:
        _write_json(
            self.meta_path(variant["id"]),
            {
                "status": status,
                "task": self.task,
                "version": model_version(variant),
                "runtime": runtime,
                "cpu_arch": cpu_arch,
                "reason": reason,
                "updated_at": _now(),
                "variant": variant,
            },
        )

    def save(self, variant: dict[str, Any], status: str = STATUS_COMPLETE) -> None:
        """Ghi metadata với môi trường hiện tại. Chỉ ghi `complete` sau khi đã kiểm hash."""
        self._write(variant, status, runtime=self.runtime, cpu_arch=self.cpu_arch)

    def mark_partial(self, variant: dict[str, Any]) -> None:
        self._write(variant, STATUS_PARTIAL, runtime=self.runtime, cpu_arch=self.cpu_arch)

    def mark_invalid(self, variant: dict[str, Any], reason: str) -> None:
        self._write(variant, STATUS_INVALID, reason, runtime=self.runtime, cpu_arch=self.cpu_arch)

    def entries(self) -> list[CacheEntry]:
        """Mọi model có metadata trong kho (kể cả partial/invalid), theo variant_id."""
        result: list[CacheEntry] = []
        if not self.cache_dir.is_dir():
            return result
        for meta_path in sorted(self.cache_dir.glob(f"*/{META_FILE}")):
            meta = read_meta(meta_path)
            if meta is not None:
                result.append(
                    CacheEntry(meta["variant"]["id"], str(meta.get("status")), meta.get("reason"))
                )
        return result

    # ------------------------------------------------------------ kiểm tra

    def _file_problem(self, variant: dict[str, Any]) -> str | None:
        final, _ = self.paths(variant)
        if not final.is_file():
            return f"thiếu file {final.name}"
        size = final.stat().st_size
        if size != variant["size_bytes"]:
            return f"kích thước {size} byte khác metadata {variant['size_bytes']} byte"
        digest = sha256_file(final)
        if digest != variant["sha256"]:
            return f"SHA-256 {digest[:12]}… khác {variant['sha256'][:12]}…"
        return None

    def _open_problem(self, variant: dict[str, Any]) -> str | None:
        final, _ = self.paths(variant)
        try:
            self.opener(final)
        except Exception as exc:  # lỗi của runtime có nhiều loại
            return f"ONNX Runtime không mở được: {type(exc).__name__}: {str(exc)[:160]}"
        return None

    def complete(self, variant: dict[str, Any]) -> Path:
        """Sau khi tải: kiểm file, kích thước, SHA-256 và mở bằng runtime rồi ghi `complete`.

        Không đạt thì ghi `invalid` và ném ModelInvalid.
        """
        problem = self._file_problem(variant) or self._open_problem(variant)
        if problem is not None:
            self.mark_invalid(variant, problem)
            raise ModelInvalid(f"{variant['id']}: {problem}")
        self.save(variant, STATUS_COMPLETE)
        return self.paths(variant)[0]

    def _check(self, meta: dict[str, Any], folder: Path) -> tuple[dict[str, Any], Path] | None:
        variant = meta["variant"]
        if folder.name != variant["id"] or meta.get("status") != STATUS_COMPLETE:
            return None
        if meta.get("task", TASK) != self.task:
            return None
        problem = self._file_problem(variant)
        if problem is None and self.runtime is not None:
            changed = meta.get("runtime") != self.runtime or (
                self.cpu_arch is not None and meta.get("cpu_arch") != self.cpu_arch
            )
            if changed:  # runtime/CPU khác lúc tải: mở thử lại trước khi tin
                problem = self._open_problem(variant)
                if problem is None:
                    self.save(variant, STATUS_COMPLETE)
        if problem is not None:
            self.mark_invalid(variant, problem)
            return None
        return variant, self.paths(variant)[0]

    def get(self, variant_id: str) -> tuple[dict[str, Any], Path] | None:
        """Model `variant_id` nếu đang `complete` và vẫn đúng hash, None nếu không."""
        meta = read_meta(self.meta_path(variant_id))
        if meta is None:
            return None
        try:
            return self._check(meta, self.meta_path(variant_id).parent)
        except (OSError, KeyError, TypeError):
            return None

    def valid_models(self) -> Found:
        """Model sẵn sàng: `complete`, đúng task, đủ file, kích thước và SHA-256 khớp."""
        found: Found = {}
        if not self.cache_dir.is_dir():
            return found
        for meta_path in sorted(self.cache_dir.glob(f"*/{META_FILE}")):
            meta = read_meta(meta_path)
            if meta is None:
                continue
            try:
                checked = self._check(meta, meta_path.parent)
            except (OSError, KeyError, TypeError):
                continue
            if checked is not None:
                found[checked[0]["id"]] = checked
        return found

    def find_local_model(self, requested: str | None = None) -> tuple[dict[str, Any], Path, str]:
        """Chọn model hợp lệ trong kho (xem `choose_local`). Không có thì ném LookupError."""
        found = self.valid_models()
        try:
            return choose_local(found, requested, load_active(self.cache_dir))
        except LookupError as exc:
            rejected = [e for e in self.entries() if e.status != STATUS_COMPLETE]
            if rejected:
                details = "; ".join(
                    f"{e.variant_id} {e.status}" + (f" ({e.reason})" if e.reason else "")
                    for e in rejected
                )
                raise LookupError(f"{exc} Không dùng được: {details}.") from exc
            raise


# ---------------------------------------------------------------- hàm gọn (giữ API cũ)


def save_variant_meta(cache_dir: Path, variant: dict[str, Any]) -> None:
    """Ghi metadata `complete` cạnh file model, chỉ gọi sau khi SHA-256 toàn file khớp."""
    ModelCache(cache_dir).save(variant, STATUS_COMPLETE)


def verified_variants(cache_dir: Path) -> Found:
    """Kho cục bộ: variant `complete`, đủ file và SHA-256 khớp -> (khối variant, file)."""
    return ModelCache(cache_dir).valid_models()
