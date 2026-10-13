"""Download Planner: lập plan chunk, chọn nguồn server hoặc peer cho từng miếng.

Planner không biết thuật toán chọn model; nó chỉ nhận variant đã chọn,
base URL của server và danh sách peer đã được xác minh (cùng lớp, đã
`complete` variant, có peer_port, chưa từng bị báo gửi sai).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from server.registry import RegistryVariant
from server.schemas import PlanItem


@dataclass(frozen=True)
class PeerSource:
    device_id: str
    origin: str  # http://<ip>:<peer_port>


def server_chunk_url(base_url: str, variant_id: str, index: int) -> str:
    return f"{base_url}/api/chunks/{variant_id}/{index}"


def peer_chunk_url(origin: str, variant_id: str, index: int) -> str:
    return f"{origin}/chunks/{variant_id}/{index}"


def peer_chunk_count(chunk_count: int) -> int:
    """Số miếng giao cho peer: nửa đầu, làm tròn lên (4 miếng -> 2, như file mẫu 04)."""
    return (chunk_count + 1) // 2


def build_plan(
    variant: RegistryVariant, base_url: str, peers: Sequence[PeerSource] = ()
) -> list[PlanItem]:
    """Plan đủ mọi miếng, sắp theo index.

    Có peer: nửa đầu chia vòng tròn cho các peer (`fallback_url` = URL server),
    nửa sau lấy từ server. Không có peer: mọi miếng từ server.
    """
    peer_share = peer_chunk_count(variant.chunk_count) if peers else 0
    plan: list[PlanItem] = []
    for chunk in variant.chunks:
        server_url = server_chunk_url(base_url, variant.variant_id, chunk.index)
        if chunk.index < peer_share:
            peer = peers[chunk.index % len(peers)]
            source, url, fallback = (
                "peer",
                peer_chunk_url(peer.origin, variant.variant_id, chunk.index),
                server_url,
            )
        else:
            source, url, fallback = "server", server_url, None
        plan.append(
            PlanItem(
                index=chunk.index,
                offset=chunk.offset,
                size_bytes=chunk.size_bytes,
                sha256=chunk.sha256,
                source=source,
                url=url,
                fallback_url=fallback,
            )
        )
    return plan


def summarize_plan(plan: Sequence[PlanItem]) -> str:
    """Tóm tắt nguồn, ví dụ "server: 2 miếng [2-3]; peer: 2 miếng [0-1]" để ghi log."""
    groups: dict[str, list[int]] = {}
    for item in plan:
        key = item.source if item.source == "server" else f"peer {item.url.split('/chunks/')[0]}"
        groups.setdefault(key, []).append(item.index)
    if not groups:
        return "plan rỗng"
    return "; ".join(f"{key}: {len(idx)} miếng {format_ranges(idx)}" for key, idx in groups.items())


def format_ranges(indexes: Sequence[int]) -> str:
    """[0, 1, 2, 5] -> "[0-2, 5]"."""
    ordered = sorted(indexes)
    if not ordered:
        return "[]"
    parts: list[str] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = value
    parts.append(f"{start}-{prev}" if start != prev else str(start))
    return f"[{', '.join(parts)}]"
