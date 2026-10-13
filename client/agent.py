"""Client agent: register -> probe -> request -> tải -> have -> inference -> feedback.

Chạy:
    python -m client.agent --server http://localhost:8000 --device-id laptopA ^
        --image samples\\dog.jpg

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
from client.device_info import collect_device_info, default_device_id, sanitize_device_id
from client.downloader import (
    ChunkDownloader,
    DownloadError,
    DownloadResult,
    SwitchRequested,
    save_variant_meta,
    verified_variants,
)
from client.inference import InferenceResult, load_labels, run_inference
from client.peer import PeerService, port_available

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
    image: Path
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

    def section(self, step: int, title: str) -> None:
        self.out("")
        self.out(f"══ [{step}/7] {title} " + "═" * max(3, 60 - len(title)))

    # ------------------------------------------------------------ các bước

    def step_register(self) -> None:
        cfg = self.config
        info = collect_device_info(self.cache_dir, cfg.platform)
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
        local = verified_variants(self.cache_dir)
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
            local = verified_variants(self.cache_dir).get(variant["id"])
            if local is None:
                raise AgentError(
                    f"Server trả plan rỗng nhưng cache không có {variant['id']} hợp lệ. "
                    "Hãy xóa cache hoặc chạy lại."
                )
            self.out(f"  Đã có sẵn {local[1]} (SHA-256 khớp), không cần tải")
            return local[1], None
        downloader = ChunkDownloader(self.api, self.config.device_id, log=self.out)
        result = downloader.download(
            response["request_id"], variant, response["plan"], self.cache_dir
        )
        save_variant_meta(self.cache_dir, variant)
        self.out(
            f"  Tải xong: {result.chunks_from_server} miếng từ server, "
            f"{result.chunks_from_peer} từ peer, {result.chunks_reused} dùng lại, "
            f"{len(result.bad_chunks)} lần lỗi"
        )
        return result.path, result

    def step_inference(self, model_path: Path, variant: dict[str, Any]) -> InferenceResult:
        cfg = self.config
        self.section(6, f"Chạy suy luận ONNX Runtime ({variant['id']})")
        if not cfg.labels.is_file():
            raise AgentError(f"Thiếu file nhãn {cfg.labels}. Chạy: python tools/download_assets.py")
        labels = load_labels(cfg.labels)
        result = run_inference(model_path, cfg.image, variant, labels, cfg.runs)
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
        self.out("   #  Nhãn                              Xác suất")
        self.out("  ──  ────────────────────────────────  ────────")
        for rank, pred in enumerate(result.top5, start=1):
            self.out(f"  {rank:>2}  {pred.label[:32]:<32}  {pred.probability * 100:7.2f} %")
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
        feedback = self.api.feedback(payload)
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
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.out(f"Dừng peer service, đã gửi {self.peer_service.served} miếng cho máy khác.")

    def stop_peer_service(self) -> None:
        if self.peer_service is not None:
            self.peer_service.stop()
            self.peer_service = None

    # ------------------------------------------------------------ luồng chính

    def run(self) -> AgentResult:
        self.step_register()
        bandwidth = self.step_bandwidth()
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
            self.start_peer_service()

            inference = self.step_inference(model_path, response["variant"])
            payload, feedback = self.step_feedback(response, download_ms, download, inference)

            if feedback["action"] == "switch" and feedback["new_variant_id"]:
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
    parser.add_argument("--image", required=True, type=Path, help="ảnh để chạy thử")
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
    )


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    config = parse_args(argv)
    if not config.image.is_file():
        print(f"[LỖI] Không thấy ảnh {config.image}")
        return EXIT_ERROR
    agent = Agent(config)
    try:
        result = agent.run()
        print("")
        print(
            f"HOÀN TẤT: {result.variant['id']} (request {result.request_id}), "
            f"top-1 '{result.inference.top5[0].label}' "
            f"{result.inference.top5[0].probability * 100:.1f} %, "
            f"feedback {result.feedback['action']}"
        )
        if config.serve:
            agent.serve_forever()
        return EXIT_OK
    except AgentError as exc:
        print(f"\n[DỪNG] {exc}")
        return exc.exit_code
    except ApiError as exc:
        print(f"\n[LỖI SERVER] {exc.code}: {exc.message}")
        return EXIT_ERROR
    except DownloadError as exc:
        print(f"\n[LỖI TẢI] {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("\nĐã dừng (Ctrl+C)")
        return EXIT_ERROR
    finally:
        agent.stop_peer_service()


if __name__ == "__main__":
    sys.exit(main())
