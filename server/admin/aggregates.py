"""Gom feedback theo variant (số đo, đánh giá thủ công) và theo nhãn (nhận xét chữ)."""

from __future__ import annotations

from typing import Any

from server.admin.common import _median


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
                "fps_median": _median(i.get("fps") for i in items),
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
