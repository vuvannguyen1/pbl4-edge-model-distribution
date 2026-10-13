"""Dịch vụ phân phối: điều phối Registry, Selector, Planner, trạng thái và feedback.

Handler HTTP trong `server/main.py` chỉ đọc/ghi HTTP rồi gọi các hàm ở đây.
Mọi dòng log của một lượt xin model đều bắt đầu bằng `[request_id]`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from server.config import Settings
from server.devices import Device, DeviceStore
from server.errors import ProtocolError, bad_request, unknown_device, unknown_variant
from server.feedback import FeedbackStore, decide
from server.planner import PeerSource, build_plan, format_ranges, summarize_plan
from server.registry import Registry, RegistryVariant
from server.schemas import (
    FeedbackRequest,
    FeedbackResponse,
    HaveRequest,
    HaveResponse,
    ModelRequest,
    ModelResponse,
    PlanItem,
    RegisterRequest,
    RegisterResponse,
)
from server.selector import (
    ModelSelector,
    NoVariantFits,
    SelectionContext,
    apply_hard_filters,
    estimate_download_s,
    fmt_num,
    no_fit_message,
)
from server.state import DownloadRequest, StateStore, url_origin

logger = logging.getLogger("pbl4.server")


@dataclass(frozen=True)
class ChunkLocation:
    path: Path
    offset: int
    size_bytes: int


class DistributionService:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: Registry,
        selector: ModelSelector,
        devices: DeviceStore | None = None,
        state: StateStore | None = None,
        feedback_store: FeedbackStore | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.selector = selector
        self.devices = devices or DeviceStore()
        self.state = state or StateStore()
        self.feedback_store = feedback_store or FeedbackStore(settings.feedback_dir)

    # ------------------------------------------------------------ đăng ký

    def register(self, req: RegisterRequest, ip: str) -> RegisterResponse:
        try:
            device, existed = self.devices.register(
                device_id=req.device_id,
                platform=req.platform,
                cpu_arch=req.cpu_arch,
                ram_mb=req.ram_mb,
                runtime=req.runtime,
                peer_port=req.peer_port,
                ip=ip,
            )
        except ValueError as exc:
            raise bad_request(str(exc)) from exc
        if device.peer_origin is not None:
            # máy khởi động lại thì cho nó cơ hội làm peer lần nữa
            self.state.unban_origin(device.peer_origin)
        logger.info(
            "%s thiết bị %s: lớp %s, ip %s, peer_port %s, %s, RAM %d MB, %s",
            "Đăng ký lại" if existed else "Đăng ký",
            device.device_id,
            device.device_class,
            device.ip,
            device.peer_port,
            device.cpu_arch,
            device.ram_mb,
            device.runtime,
        )
        return RegisterResponse(
            device_id=device.device_id,
            device_class=device.device_class,
            peer_enabled=self.settings.peer_enabled,
        )

    def _device(self, device_id: str) -> Device:
        device = self.devices.get(device_id)
        if device is None:
            raise unknown_device(device_id)
        return device

    # ------------------------------------------------------------ xin model

    def effective_bandwidth(self, requested: float | None) -> float | None:
        """Băng thông dùng để tính: của request, nếu null thì giới hạn server, 0 -> None."""
        if requested:
            return requested
        return self.settings.rate_limit_kbps or None

    def request_model(self, req: ModelRequest, base_url: str) -> ModelResponse:
        device = self._device(req.device_id)
        self.registry.refresh_if_changed()
        bandwidth = self.effective_bandwidth(req.bandwidth_kbps)
        context = SelectionContext(
            device_id=device.device_id,
            device_class=device.device_class,
            platform=device.platform,
            cpu_arch=device.cpu_arch,
            ram_mb=device.ram_mb,
            runtime=device.runtime,
            task=req.task,
            bandwidth_kbps=bandwidth,
            deadline_s=req.deadline_s,
            free_storage_mb=req.free_storage_mb,
            class_preferred_variant=self.state.class_variant(device.device_class),
        )

        if req.preferred_variant is not None:
            variant, reason, selector_version, decision_log = self._preferred(
                req.preferred_variant, context
            )
        else:
            variant, reason, selector_version, decision_log = self._select(context)

        estimate = estimate_download_s(variant.size_bytes, bandwidth)
        already_have = False
        unverified_have = False
        if variant.variant_id in req.have_variants:
            if self.state.has_complete(device.device_id, variant.variant_id):
                already_have = True
                estimate = 0.0
                reason += (
                    f" Thiết bị đã có đủ {variant.variant_id} (server đã ghi nhận qua "
                    "/api/have), không cần tải lại."
                )
            else:
                unverified_have = True

        plan: list[PlanItem] = []
        peers: list[PeerSource] = []
        if not already_have:
            peers = self._find_peers(device, variant.variant_id)
            plan = build_plan(variant, base_url, peers)
            if peers:
                peer_indexes = [p.index for p in plan if p.source == "peer"]
                reason += (
                    f" {', '.join(p.device_id for p in peers)} cùng lớp {device.device_class} "
                    f"đã có đủ {variant.variant_id}, nên lấy miếng {format_ranges(peer_indexes)} "
                    "từ peer, còn lại từ server để tải song song."
                )

        record = self.state.create_request(
            device_id=device.device_id,
            variant_id=variant.variant_id,
            chunk_count=variant.chunk_count,
            selector_version=selector_version,
            reason=reason,
            plan=plan,
            complete=already_have,
        )
        rid = record.request_id
        logger.info(
            "[%s] Xin model: thiết bị %s (lớp %s, ip %s), task %s, băng thông %s "
            "(request: %s), hạn %s s, trống %s MB, preferred %s, have %s",
            rid,
            device.device_id,
            device.device_class,
            device.ip,
            req.task,
            f"{fmt_num(bandwidth)} kbps" if bandwidth else "không rõ",
            req.bandwidth_kbps,
            fmt_num(req.deadline_s),
            req.free_storage_mb,
            req.preferred_variant,
            req.have_variants,
        )
        for line in decision_log:
            logger.info("[%s] %s", rid, line)
        if unverified_have:
            logger.warning(
                "[%s] %s khai báo đã có %s nhưng server chưa ghi nhận complete; "
                "lập plan bình thường",
                rid,
                device.device_id,
                variant.variant_id,
            )
        logger.info(
            "[%s] Chọn %s (%s, %d miếng, %d bytes), selector %s, ước tính %s s. Plan: %s",
            rid,
            variant.variant_id,
            variant.file,
            variant.chunk_count,
            variant.size_bytes,
            selector_version,
            estimate,
            "rỗng, thiết bị đã có đủ" if already_have else summarize_plan(plan),
        )
        return ModelResponse(
            request_id=rid,
            variant=variant.protocol_variant,
            reason=reason,
            estimated_download_s=estimate,
            plan=plan,
        )

    def _preferred(
        self, variant_id: str, context: SelectionContext
    ) -> tuple[RegistryVariant, str, str, list[str]]:
        variant = self.registry.get_ready(variant_id)
        if variant is None:
            raise unknown_variant(variant_id)
        reason = f"Thiết bị yêu cầu đúng {variant_id} (preferred_variant), bỏ qua Selector"
        estimate = estimate_download_s(variant.size_bytes, context.bandwidth_kbps)
        if estimate is not None:
            reason += f"; tải ước tính {fmt_num(estimate)} s"
            if estimate > context.deadline_s:
                reason += f", vượt hạn {fmt_num(context.deadline_s)} s"
        reason += "."
        log = [f"preferred_variant={variant_id}: bỏ qua Selector"]
        return variant, reason, "preferred", log

    def _select(self, context: SelectionContext) -> tuple[RegistryVariant, str, str, list[str]]:
        ready = self.registry.ready_variants()
        candidates, rejections = apply_hard_filters(ready, context)
        log = [
            "Ứng viên ready: " + (", ".join(v.variant_id for v in ready) or "không có"),
            "Qua bộ lọc cứng: " + (", ".join(c.variant_id for c in candidates) or "không có"),
        ]
        log += [f"Loại {r.variant_id}: {r.reason}" for r in rejections]
        if not candidates:
            message = no_fit_message(context, rejections)
            logger.warning("NO_VARIANT_FITS cho %s: %s", context.device_id, message)
            raise ProtocolError("NO_VARIANT_FITS", message)
        try:
            result = self.selector.select(context, candidates)
        except NoVariantFits as exc:
            raise ProtocolError("NO_VARIANT_FITS", exc.message) from exc
        variant = next(c.variant for c in candidates if c.variant_id == result.variant_id)
        reason = result.reason
        if rejections:
            reason += "; loại " + ", ".join(f"{r.variant_id} vì {r.reason}" for r in rejections)
        reason += "."
        log.append(f"Selector {result.selector_version} chọn {result.variant_id}: {result.reason}")
        log += [f"Không chọn {r.variant_id}: {r.reason}" for r in result.rejected]
        return variant, reason, result.selector_version, log

    def _find_peers(self, requester: Device, variant_id: str) -> list[PeerSource]:
        """Peer hợp lệ: khác thiết bị, cùng lớp, có peer_port, đã complete, chưa bị báo sai."""
        if not self.settings.peer_enabled:
            return []
        peers: list[PeerSource] = []
        for device_id in self.state.devices_with_complete(variant_id):
            if device_id == requester.device_id:
                continue
            device = self.devices.get(device_id)
            if device is None or device.device_class != requester.device_class:
                continue
            origin = device.peer_origin
            if origin is None or origin == requester.peer_origin or self.state.is_banned(origin):
                continue
            peers.append(PeerSource(device_id=device_id, origin=origin))
        return peers

    # ------------------------------------------------------------ chunk

    def locate_chunk(self, variant_id: str, index: int) -> ChunkLocation:
        self.registry.refresh_if_changed()
        variant = self.registry.get_ready(variant_id)
        if variant is None:
            raise unknown_variant(variant_id)
        if index < 0 or index >= variant.chunk_count:
            raise ProtocolError(
                "CHUNK_OUT_OF_RANGE",
                f"Miếng {index} nằm ngoài phạm vi 0..{variant.chunk_count - 1} của {variant_id}.",
            )
        path = self.registry.file_path(variant)
        try:
            if path is None:
                raise OSError("storage_path không an toàn")
            actual_size = path.stat().st_size
        except OSError as exc:
            logger.error("Không đọc được file của %s: %s", variant_id, exc)
            raise ProtocolError("INTERNAL", "Server không đọc được file model.") from exc
        if actual_size != variant.size_bytes:
            logger.error(
                "File của %s có %d bytes, Registry ghi %d: cần chạy lại build_registry",
                variant_id,
                actual_size,
                variant.size_bytes,
            )
            raise ProtocolError("INTERNAL", "File model trên server không khớp Registry.")
        chunk = variant.chunks[index]
        return ChunkLocation(path=path, offset=chunk.offset, size_bytes=chunk.size_bytes)

    # ------------------------------------------------------------ have / feedback

    def _request_for(self, device_id: str, request_id: str, variant_id: str) -> DownloadRequest:
        self._device(device_id)
        record = self.state.get_request(request_id)
        if record is None:
            raise bad_request(f"request_id '{request_id}' không tồn tại.")
        if record.device_id != device_id:
            raise bad_request(f"request_id '{request_id}' không thuộc thiết bị '{device_id}'.")
        if record.variant_id != variant_id:
            raise ProtocolError(
                "UNKNOWN_VARIANT",
                f"variant_id '{variant_id}' không khớp với '{record.variant_id}' của {request_id}.",
            )
        return record

    def have(self, req: HaveRequest) -> HaveResponse:
        record = self._request_for(req.device_id, req.request_id, req.variant_id)
        rid = record.request_id
        out_of_range = [i for i in req.chunks if i >= record.chunk_count]
        out_of_range += [b.index for b in req.bad_chunks if b.index >= record.chunk_count]
        if out_of_range:
            raise ProtocolError(
                "CHUNK_OUT_OF_RANGE",
                f"Miếng {sorted(set(out_of_range))} ngoài phạm vi 0..{record.chunk_count - 1}.",
            )

        plan_by_index = {item.index: item for item in record.plan}
        for bad in req.bad_chunks:
            logger.warning("[%s] Miếng lỗi %d từ %s", rid, bad.index, bad.source_url)
            item = plan_by_index.get(bad.index)
            if (
                item is not None
                and item.source == "peer"
                and url_origin(bad.source_url) == url_origin(item.url)
            ):
                self.state.ban_origin(url_origin(item.url))
                logger.warning(
                    "[%s] Ngừng giao peer %s cho các plan sau", rid, url_origin(item.url)
                )

        have_count, total = self.state.add_chunks(rid, req.chunks)
        bandwidth = f"{fmt_num(req.bandwidth_kbps)} kbps" if req.bandwidth_kbps else "null"
        logger.info(
            "[%s] Tiến độ %s: +%d miếng %s, đã có %d/%d, băng thông server %s",
            rid,
            req.device_id,
            len(req.chunks),
            format_ranges(req.chunks),
            have_count,
            total,
            bandwidth,
        )
        if req.complete:
            if self.state.mark_complete(rid):
                logger.info(
                    "[%s] Hoàn tất: %s đã có đủ %s và hash toàn file khớp",
                    rid,
                    req.device_id,
                    req.variant_id,
                )
            else:
                logger.warning(
                    "[%s] %s báo complete nhưng server mới thấy %d/%d miếng; chưa ghi nhận",
                    rid,
                    req.device_id,
                    have_count,
                    total,
                )
        return HaveResponse(action="continue", new_variant_id=None, reason=None)

    def feedback(self, req: FeedbackRequest) -> FeedbackResponse:
        record = self._request_for(req.device_id, req.request_id, req.variant_id)
        device = self._device(req.device_id)
        rid = record.request_id
        decision = decide(
            variant_id=req.variant_id,
            latency_ms_median=req.latency_ms_median,
            device_class=device.device_class,
            current=self.registry.get_ready(req.variant_id),
            ready_variants=self.registry.ready_variants(),
            latency_slo_ms=self.settings.latency_slo_ms,
        )
        if decision.action == "switch" and decision.new_variant_id is not None:
            self.state.remember_class_variant(device.device_class, decision.new_variant_id)

        now = datetime.now(timezone.utc)
        entry = {
            "timestamp": now.isoformat(timespec="milliseconds"),
            **req.model_dump(),
            "device_class": device.device_class,
            "platform": device.platform,
            "runtime": device.runtime,
            "selector_version": record.selector_version,
            "selector_reason": record.reason,
            "decision": asdict(decision),
            "data_kind": "demo",
        }
        path = self.feedback_store.append(entry, now.astimezone())
        logger.info(
            "[%s] Feedback %s/%s: tải %s ms (peer %d, server %d), nạp %s ms, "
            "latency %s ms x%d, RAM %s MB, top1 %s (%s) -> %s %s. Ghi %s",
            rid,
            req.device_id,
            req.variant_id,
            fmt_num(req.download_ms),
            req.chunks_from_peer,
            req.chunks_from_server,
            fmt_num(req.load_ms),
            fmt_num(req.latency_ms_median, 2),
            req.runs,
            req.peak_ram_mb,
            req.top1_label,
            req.top1_confidence,
            decision.action,
            decision.new_variant_id or "",
            path.name,
        )
        return FeedbackResponse(
            action=decision.action,
            new_variant_id=decision.new_variant_id,
            applies_to_class=decision.applies_to_class,
            reason=decision.reason,
        )
