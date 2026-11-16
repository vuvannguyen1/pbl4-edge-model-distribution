"""Hàm đọc dữ liệu và tóm tắt cho dashboard (`/admin-api/*`), không có route.

Đọc trạng thái sẵn có của `DistributionService`, các file JSONL feedback và file Registry.
Không chọn model, không lập plan, không ghi gì.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.config import MODEL_CARDS_DIR, Settings
from server.errors import bad_request
from server.state import DownloadRequest

if TYPE_CHECKING:
    from server.service import DistributionService

METRICS_PATTERN = "feedback-*.jsonl"
MANUAL_PATTERN = "manual-feedback-*.jsonl"
FEEDBACK_KINDS = ("metrics", "manual", "text")
MAX_LIMIT = 1000
# request chưa xong mà quá lâu không báo tiến độ thì cảnh báo trên dashboard
STALLED_AFTER_S = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_s(timestamp: str | None, now: datetime) -> float | None:
    if not timestamp:
        return None
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (now - then).total_seconds())


def _limit(value: int) -> int:
    if not 1 <= value <= MAX_LIMIT:
        raise bad_request(f"limit phải từ 1 đến {MAX_LIMIT}, nhận {value}.")
    return value


def _median(values: Iterable[float | int | None]) -> float | None:
    numbers = [float(v) for v in values if isinstance(v, (int, float))]
    return round(statistics.median(numbers), 2) if numbers else None


def read_jsonl_newest(paths: Iterable[Path], limit: int) -> list[dict[str, Any]]:
    """Bản ghi mới nhất trước: file mới trước, trong file đọc từ cuối. Bỏ dòng hỏng."""
    records: list[dict[str, Any]] = []
    for path in sorted(paths, key=lambda p: p.name, reverse=True):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
                if len(records) >= limit:
                    return records
    return records


def _feedback_paths(settings: Settings, kind: str) -> list[Path]:
    if kind == "text":
        path = settings.user_feedback_log
        return [path] if path.is_file() else []
    pattern = METRICS_PATTERN if kind == "metrics" else MANUAL_PATTERN
    folder = settings.feedback_dir
    return list(folder.glob(pattern)) if folder.is_dir() else []


def _registry_entries(settings: Settings) -> list[dict[str, Any]]:
    """Mọi bản ghi trong file Registry, kể cả chưa `ready` (Registry service chỉ giữ ready)."""
    try:
        raw = json.loads(settings.registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in raw.get("variants", []) if isinstance(e.get("variant_id"), str)]


def _model_card(variant_id: str) -> dict[str, Any] | None:
    try:
        return json.loads((MODEL_CARDS_DIR / f"{variant_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _peer_origin(service: DistributionService, device: Any) -> str | None:
    """Nguồn peer server có thể giao cho máy khác: HTTP (client Python) hoặc WebRTC (trình duyệt
    đang mở trang)."""
    if device.peer_origin:
        return device.peer_origin
    if device.webrtc_origin and service.signals.is_online(device.device_id):
        return device.webrtc_origin
    return None


def _sharing_ids(service: DistributionService) -> set[str]:
    """Thiết bị server đang dùng làm peer: chia sẻ được (HTTP/WebRTC), đủ model, không bị cấm."""
    complete = {dev for dev, _ in service.state.complete_pairs()}
    banned = set(service.state.banned_origins())
    result = set()
    for d in service.devices.all():
        origin = _peer_origin(service, d)
        if origin and origin not in banned and d.device_id in complete:
            result.add(d.device_id)
    return result


def _plan_counts(record: DownloadRequest) -> dict[str, int]:
    counts = {"server": 0, "peer": 0, "server_received": 0, "peer_received": 0, "webrtc": 0}
    for item in record.plan:
        counts[item.source] += 1
        if item.index in record.received:
            counts[f"{item.source}_received"] += 1
        if str(item.url).startswith("webrtc://"):
            counts["webrtc"] += 1
    return counts


def _request_status(record: DownloadRequest, now: datetime) -> str:
    if record.complete:
        return "complete"
    if record.source == "web":
        return "planned"  # dashboard chỉ xin plan, không có thiết bị nào tải
    age = _age_s(record.updated_at, now)
    return "stalled" if age is not None and age > STALLED_AFTER_S else "downloading"


def _request_summary(service: DistributionService, record: DownloadRequest, now: datetime) -> dict:
    device = service.devices.get(record.device_id)
    return {
        "request_id": record.request_id,
        "device_id": record.device_id,
        "device_name": device.name if device else record.device_id,
        "device_class": device.device_class if device else None,
        "variant_id": record.variant_id,
        "selector_version": record.selector_version,
        "reason": record.reason,
        "source": record.source,
        "status": _request_status(record, now),
        "requested_bandwidth_kbps": record.requested_bandwidth_kbps,
        "bandwidth_kbps": record.bandwidth_kbps,
        "server_bandwidth_kbps": record.server_bandwidth_kbps,
        "deadline_s": record.deadline_s,
        "estimated_download_s": record.estimated_download_s,
        "chunk_count": record.chunk_count,
        "received_count": len(record.received),
        "bad_count": len(record.bad_chunks),
        "plan_counts": _plan_counts(record),
        "decision_log": record.decision_log,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "completed_at": record.completed_at,
        "mode": record.mode,
        "budget_ms": record.budget_ms,
        "runtime_family": record.runtime_family,
        "metrics": record.metrics,
    }


def _alerts(
    service: DistributionService,
    settings: Settings,
    records: list[DownloadRequest],
    entries: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    for origin in service.state.banned_origins():
        alerts.append(
            {
                "level": "danger",
                "message": f"Peer {origin} từng gửi miếng sai hash; server ngừng giao peer này.",
            }
        )
    for record in records:
        if record.bad_chunks:
            alerts.append(
                {
                    "level": "warning",
                    "message": f"{record.request_id} ({record.device_id}) báo "
                    f"{len(record.bad_chunks)} miếng lỗi chưa tải lại được.",
                }
            )
        if _request_status(record, now) == "stalled":
            alerts.append(
                {
                    "level": "warning",
                    "message": f"{record.request_id} ({record.device_id}) chưa xong và không báo "
                    f"tiến độ hơn {STALLED_AFTER_S} s.",
                }
            )
    for entry in entries:
        problems = list(entry.get("validation_errors") or [])
        if entry.get("status") != "ready" or problems:
            alerts.append(
                {
                    "level": "warning",
                    "message": f"Registry: {entry['variant_id']} ở trạng thái "
                    f"{entry.get('status')}" + (f" ({'; '.join(problems)})" if problems else ""),
                }
            )
    reviewed: set[str] = set()
    for record in read_jsonl_newest(_feedback_paths(settings, "manual"), MAX_LIMIT):
        variant_id = record.get("variant_id")
        if variant_id in reviewed:
            continue
        reviewed.add(variant_id)
        evaluation = record.get("evaluation") or {}
        if evaluation.get("needs_review"):
            alerts.append({"level": "warning", "message": str(evaluation.get("reason"))})
    return alerts


def _variant_row(service: DistributionService, entry: dict[str, Any], metrics: list[dict]) -> dict:
    variant_id = entry["variant_id"]
    own = [m for m in metrics if m.get("variant_id") == variant_id]
    return {
        "variant_id": variant_id,
        "model_id": entry.get("model_id"),
        "family": entry.get("family"),
        "version": entry.get("version"),
        "task_id": entry.get("task_id"),
        "precision": entry.get("precision"),
        "level": entry.get("level"),
        "file": entry.get("file"),
        "size_bytes": entry.get("size_bytes"),
        "chunk_size": entry.get("chunk_size"),
        "chunk_count": entry.get("chunk_count"),
        "sha256": entry.get("sha256"),
        "top1_accuracy": entry.get("top1_accuracy"),
        "map50_95": entry.get("map50_95"),
        "gflops": entry.get("gflops"),
        "minimum_ram_mb": entry.get("minimum_ram_mb"),
        "detection_format": (entry.get("detection") or {}).get("format"),
        "detection_input": (entry.get("detection") or {}).get("input_size"),
        "input_shape": entry.get("input_shape"),
        "supported_runtimes": entry.get("supported_runtimes"),
        "status": entry.get("status"),
        "distributable": service.registry.get_ready(variant_id) is not None,
        "warnings": entry.get("warnings") or [],
        "validation_errors": entry.get("validation_errors") or [],
        "devices_complete": sum(1 for _, v in service.state.complete_pairs() if v == variant_id),
        "metrics_count": len(own),
        "latency_ms_median": _median(m.get("latency_ms_median") for m in own),
        "load_ms_median": _median(m.get("load_ms") for m in own),
        "download_ms_median": _median(m.get("download_ms") for m in own),
        "peak_ram_mb_median": _median(m.get("peak_ram_mb") for m in own),
    }


def _count_jsonl(paths: Iterable[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += sum(
                1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        except OSError:
            continue
    return total
