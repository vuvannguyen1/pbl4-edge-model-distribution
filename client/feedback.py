"""Đánh giá của người dùng: lần detect gần nhất, hàng chờ JSONL và gửi bù.

- `last_detect.json` (trong cache của thiết bị): model, ảnh, dự đoán của lần detect
  gần nhất, để `client.feedback_cli` điền sẵn thông tin.
- `feedback_queue.jsonl`: mỗi dòng một đánh giá, `status` là `pending` (chưa gửi được),
  `sent` (server đã xác nhận) hoặc `rejected` (server từ chối nội dung, giữ lại để xem).

Đánh giá luôn được ghi `pending` trước khi gửi; chỉ đổi sang `sent` khi server trả
`ok` đúng `feedback_id`. Mỗi đánh giá có `feedback_id` riêng, server bỏ qua bản trùng
nên gửi lại sau lỗi mạng không bị ghi hai lần. Ghi file có khóa, để agent `--serve` và
CLI cùng chạy không làm mất dòng của nhau.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from client.api import ApiClient, ApiError

QUEUE_FILE = "feedback_queue.jsonl"
LAST_DETECT_FILE = "last_detect.json"
STATUS_PENDING = "pending"
STATUS_SENT = "sent"
STATUS_REJECTED = "rejected"
LOCK_TIMEOUT_S = 10.0

# các trường gửi lên server; phần còn lại của bản ghi chỉ lưu trên máy
PAYLOAD_FIELDS = (
    "feedback_id",
    "device_id",
    "variant_id",
    "rating",
    "correct",
    "prediction",
    "confidence",
    "expected_label",
    "comment",
    "request_id",
    "detected_at",
    "created_at",
)

Printer = Callable[[str], None]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_feedback_id() -> str:
    return f"fb-{uuid.uuid4().hex}"


# ---------------------------------------------------------------- lần detect gần nhất


def save_last_detection(
    cache_dir: Path,
    *,
    variant_id: str,
    image: Path,
    prediction: str | None,
    confidence: float | None,
    request_id: str | None = None,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "variant_id": variant_id,
        "image": str(image),
        "prediction": prediction,
        "confidence": confidence,
        "request_id": request_id or None,
        "detected_at": utc_now(),
    }
    path = cache_dir / LAST_DETECT_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def load_last_detection(cache_dir: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((cache_dir / LAST_DETECT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("variant_id"), str) else None


# ---------------------------------------------------------------- khóa file


@contextmanager
def file_lock(path: Path, timeout_s: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Khóa độc quyền giữa các tiến trình bằng file `.lock` (Windows và Linux)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                _lock(handle)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Không khóa được {path} sau {timeout_s:g} s") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)


if os.name == "nt":
    import msvcrt

    def _lock(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: Any) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: Any) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------- hàng chờ


@dataclass
class SyncReport:
    sent: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    remaining: int = 0
    error: str | None = None  # lỗi mạng/server làm dừng giữa chừng


class FeedbackQueue:
    def __init__(self, cache_dir: Path) -> None:
        self.path = cache_dir / QUEUE_FILE
        self.lock_path = cache_dir / (QUEUE_FILE + ".lock")

    def _read_lines(self) -> list[str]:
        try:
            return [ln for ln in self.path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except FileNotFoundError:
            return []

    @staticmethod
    def _parse(line: str) -> dict[str, Any] | None:
        try:
            record = json.loads(line)
        except ValueError:
            return None
        return record if isinstance(record, dict) and "feedback_id" in record else None

    def records(self) -> list[dict[str, Any]]:
        with file_lock(self.lock_path):
            lines = self._read_lines()
        return [r for r in map(self._parse, lines) if r is not None]

    def pending(self) -> list[dict[str, Any]]:
        """Bản ghi `pending`, cũ trước (theo created_at, rồi thứ tự trong file)."""
        items = [r for r in self.records() if r.get("status") == STATUS_PENDING]
        return sorted(items, key=lambda r: str(r.get("created_at", "")))

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ghi một đánh giá mới với trạng thái `pending` (ghi thêm, không viết lại file)."""
        record = {**payload, "status": STATUS_PENDING}
        line = json.dumps(record, ensure_ascii=False)
        with file_lock(self.lock_path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        return record

    def update(self, feedback_id: str, **fields: Any) -> None:
        """Sửa bản ghi theo feedback_id; dòng không đọc được giữ nguyên như cũ."""
        with file_lock(self.lock_path):
            out: list[str] = []
            for line in self._read_lines():
                record = self._parse(line)
                if record is not None and record["feedback_id"] == feedback_id:
                    line = json.dumps({**record, **fields}, ensure_ascii=False)
                out.append(line)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("".join(ln + "\n" for ln in out), encoding="utf-8")
            os.replace(tmp, self.path)

    def sync(
        self, api: ApiClient, out: Printer = print, only: set[str] | None = None
    ) -> SyncReport:
        """Gửi lần lượt các bản ghi `pending` (chỉ các feedback_id trong `only` nếu có).

        Lỗi mạng/5xx: dừng, giữ `pending` để thử lại.
        """
        report = SyncReport()
        items = [r for r in self.pending() if only is None or r["feedback_id"] in only]
        for number, record in enumerate(items, start=1):
            payload = {key: record.get(key) for key in PAYLOAD_FIELDS}
            try:
                response = api.submit_feedback(payload)
            except ApiError as exc:
                if exc.status is not None and 400 <= exc.status < 500:
                    self.update(
                        record["feedback_id"],
                        status=STATUS_REJECTED,
                        error=f"{exc.code}: {exc.message}",
                        rejected_at=utc_now(),
                    )
                    report.rejected.append({**record, "error": exc.message})
                    out(f"  [cảnh báo] Server từ chối {record['feedback_id']}: {exc.message}")
                    continue
                report.error = exc.message
                report.remaining = len(items) - number + 1
                return report
            if (
                response.get("ok") is not True
                or response.get("feedback_id") != payload["feedback_id"]
            ):
                report.error = "Server trả phản hồi không xác nhận feedback_id"
                report.remaining = len(items) - number + 1
                return report
            self.update(
                record["feedback_id"],
                status=STATUS_SENT,
                sent_at=utc_now(),
                server_status=response.get("status"),
                evaluation=response.get("evaluation"),
            )
            report.sent.append({**record, "response": response})
        return report
