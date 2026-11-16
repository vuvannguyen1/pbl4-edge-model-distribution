"""Chọn peer cho một lượt tải: máy khác đã có đủ variant và nói cùng cách P2P."""

from __future__ import annotations

from server.config import Settings
from server.devices import Device, DeviceStore
from server.planner import PeerSource
from server.signaling import SignalHub
from server.state import StateStore


def find_peers(
    requester: Device,
    variant_id: str,
    *,
    settings: Settings,
    state: StateStore,
    devices: DeviceStore,
    signals: SignalHub,
) -> list[PeerSource]:
    """Peer hợp lệ: khác thiết bị, đã complete variant, chưa bị báo sai, và cùng cách P2P.

    Không xét lớp thiết bị: cùng variant là cùng một file, mỗi miếng đều được kiểm SHA-256,
    nên hai máy khác cấu hình vẫn chia sẻ được. Lớp thiết bị chỉ dùng để chọn model.

    - Client Python (peer_port): peer HTTP `http://<ip>:<peer_port>`.
    - Trình duyệt (capabilities.p2p = webrtc): peer `webrtc://<device_id>` đang mở trang
      (long-poll /api/signal trong vòng 12 s). Trình duyệt không gọi được peer HTTP và
      client Python không nói WebRTC, nên hai loại không bao giờ ghép với nhau.
    """
    if not settings.peer_enabled:
        return []
    wants_webrtc = requester.p2p_transport == "webrtc"
    peers: list[PeerSource] = []
    for device_id in state.devices_with_complete(variant_id):
        if device_id == requester.device_id:
            continue
        device = devices.get(device_id)
        if device is None:
            continue
        if wants_webrtc:
            origin = device.webrtc_origin
            if origin is None or not signals.is_online(device_id):
                continue
        else:
            origin = device.peer_origin
            if origin is None or origin == requester.peer_origin:
                continue
        if state.is_banned(origin):
            continue
        peers.append(PeerSource(device_id=device_id, origin=origin, name=device.name))
    return peers
