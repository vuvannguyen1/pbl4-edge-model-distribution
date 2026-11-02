"""API chỉ đọc cho dashboard web (`/admin-api/*`).

Lớp mỏng: đọc trạng thái sẵn có của `DistributionService` (Registry, thiết bị, request
trong RAM), các file JSONL feedback và file log trong ngày, rồi trả JSON cho trang
`/ui`. Không chọn model, không lập plan, không ghi gì. Thao tác của dashboard (xin model
thử, gửi đánh giá) gọi thẳng các endpoint Protocol 1.0 trong `server/main.py`.
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Iterable, Iterator
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from server.config import PROTOCOL_VERSION, REPO_ROOT, Settings
from server.errors import ProtocolError, bad_request, unknown_variant
from server.service import DistributionService
from server.state import DownloadRequest, url_origin
from server.user_feedback_handler import FEEDBACK_LOG_PATH

MODEL_CARDS_DIR = REPO_ROOT / "metadata" / "model_cards"
METRICS_PATTERN = "feedback-*.jsonl"
MANUAL_PATTERN = "manual-feedback-*.jsonl"
FEEDBACK_KINDS = ("metrics", "manual", "text")
MAX_LIMIT = 1000
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# request chưa xong mà quá lâu không báo tiến độ thì cảnh báo trên dashboard
STALLED_AFTER_S = 60
_LOG_LINE = re.compile(r"^(\d{2}:\d{2}:\d{2}) (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.*)$")
_REQUEST_ID = re.compile(r"\[(r-\d+)\]")

router = APIRouter(prefix="/admin-api", tags=["dashboard"])


# ---------------------------------------------------------------- helper


def _service(request: Request) -> DistributionService:
    return request.app.state.service


def _settings(request: Request) -> Settings:
    return request.app.state.settings


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
        return [FEEDBACK_LOG_PATH] if FEEDBACK_LOG_PATH.is_file() else []
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


def _plan_counts(record: DownloadRequest) -> dict[str, int]:
    counts = {"server": 0, "peer": 0, "server_received": 0, "peer_received": 0}
    for item in record.plan:
        counts[item.source] += 1
        if item.index in record.received:
            counts[f"{item.source}_received"] += 1
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
    }


# ---------------------------------------------------------------- tổng quan


@router.get("/summary")
def summary(request: Request) -> dict[str, Any]:
    service, settings, now = _service(request), _settings(request), _now()
    entries = _registry_entries(settings)
    by_status: dict[str, int] = {}
    for entry in entries:
        status = str(entry.get("status"))
        by_status[status] = by_status.get(status, 0) + 1

    devices = service.devices.all()
    complete_pairs = service.state.complete_pairs()
    banned = set(service.state.banned_origins())
    sharing = {
        d.device_id
        for d in devices
        if d.peer_origin
        and d.peer_origin not in banned
        and any(dev == d.device_id for dev, _ in complete_pairs)
    }
    active = [
        d for d in devices if (_age_s(service.devices.last_seen(d.device_id), now) or 1e9) <= 30
    ]

    records = service.state.requests_snapshot()
    statuses: dict[str, int] = {}
    planned = {"server": 0, "peer": 0, "server_received": 0, "peer_received": 0}
    for record in records:
        status = _request_status(record, now)
        statuses[status] = statuses.get(status, 0) + 1
        if record.source != "web":
            for key, value in _plan_counts(record).items():
                planned[key] += value

    metrics = read_jsonl_newest(_feedback_paths(settings, "metrics"), MAX_LIMIT)
    actual = {
        "server": sum(int(m.get("chunks_from_server") or 0) for m in metrics),
        "peer": sum(int(m.get("chunks_from_peer") or 0) for m in metrics),
        "reports": len(metrics),
    }

    return {
        "now": now.isoformat(timespec="seconds"),
        "server": {
            "protocol_version": PROTOCOL_VERSION,
            "selector_version": service.selector.version,
            "rate_limit_kbps": settings.rate_limit_kbps or None,
            "peer_enabled": settings.peer_enabled,
            "latency_slo_ms": settings.latency_slo_ms,
        },
        "variants": {"total": len(entries), "by_status": by_status},
        "devices": {
            "registered": len(devices),
            "active": len(active),
            "sharing_peers": len(sharing),
        },
        "requests": {"total": len(records), "by_status": statuses},
        "chunks_planned": planned,
        "chunks_actual": actual,
        "alerts": _alerts(service, settings, records, entries, now),
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


# ---------------------------------------------------------------- thiết bị


@router.get("/devices")
def devices(request: Request) -> dict[str, Any]:
    service, now = _service(request), _now()
    complete_pairs = service.state.complete_pairs()
    banned = set(service.state.banned_origins())
    class_variants = service.state.class_variants()
    records = service.state.requests_snapshot()
    items = []
    for device in sorted(service.devices.all(), key=lambda d: d.device_id):
        own = [r for r in records if r.device_id == device.device_id]
        last_seen = service.devices.last_seen(device.device_id)
        complete = sorted(v for d, v in complete_pairs if d == device.device_id)
        items.append(
            {
                **asdict(device),
                "peer_origin": device.peer_origin,
                "last_seen": last_seen,
                "last_seen_age_s": _age_s(last_seen, now),
                "complete_variants": complete,
                "peer_banned": device.peer_origin in banned if device.peer_origin else False,
                "sharing": bool(device.peer_origin and complete)
                and device.peer_origin not in banned,
                "request_count": len(own),
                "last_request_id": own[-1].request_id if own else None,
                "class_variant": class_variants.get(device.device_class),
            }
        )
    return {"now": now.isoformat(timespec="seconds"), "devices": items}


# ---------------------------------------------------------------- Registry


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


@router.get("/variants")
def variants(request: Request) -> dict[str, Any]:
    service, settings = _service(request), _settings(request)
    metrics = read_jsonl_newest(_feedback_paths(settings, "metrics"), MAX_LIMIT)
    rows = [_variant_row(service, e, metrics) for e in _registry_entries(settings)]
    return {"variants": sorted(rows, key=lambda r: r["variant_id"])}


@router.get("/variants/{variant_id}")
def variant_detail(request: Request, variant_id: str) -> dict[str, Any]:
    service, settings = _service(request), _settings(request)
    entry = next((e for e in _registry_entries(settings) if e["variant_id"] == variant_id), None)
    if entry is None:
        raise unknown_variant(variant_id)
    metrics = read_jsonl_newest(_feedback_paths(settings, "metrics"), MAX_LIMIT)
    return {
        **_variant_row(service, entry, metrics),
        "normalize": entry.get("normalize"),
        "preprocessing": entry.get("preprocessing"),
        "output_contract": entry.get("output_contract"),
        "runtime_info": entry.get("runtime_info"),
        "chunks": entry.get("chunks") or [],
        "model_card": _model_card(variant_id),
    }


# ---------------------------------------------------------------- request / truyền tải


@router.get("/requests")
def requests_list(request: Request, limit: int = 100) -> dict[str, Any]:
    service, now = _service(request), _now()
    records = service.state.requests_snapshot()[-_limit(limit) :]
    return {
        "now": now.isoformat(timespec="seconds"),
        "class_variants": service.state.class_variants(),
        "requests": [_request_summary(service, r, now) for r in reversed(records)],
    }


@router.get("/requests/{request_id}")
def request_detail(request: Request, request_id: str) -> dict[str, Any]:
    service, now = _service(request), _now()
    record = next(
        (r for r in service.state.requests_snapshot() if r.request_id == request_id), None
    )
    if record is None:
        raise ProtocolError("BAD_REQUEST", f"request_id '{request_id}' không tồn tại.", 404)
    banned = set(service.state.banned_origins())
    plan = [
        {
            "index": item.index,
            "source": item.source,
            "origin": url_origin(str(item.url)),
            "size_bytes": item.size_bytes,
            "received": item.index in record.received,
            "bad": item.index in record.bad_chunks,
            "origin_banned": item.source == "peer" and url_origin(str(item.url)) in banned,
        }
        for item in record.plan
    ]
    return {
        "now": now.isoformat(timespec="seconds"),
        **_request_summary(service, record, now),
        "plan": plan,
    }


# ---------------------------------------------------------------- feedback


def _metrics_by_variant(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(str(record.get("variant_id")), []).append(record)
    rows = []
    for variant_id, items in sorted(groups.items()):
        rows.append(
            {
                "variant_id": variant_id,
                "count": len(items),
                "download_ms_median": _median(i.get("download_ms") for i in items),
                "load_ms_median": _median(i.get("load_ms") for i in items),
                "latency_ms_median": _median(i.get("latency_ms_median") for i in items),
                "peak_ram_mb_median": _median(i.get("peak_ram_mb") for i in items),
                "chunks_from_peer": sum(int(i.get("chunks_from_peer") or 0) for i in items),
                "chunks_from_server": sum(int(i.get("chunks_from_server") or 0) for i in items),
                "switch_count": sum(
                    1 for i in items if (i.get("decision") or {}).get("action") == "switch"
                ),
            }
        )
    return rows


def _manual_by_variant(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:  # mới trước: bản đầu tiên gặp là thống kê mới nhất
        variant_id = str(record.get("variant_id"))
        if variant_id not in latest:
            latest[variant_id] = {"variant_id": variant_id, **(record.get("evaluation") or {})}
    return [latest[k] for k in sorted(latest)]


def _text_by_label(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, str], int] = {}
    for record in records:
        key = (int(record.get("score") or 0), str(record.get("label")))
        groups[key] = groups.get(key, 0) + 1
    return [
        {"score": score, "label": label, "count": count}
        for (score, label), count in sorted(groups.items(), reverse=True)
    ]


@router.get("/feedback")
def feedback(request: Request, kind: str = "metrics", limit: int = 200) -> dict[str, Any]:
    if kind not in FEEDBACK_KINDS:
        raise bad_request(f"kind phải là một trong {', '.join(FEEDBACK_KINDS)}, nhận '{kind}'.")
    settings = _settings(request)
    paths = _feedback_paths(settings, kind)
    records = read_jsonl_newest(paths, _limit(limit))
    everything = read_jsonl_newest(paths, MAX_LIMIT)
    if kind == "metrics":
        groups = _metrics_by_variant(everything)
    elif kind == "manual":
        groups = _manual_by_variant(everything)
    else:
        groups = _text_by_label(everything)
    return {
        "kind": kind,
        "sources": sorted(p.name for p in paths),
        "total": len(everything),
        "records": records,
        "groups": groups,
    }


# ---------------------------------------------------------------- log


def parse_log_lines(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Gom dòng log `HH:MM:SS LEVEL nội dung`; dòng tiếp theo (traceback) gộp vào dòng trước."""
    current: dict[str, Any] | None = None
    for line in lines:
        match = _LOG_LINE.match(line)
        if match:
            if current is not None:
                yield current
            time_text, level, message = match.groups()
            rid = _REQUEST_ID.search(message)
            current = {
                "time": time_text,
                "level": level,
                "message": message,
                "request_id": rid.group(1) if rid else None,
            }
        elif current is not None and line.strip():
            current["message"] += "\n" + line
    if current is not None:
        yield current


@router.get("/logs")
def logs(
    request: Request, tail: int = 200, level: str | None = None, q: str | None = None
) -> dict[str, Any]:
    settings = _settings(request)
    tail = _limit(tail)
    if level is not None and level.upper() not in LOG_LEVELS:
        raise bad_request(f"level phải là một trong {', '.join(LOG_LEVELS)}.")
    path = settings.logs_dir / f"server-{datetime.now():%Y%m%d}.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"file": path.name, "exists": False, "entries": []}
    entries = list(parse_log_lines(lines))
    if level is not None:
        wanted = LOG_LEVELS.index(level.upper())
        entries = [e for e in entries if LOG_LEVELS.index(e["level"]) >= wanted]
    if q:
        needle = q.casefold()
        entries = [e for e in entries if needle in e["message"].casefold()]
    return {"file": path.name, "exists": True, "entries": entries[-tail:]}
