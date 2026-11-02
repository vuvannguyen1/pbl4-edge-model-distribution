"""Client agent: register -> probe -> request -> tải -> have -> inference -> feedback.

Chạy:
    python -m client.agent --server http://localhost:8000 --device-id laptopA ^
        --image samples\\dog.jpg

Bỏ `--image` thì client mở hộp thoại để chọn ảnh trên máy.

Trước khi gọi server, client tìm trong kho cục bộ (`client/model_cache.py`) một model
`complete`, đúng task và SHA-256. Có thì nhận diện ngay bằng model đó, không xin model
mới; chưa có (hoặc model hỏng) thì mới register -> request -> tải. `--refresh` buộc hỏi
server dù đã có model; `--serve` cũng luôn hỏi để server ghi nhận máy này làm peer.

Không kết nối được server (chờ mở kết nối tối đa 5 s): dùng model `complete` có sẵn,
bỏ qua feedback tự động; chưa có model thì báo rõ là chưa detect offline được. Đánh
giá của người dùng gửi bằng `python -m client.feedback_cli`; các đánh giá còn `pending`
được gửi bù khi agent kết nối được server (cả khi server mở lại lúc đang `--serve`).

Mã thoát: 0 thành công, 2 server báo NO_VARIANT_FITS, 1 lỗi khác.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from client.api import ApiClient, ApiError, build_feedback, build_model_request, build_register
from client.device_info import (
    DeviceInfo,
    collect_device_info,
    default_device_id,
    sanitize_device_id,
)
from client.downloader import ChunkDownloader, DownloadError, DownloadResult, SwitchRequested
from client.feedback import FeedbackQueue, save_last_detection
from client.inference import Classifier, InferenceResult, format_top5, load_labels
from client.model_cache import (  # noqa: F401 - verified_variants giữ đường import cũ
    ModelCache,
    ModelInvalid,
    save_active,
    verified_variants,
)
from client.monitor import ServerMonitor, server_reachable
from client.peer import PeerService, port_available
from client.picker import pick_images

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = REPO_ROOT / "samples" / "imagenet_synset.txt"
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_VARIANT = 2
MAX_SWITCHES = 1
BYTES_PER_MB = 1024 * 1024

Printer = Callable[[str], None]


class AgentError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass
class AgentConfig:
    server: str
    device_id: str
    image: Path | None  # None: main() mở hộp thoại chọn ảnh trước khi chạy
    deadline_s: float = 60.0
    bandwidth_kbps: float | None = None
    preferred_variant: str | None = None
    peer_port: int = 9000
    cache_dir: Path | None = None
    runs: int = 20
    platform: str | None = None
    follow_switch: bool = False
    serve: bool = False
    labels: Path = DEFAULT_LABELS
    probe_kb: int = 256
    refresh: bool = False  # True: hỏi server dù kho đã có model hợp lệ

    def resolved_cache_dir(self) -> Path:
        return self.cache_dir or (REPO_ROOT / "data" / "client" / self.device_id)


@dataclass
class AgentResult:
    device_class: str
    request_id: str
    variant: dict[str, Any]
    model_path: Path
    download_ms: float
    download: DownloadResult | None
    inference: InferenceResult
    feedback_payload: dict[str, Any]
    feedback: dict[str, Any]
    history: list[str] = field(default_factory=list)  # các variant đã xin, theo thứ tự
    offline: bool = False  # True: không kết nối được server, dùng model trong cache
    source: str = "server"  # "server": vừa xin model; "cache": dùng model có sẵn, không xin


# ---------------------------------------------------------------- agent


class Agent:
    def __init__(self, config: AgentConfig, out: Printer = print) -> None:
        self.config = config
        self.out = out
        self.api = ApiClient(config.server)
        self.cache_dir = config.resolved_cache_dir()
        self.device_class = ""
        self.free_storage_mb: float | None = None
        self.peer_port = config.peer_port  # 0 nếu không chia sẻ được
        self.peer_service: PeerService | None = None
        self.monitor: ServerMonitor | None = None
        self.info: DeviceInfo | None = None
        self.cache = ModelCache(self.cache_dir)  # step_device gắn runtime/cpu_arch hiện tại
        self.queue = FeedbackQueue(self.cache_dir)

    def section(self, step: int | None, title: str) -> None:
        self.out("")
        prefix = f"[{step}/7] " if step is not None else ""
        self.out(f"══ {prefix}{title} " + "═" * max(3, 60 - len(title)))

    # ------------------------------------------------------------ các bước

    def step_device(self) -> DeviceInfo:
        cfg = self.config
        info = collect_device_info(self.cache_dir, cfg.platform)
        self.info = info
        self.cache = ModelCache(self.cache_dir, runtime=info.runtime, cpu_arch=info.cpu_arch)
        self.section(1, "Thông tin thiết bị")
        self.out(f"  device_id      : {cfg.device_id}")
        self.out(f"  platform       : {info.platform}")
        self.out(f"  cpu_arch       : {info.cpu_arch}")
        self.out(f"  ram_mb         : {info.ram_mb}")
        self.out(f"  runtime        : {info.runtime}")
        storage = (
            f"{info.free_storage_mb:,.0f} MB"
            if info.free_storage_mb is not None
            else "không đo được"
        )
        self.out(f"  dung lượng trống: {storage} ({self.cache_dir})")
        if cfg.peer_port and not port_available(cfg.peer_port):
            self.out(
                f"  [cảnh báo] Cổng peer {cfg.peer_port} đang bị chương trình khác dùng; "
                "đăng ký peer_port = null (không chia sẻ). Dùng --peer-port khác để chia sẻ."
            )
            self.peer_port = 0
        self.out(f"  peer_port      : {self.peer_port or 'null (không chia sẻ)'}")
        self.free_storage_mb = info.free_storage_mb
        return info

    def step_register(self) -> None:
        cfg = self.config
        info = self.info or self.step_device()
        self.section(2, "Đăng ký với server (POST /api/register)")
        payload = build_register(
            device_id=cfg.device_id,
            platform=info.platform,
            cpu_arch=info.cpu_arch,
            ram_mb=info.ram_mb,
            runtime=info.runtime,
            peer_port=self.peer_port or None,
        )
        response = self.api.register(payload)
        self.device_class = response["device_class"]
        self.out(f"  Server: {cfg.server}")
        self.out(f"  device_class   : {self.device_class}")
        self.out(f"  chunk_size     : {response['chunk_size']:,} byte")
        self.out(f"  peer_enabled   : {response['peer_enabled']}")

    def step_bandwidth(self) -> float | None:
        cfg = self.config
        self.section(3, "Đo băng thông (GET /api/probe)")
        if cfg.bandwidth_kbps:
            self.out(f"  Dùng giá trị --bandwidth: {cfg.bandwidth_kbps:,.0f} kbps (không đo)")
            return cfg.bandwidth_kbps
        size, seconds = self.api.probe(cfg.probe_kb)
        if seconds <= 0 or size <= 0:
            self.out("  Không đo được, gửi bandwidth_kbps = null (server dùng giới hạn của nó)")
            return None
        kbps = size * 8 / 1000 / seconds
        self.out(f"  Nhận {size:,} byte trong {seconds:.3f} s → {kbps:,.0f} kbps")
        return kbps

    def step_request(self, bandwidth: float | None, preferred: str | None) -> dict[str, Any]:
        cfg = self.config
        local = self.cache.valid_models()
        self.section(4, "Xin model (POST /api/request)")
        payload = build_model_request(
            device_id=cfg.device_id,
            bandwidth_kbps=bandwidth,
            deadline_s=cfg.deadline_s,
            free_storage_mb=self.free_storage_mb,
            preferred_variant=preferred,
            have_variants=local.keys(),
        )
        bw_text = f"{payload['bandwidth_kbps']:,} kbps" if payload["bandwidth_kbps"] else "null"
        self.out(
            f"  Gửi: băng thông {bw_text}, hạn {cfg.deadline_s:g} s, preferred_variant="
            f"{preferred}, have_variants={payload['have_variants']}"
        )
        try:
            response = self.api.request_model(payload)
        except ApiError as exc:
            if exc.code == "NO_VARIANT_FITS":
                self.out(f"  [LỖI] {exc.code} (HTTP {exc.status}): {exc.message}")
                raise AgentError(exc.message, EXIT_NO_VARIANT) from exc
            raise
        variant = response["variant"]
        sources: dict[str, int] = {}
        for item in response["plan"]:
            sources[item["source"]] = sources.get(item["source"], 0) + 1
        estimate = response["estimated_download_s"]
        self.out(f"  request_id     : {response['request_id']}")
        self.out(
            f"  Variant        : {variant['id']} ({variant['file']}, "
            f"{variant['size_bytes'] / BYTES_PER_MB:.2f} MB, {variant['chunk_count']} miếng, "
            f"{variant['precision']}, top-1 {variant['top1_accuracy']})"
        )
        self.out(f"  Lý do          : {response['reason']}")
        self.out(
            "  Tải ước tính   : " + (f"{estimate:g} s" if estimate is not None else "không rõ")
        )
        if response["plan"]:
            parts = ", ".join(f"{n} miếng từ {src}" for src, n in sorted(sources.items()))
            self.out(f"  Kế hoạch tải   : {parts}")
            peers = sorted(
                {p["url"].split("/chunks/")[0] for p in response["plan"] if p["source"] == "peer"}
            )
            for origin in peers:
                indexes = [
                    p["index"] for p in response["plan"] if p["url"].startswith(origin + "/")
                ]
                self.out(f"    peer {origin}: miếng {indexes} (fallback về server nếu lỗi)")
        else:
            self.out("  Kế hoạch tải   : rỗng, dùng file đã có trong cache")
        return response

    def step_download(self, response: dict[str, Any]) -> tuple[Path, DownloadResult | None]:
        variant = response["variant"]
        self.section(5, f"Tải model ({len(response['plan'])} miếng, tối đa 4 song song)")
        if not response["plan"]:
            local = self.cache.get(variant["id"])
            if local is None:
                raise AgentError(
                    f"Server trả plan rỗng nhưng cache không có {variant['id']} hợp lệ. "
                    "Hãy xóa cache hoặc chạy lại."
                )
            self.out(f"  Đã có sẵn {local[1]} (SHA-256 khớp), không cần tải")
            return local[1], None
        if self.cache.get(variant["id"]) is None:
            self.cache.mark_partial(variant)  # file đang dùng được thì giữ complete
        downloader = ChunkDownloader(self.api, self.config.device_id, log=self.out)
        result = downloader.download(
            response["request_id"], variant, response["plan"], self.cache_dir
        )
        try:
            self.cache.complete(variant)
        except ModelInvalid as exc:
            raise AgentError(f"Model vừa tải không dùng được, đã đánh dấu invalid: {exc}") from exc
        self.out(f"  Kho cục bộ: {variant['id']} complete (đủ file, SHA-256 khớp, runtime mở được)")
        self.out(
            f"  Tải xong: {result.chunks_from_server} miếng từ server, "
            f"{result.chunks_from_peer} từ peer, {result.chunks_reused} dùng lại, "
            f"{len(result.bad_chunks)} lần lỗi"
        )
        return result.path, result

    def step_inference(
        self,
        model_path: Path,
        variant: dict[str, Any],
        step: int | None = 6,
        request_id: str | None = None,
    ) -> InferenceResult:
        cfg = self.config
        self.section(step, f"Chạy suy luận ONNX Runtime ({variant['id']})")
        if not cfg.labels.is_file():
            raise AgentError(f"Thiếu file nhãn {cfg.labels}. Chạy: python tools/download_assets.py")
        if cfg.image is None:
            raise AgentError("Chưa chọn ảnh để nhận diện.")
        labels = load_labels(cfg.labels)
        try:
            classifier = Classifier(model_path, variant, labels)
        except Exception as exc:  # file đúng hash nhưng runtime không mở được
            self.cache.mark_invalid(variant, f"ONNX Runtime không mở được: {exc}")
            raise AgentError(
                f"ONNX Runtime không mở được {variant['id']}, đã đánh dấu invalid; lần chạy sau "
                f"có server sẽ tải lại. Chi tiết: {type(exc).__name__}: {exc}"
            ) from exc
        result = classifier.classify(cfg.image, cfg.runs)
        top1 = result.top5[0] if result.top5 else None
        save_last_detection(
            self.cache_dir,
            variant_id=variant["id"],
            image=cfg.image,
            prediction=top1.label if top1 else None,
            confidence=top1.probability if top1 else None,
            request_id=request_id,
        )
        self.out(f"  Ảnh            : {cfg.image}")
        self.out(f"  Input          : '{result.input_name}' {variant['input_shape']}")
        self.out(f"  Nạp model      : {result.load_ms:.1f} ms")
        self.out(
            f"  Latency trung vị: {result.latency_ms_median:.2f} ms "
            f"({result.runs} lần, sau 3 lần warm-up)"
        )
        ram = f"{result.peak_ram_mb:.1f} MB" if result.peak_ram_mb is not None else "không đo được"
        self.out(f"  RAM đỉnh       : {ram}")
        self.out("")
        for line in format_top5(result.top5):
            self.out(line)
        return result

    def step_feedback(
        self,
        response: dict[str, Any],
        download_ms: float,
        download: DownloadResult | None,
        inference: InferenceResult,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self.section(7, "Gửi phản hồi (POST /api/feedback)")
        top1 = inference.top5[0] if inference.top5 else None
        payload = build_feedback(
            device_id=self.config.device_id,
            request_id=response["request_id"],
            variant_id=response["variant"]["id"],
            download_ms=download_ms,
            chunks_from_peer=download.chunks_from_peer if download else 0,
            chunks_from_server=download.chunks_from_server if download else 0,
            load_ms=inference.load_ms,
            latency_ms_median=inference.latency_ms_median,
            runs=inference.runs,
            peak_ram_mb=inference.peak_ram_mb,
            top1_label=top1.label if top1 else None,
            top1_confidence=top1.probability if top1 else None,
        )
        self.out(
            f"  Gửi: download {payload['download_ms']:,} ms, load {payload['load_ms']} ms, "
            f"latency {payload['latency_ms_median']} ms, RAM {payload['peak_ram_mb']} MB, "
            f"top-1 {payload['top1_label']} ({payload['top1_confidence']})"
        )
        try:
            feedback = self.api.feedback(payload)
        except ApiError as exc:
            if exc.code != "CONNECTION":
                raise
            # đã nhận diện xong: mất server lúc này không làm hỏng kết quả detect
            self.out(f"  [cảnh báo] Không gửi được số đo vì mất kết nối server: {exc.message}")
            return payload, {}
        self.out(
            f"  Quyết định     : {feedback['action'].upper()}"
            + (f" → {feedback['new_variant_id']}" if feedback["new_variant_id"] else "")
        )
        self.out(f"  Áp dụng cho lớp: {feedback['applies_to_class']}")
        self.out(f"  Lý do          : {feedback['reason']}")
        return payload, feedback

    # ------------------------------------------------------------ peer (Phase 4)

    def start_peer_service(self) -> None:
        """Bật peer service (một lần) sau khi có variant hoàn chỉnh đã kiểm hash."""
        if not self.peer_port:
            return
        if self.peer_service is None:
            service = PeerService(self.cache_dir, self.peer_port, log=self.out)
            try:
                service.start()
            except OSError as exc:
                self.out(
                    f"  [cảnh báo] Không mở được cổng peer {self.peer_port}: {exc}. "
                    "Máy khác sẽ tự tải lại từ server (fallback)."
                )
                return
            self.peer_service = service
        shared = self.peer_service.refresh()
        self.out(
            f"  Peer service đang chạy ở cổng {self.peer_service.port}, chia sẻ: "
            f"{', '.join(shared) or 'chưa có'}"
        )

    def serve_forever(self) -> None:
        """Giữ tiến trình chạy để máy khác lấy miếng, đến khi bấm Ctrl+C."""
        if self.peer_service is None:
            self.out("  Peer service chưa bật (peer_port = 0 hoặc chưa có variant hoàn chỉnh)")
            return
        self.out("")
        self.out(
            f"Đang phục vụ peer ở cổng {self.peer_service.port} "
            f"({', '.join(self.peer_service.shared_ids())}). Bấm Ctrl+C để dừng."
        )
        if self.monitor is not None:
            self.out(f"Đang theo dõi kết nối tới server {self.api.server_url}.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.out(f"Dừng peer service, đã gửi {self.peer_service.served} miếng cho máy khác.")

    def stop_peer_service(self) -> None:
        if self.peer_service is not None:
            self.peer_service.stop()
            self.peer_service = None

    # ------------------------------------------------------------ theo dõi server

    def start_monitor(self) -> None:
        if self.monitor is None:
            self.monitor = ServerMonitor(
                self.api.server_url, out=self.out, on_reconnect=self.sync_feedback
            )
            self.monitor.start()

    def stop_monitor(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None

    def close(self) -> None:
        """Dừng luồng theo dõi server và peer service."""
        self.stop_monitor()
        self.stop_peer_service()

    # ------------------------------------------------------------ feedback người dùng

    def sync_feedback(self) -> None:
        """Gửi bù các đánh giá `pending` (nhập bằng client.feedback_cli khi server tắt)."""
        pending = self.queue.pending()
        if not pending:
            return
        self.out(f"  Gửi bù {len(pending)} đánh giá đang chờ (POST /api/feedback)...")
        report = self.queue.sync(ApiClient(self.api.server_url), out=self.out)
        self.out(
            f"  Đã gửi {len(report.sent)}, server từ chối {len(report.rejected)}"
            + (f", còn {report.remaining} pending ({report.error})" if report.error else "")
        )

    def feedback_hint(self, variant_id: str) -> None:
        self.out("")
        self.out(
            "  Gửi đánh giá kết quả: python -m client.feedback_cli "
            f"--device-id {self.config.device_id} --variant-id {variant_id} "
            f"--server {self.api.server_url}"
        )

    # ------------------------------------------------------------ model có sẵn / offline

    def find_local(self) -> tuple[dict[str, Any], Path, str] | None:
        try:
            return self.cache.find_local_model(self.config.preferred_variant)
        except LookupError:
            return None

    def _local_result(
        self, variant: dict[str, Any], path: Path, inference: InferenceResult, offline: bool
    ) -> AgentResult:
        return AgentResult(
            device_class=self.device_class,
            request_id="",
            variant=variant,
            model_path=path,
            download_ms=0.0,
            download=None,
            inference=inference,
            feedback_payload={},
            feedback={},
            history=[variant["id"]],
            offline=offline,
            source="cache",
        )

    def run_local(self, variant: dict[str, Any], path: Path, why: str) -> AgentResult:
        """Kho đã có model hợp lệ: không xin model mới, nhận diện ngay bằng model này."""
        online = server_reachable(self.api.server_url)
        self.section(None, "Model có sẵn trên máy (không gửi /api/request)")
        self.out(f"  Variant        : {variant['id']} ({why})")
        self.out(
            f"  Trạng thái     : complete, kích thước và SHA-256 khớp ({variant['sha256'][:16]}…)"
        )
        self.out(f"  File           : {path}")
        if online:
            self.out(f"  Server {self.api.server_url} đang chạy; thêm --refresh để hỏi bản mới")
        else:
            self.out(
                f"  Không kết nối được server {self.api.server_url}: "
                "Chuyển sang chế độ offline, dùng model này"
            )
        save_active(self.cache_dir, variant["id"])
        inference = self.step_inference(path, variant, step=None)
        if online:
            try:
                self.sync_feedback()
            except Exception as exc:  # gửi bù là phụ, không làm hỏng kết quả detect
                self.out(f"  [cảnh báo] Chưa gửi bù được đánh giá: {exc}")
        self.feedback_hint(variant["id"])
        return self._local_result(variant, path, inference, offline=not online)

    def run_offline(self, error: ApiError) -> AgentResult:
        """Không kết nối được server: chạy bằng model `complete` trong kho, không gửi feedback."""
        self.out("")
        self.out(f"  [cảnh báo] Không kết nối được server {self.api.server_url}")
        try:
            variant, path, why = self.cache.find_local_model(self.config.preferred_variant)
        except LookupError as exc:
            raise AgentError(
                f"Không kết nối được server {self.api.server_url} và chưa thể detect offline vì "
                f"máy chưa có model phù hợp. {exc} Hãy chạy lại khi có kết nối server để tải "
                "model."
            ) from error
        self.out(f"  Chuyển sang chế độ offline: dùng {variant['id']} ({why}), SHA-256 khớp")
        self.out(f"  File: {path}")
        inference = self.step_inference(path, variant)
        self.section(7, "Gửi phản hồi")
        self.out("  Bỏ qua vì đang offline")
        self.feedback_hint(variant["id"])
        return self._local_result(variant, path, inference, offline=True)

    # ------------------------------------------------------------ luồng chính

    def run(self) -> AgentResult:
        self.step_device()
        if not (self.config.refresh or self.config.serve):
            local = self.find_local()
            if local is not None:
                return self.run_local(*local)
        try:
            self.step_register()
            self.start_monitor()
            self.sync_feedback()
            bandwidth = self.step_bandwidth()
        except ApiError as exc:
            if exc.code != "CONNECTION":
                raise
            self.stop_monitor()
            return self.run_offline(exc)
        try:
            return self._run_online(bandwidth)
        except ApiError as exc:
            # server tắt trước khi kịp nhận diện (lỗi ở bước feedback đã được bỏ qua)
            if exc.code != "CONNECTION":
                raise
            self.stop_monitor()
            return self.run_offline(exc)
        finally:
            if not self.config.serve:  # --serve: tiếp tục theo dõi trong lúc phục vụ peer
                self.stop_monitor()

    def _run_online(self, bandwidth: float | None) -> AgentResult:
        preferred = self.config.preferred_variant
        switches_left = MAX_SWITCHES
        history: list[str] = []

        while True:
            response = self.step_request(bandwidth, preferred)
            history.append(response["variant"]["id"])
            plan_received = time.perf_counter()
            try:
                model_path, download = self.step_download(response)
            except SwitchRequested as switch:
                self.out(f"  Server yêu cầu đổi sang {switch.new_variant_id}: {switch.reason}")
                if switches_left <= 0:
                    raise AgentError("Server yêu cầu đổi variant quá số lần cho phép") from switch
                switches_left -= 1
                preferred = switch.new_variant_id
                continue
            download_ms = (time.perf_counter() - plan_received) * 1000
            save_active(self.cache_dir, response["variant"]["id"])
            self.start_peer_service()

            inference = self.step_inference(
                model_path, response["variant"], request_id=response["request_id"]
            )
            payload, feedback = self.step_feedback(response, download_ms, download, inference)

            if feedback.get("action") == "switch" and feedback["new_variant_id"]:
                if self.config.follow_switch and switches_left > 0:
                    switches_left -= 1
                    preferred = feedback["new_variant_id"]
                    self.out(f"  --follow-switch: xin lại với preferred_variant={preferred}")
                    continue
                if not self.config.follow_switch:
                    self.out(
                        "  Gợi ý: chạy lại với --preferred-variant "
                        f"{feedback['new_variant_id']} hoặc thêm --follow-switch"
                    )
            self.feedback_hint(response["variant"]["id"])
            return AgentResult(
                device_class=self.device_class,
                request_id=response["request_id"],
                variant=response["variant"],
                model_path=model_path,
                download_ms=download_ms,
                download=download,
                inference=inference,
                feedback_payload=payload,
                feedback=feedback,
                history=history,
            )


# ---------------------------------------------------------------- CLI


def parse_args(argv: Sequence[str] | None = None) -> AgentConfig:
    parser = argparse.ArgumentParser(
        prog="python -m client.agent",
        description="Client agent PBL4: xin model theo băng thông, tải, kiểm hash, chạy ONNX.",
    )
    parser.add_argument("--server", default="http://localhost:8000", help="URL server")
    parser.add_argument("--device-id", default=None, help="mặc định: tên máy đã lọc ký tự")
    parser.add_argument(
        "--image", type=Path, default=None, help="ảnh để chạy thử; bỏ trống thì mở hộp thoại chọn"
    )
    parser.add_argument("--deadline", type=float, default=60.0, help="hạn tải (giây)")
    parser.add_argument(
        "--bandwidth", type=float, default=None, help="kbps; bỏ trống thì đo bằng /api/probe"
    )
    parser.add_argument("--preferred-variant", default=None)
    parser.add_argument("--peer-port", type=int, default=9000, help="0 = không chia sẻ")
    parser.add_argument("--cache-dir", type=Path, default=None, help="mặc định data/client/<id>")
    parser.add_argument("--runs", type=int, default=20, help="số lần chạy để lấy trung vị")
    parser.add_argument(
        "--platform",
        default=None,
        choices=["android", "android_browser", "ios_browser", "windows", "linux"],
    )
    parser.add_argument(
        "--follow-switch", action="store_true", help="tự xin lại khi feedback trả switch (1 lần)"
    )
    parser.add_argument(
        "--serve", action="store_true", help="giữ peer service chạy đến khi bấm Ctrl+C"
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="file synset")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="hỏi server dù máy đã có model hợp lệ (mặc định dùng model có sẵn)",
    )
    args = parser.parse_args(argv)

    if args.peer_port and not 1024 <= args.peer_port <= 65535:
        parser.error("--peer-port phải là 0 hoặc trong khoảng 1024-65535")
    if args.runs < 1:
        parser.error("--runs phải >= 1")
    if args.deadline <= 0:
        parser.error("--deadline phải > 0")
    if args.bandwidth is not None and args.bandwidth <= 0:
        parser.error("--bandwidth phải > 0")
    device_id = sanitize_device_id(args.device_id) if args.device_id else default_device_id()
    if args.device_id and device_id != args.device_id:
        parser.error("--device-id chỉ gồm A-Z a-z 0-9 _ -, tối đa 32 ký tự")
    return AgentConfig(
        server=args.server,
        device_id=device_id,
        image=args.image,
        deadline_s=args.deadline,
        bandwidth_kbps=args.bandwidth,
        preferred_variant=args.preferred_variant,
        peer_port=args.peer_port,
        cache_dir=args.cache_dir,
        runs=args.runs,
        platform=args.platform,
        follow_switch=args.follow_switch,
        serve=args.serve,
        labels=args.labels,
        refresh=args.refresh,
    )


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    config = parse_args(argv)
    if config.image is None:
        print("Chọn ảnh cần nhận diện trong hộp thoại...")
        picked = pick_images(multiple=False)
        if not picked:
            print("[DỪNG] Chưa chọn ảnh.")
            return EXIT_ERROR
        config.image = picked[0]
    if not config.image.is_file():
        print(f"[LỖI] Không thấy ảnh {config.image}")
        return EXIT_ERROR
    agent = Agent(config)
    try:
        result = agent.run()
        top1 = result.inference.top5[0]
        print("")
        if result.offline:
            print(
                f"HOÀN TẤT (offline): {result.variant['id']} trong cache, "
                f"top-1 '{top1.label}' {top1.probability * 100:.1f} %"
            )
        elif result.source == "cache":
            print(
                f"HOÀN TẤT: {result.variant['id']} có sẵn trên máy (không xin model), "
                f"top-1 '{top1.label}' {top1.probability * 100:.1f} %"
            )
        else:
            decision = result.feedback.get("action", "chưa gửi được (mất kết nối)")
            print(
                f"HOÀN TẤT: {result.variant['id']} (request {result.request_id}), "
                f"top-1 '{top1.label}' {top1.probability * 100:.1f} %, "
                f"feedback {decision}"
            )
        if config.serve:
            agent.serve_forever()
        return EXIT_OK
    except AgentError as exc:
        print(f"\n[DỪNG] {exc}")
        return exc.exit_code
    except ApiError as exc:
        if exc.code == "CONNECTION":
            print(
                f"\n[MẤT KẾT NỐI] Không kết nối được server {agent.api.server_url}: server đã "
                "đóng hoặc mạng bị ngắt giữa chừng."
            )
        else:
            print(f"\n[LỖI SERVER] {exc.code}: {exc.message}")
        return EXIT_ERROR
    except DownloadError as exc:
        print(f"\n[LỖI TẢI] {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nĐã dừng (Ctrl+C)")
        return EXIT_ERROR
    except Exception as exc:  # lỗi ảnh/ONNX/đĩa: báo gọn cho người xem demo
        print(f"\n[LỖI] {type(exc).__name__}: {exc}")
        return EXIT_ERROR
    finally:
        agent.close()


if __name__ == "__main__":
    sys.exit(main())
