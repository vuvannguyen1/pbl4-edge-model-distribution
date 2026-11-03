"""Giao diện web cho client: chọn ảnh, xin/tải model, nhận diện, gửi đánh giá.

Chạy:
    python -m client.web_ui --server http://localhost:8000 --device-id laptopA
rồi mở http://127.0.0.1:8080 trên trình duyệt của chính máy client (kể cả điện thoại
chạy Termux). Thêm `--peer-port 9000` để chia sẻ model cho máy khác (P2P).

Lớp mỏng: mỗi lần "Nhận diện" chạy đúng `client.agent.Agent` trong một luồng nền và
ghi lại các dòng nó in ra; đánh giá đi qua `FeedbackQueue` và các hàm kiểm tra của
`client.feedback_cli`. Web server dùng thư viện chuẩn (như `client/peer.py`) để chạy
được trên Termux mà không cần cài thêm gói.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import requests

from client.agent import (
    DEFAULT_LABELS,
    REPO_ROOT,
    Agent,
    AgentConfig,
    AgentError,
    AgentResult,
)
from client.api import ApiClient, ApiError, build_manual_feedback, normalize_server_url
from client.device_info import collect_device_info, default_device_id, sanitize_device_id
from client.downloader import DownloadError
from client.feedback import (
    STATUS_PENDING,
    FeedbackQueue,
    load_last_detection,
    new_feedback_id,
    utc_now,
)
from client.feedback_cli import (
    _VARIANT_ID,
    MAX_COMMENT,
    MAX_LABEL,
    FeedbackInputError,
    check_text,
    parse_rating,
)
from client.model_cache import ModelCache, load_active, read_meta
from client.monitor import server_reachable

WEB_DIR = Path(__file__).resolve().parent / "web"
SAMPLES_DIR = REPO_ROOT / "samples"
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
REACHABLE_TTL_S = 2.0
HEARTBEAT_EVERY_S = 10.0  # trang đang mở thì báo server máy này còn chạy
COMMENT_TIMEOUT_S = 3.0  # gửi kèm nhận xét chữ tới /api/user-feedback, lỗi thì bỏ qua
HISTORY_LIMIT = 20
DASHBOARD_HINT = "Chạy web_ui với --server http://<IP server>:8000 để mở dashboard từ thiết bị này"


def _is_loopback(host: str) -> bool:
    return host == "localhost" or host == "::1" or host.startswith("127.")


def dashboard_info(server: str, browser_host: str | None) -> dict[str, Any]:
    """Địa chỉ dashboard cho các liên kết trên trang client.

    `browser_host` là header Host trình duyệt dùng để mở trang client. Server ghi 127.0.0.1
    mà trình duyệt ở máy khác (điện thoại mở http://<IP laptop>:8080) thì liên kết sẽ trỏ về
    chính thiết bị đó: báo `reachable=False` để giao diện làm mờ thay vì hiện liên kết hỏng.
    """
    server_host = urlsplit(server).hostname or ""
    viewer = urlsplit(f"//{browser_host}").hostname if browser_host else None
    reachable = not (_is_loopback(server_host) and viewer and not _is_loopback(viewer))
    return {
        "url": f"{server}/ui/",
        "reachable": reachable,
        "hint": None if reachable else DASHBOARD_HINT,
    }


# Dòng agent/downloader in ra: "  [  5/14] miếng   4 ← peer ..." và "══ [5/7] Tải model ═══"
CHUNK_LINE = re.compile(r"\[\s*(\d+)/(\d+)\] miếng\s+\d+ ← (peer|server)")
SECTION_LINE = re.compile(r"^══ (?:\[(\d)/7\] )?(.+?) ═+$")


class WebError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ---------------------------------------------------------------- một lượt nhận diện


@dataclass
class Job:
    job_id: str
    image: str
    started_at: str
    status: str = "running"  # running | done | error
    lines: list[str] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    chunks: dict[str, int] = field(
        default_factory=lambda: {"done": 0, "total": 0, "peer": 0, "server": 0}
    )
    result: dict[str, Any] | None = None
    error: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, line: str) -> None:
        """Printer truyền cho Agent: lưu dòng và rút ra bước hiện tại, tiến độ chunk."""
        with self.lock:
            self.lines.append(line)
            section = SECTION_LINE.match(line.strip())
            if section:
                step, title = section.groups()
                self.sections.append({"step": int(step) if step else None, "title": title})
            chunk = CHUNK_LINE.search(line)
            if chunk:
                done, total, source = chunk.groups()
                self.chunks["done"] = int(done)
                self.chunks["total"] = int(total)
                self.chunks[source] += 1

    def finish(self, result: dict[str, Any]) -> None:
        with self.lock:
            self.status, self.result = "done", result

    def fail(self, message: str) -> None:
        with self.lock:
            self.status, self.error = "error", message

    def view(self, since: int = 0) -> dict[str, Any]:
        with self.lock:
            return {
                "job_id": self.job_id,
                "image": self.image,
                "started_at": self.started_at,
                "status": self.status,
                "line_count": len(self.lines),
                "lines": self.lines[since:],
                "sections": list(self.sections),
                "chunks": dict(self.chunks),
                "result": self.result,
                "error": self.error,
            }


def result_view(result: AgentResult) -> dict[str, Any]:
    variant = result.variant
    download = result.download
    inference = result.inference
    return {
        "variant": {
            key: variant.get(key)
            for key in ("id", "precision", "size_bytes", "chunk_count", "top1_accuracy")
        },
        "source": result.source,
        "offline": result.offline,
        "request_id": result.request_id or None,
        "device_class": result.device_class or None,
        "download_ms": round(result.download_ms, 1) if download else None,
        "download": (
            {
                "chunks_from_server": download.chunks_from_server,
                "chunks_from_peer": download.chunks_from_peer,
                "chunks_reused": download.chunks_reused,
                "bad_chunks": len(download.bad_chunks),
            }
            if download
            else None
        ),
        "top5": [asdict(p) for p in inference.top5],
        "load_ms": round(inference.load_ms, 1),
        "latency_ms_median": round(inference.latency_ms_median, 2),
        "runs": inference.runs,
        "peak_ram_mb": inference.peak_ram_mb,
        "decision": result.feedback or None,
        "history": result.history,
    }


# ---------------------------------------------------------------- trạng thái client


@dataclass
class WebConfig:
    server: str
    device_id: str
    cache_dir: Path
    peer_port: int = 0
    labels: Path = DEFAULT_LABELS
    runs: int = 10


class ClientWebApp:
    """Logic của giao diện client, tách khỏi HTTP để test được."""

    def __init__(self, config: WebConfig, agent_cls: type[Agent] = Agent) -> None:
        self.config = config
        self.server = normalize_server_url(config.server)
        self.agent_cls = agent_cls
        self.queue = FeedbackQueue(config.cache_dir)
        self.device = collect_device_info(config.cache_dir)
        self.cache = ModelCache(config.cache_dir)
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._job_images: dict[str, Path] = {}  # để "Tải bản đề xuất" chạy lại đúng ảnh cũ
        self._current: Job | None = None
        self._counter = 0
        self._peer_agent: Agent | None = None  # agent đang giữ peer service sau lượt cuối
        self._reachable: tuple[float, bool] = (0.0, False)
        self._last_beat = 0.0

    # ------------------------------------------------------------ đọc trạng thái

    def server_online(self) -> bool:
        at, value = self._reachable
        if time.monotonic() - at > REACHABLE_TTL_S:
            value = server_reachable(self.server, timeout_s=1.0)
            self._reachable = (time.monotonic(), value)
        return value

    def _heartbeat(self) -> None:
        """Gọi theo nhịp hỏi /api/state của trang: luồng nền, không làm chậm giao diện."""
        if time.monotonic() - self._last_beat < HEARTBEAT_EVERY_S:
            return
        self._last_beat = time.monotonic()
        threading.Thread(
            target=ApiClient(self.server).heartbeat,
            args=(self.config.device_id,),
            name="heartbeat",
            daemon=True,
        ).start()

    def models(self) -> list[dict[str, Any]]:
        active = load_active(self.config.cache_dir)
        rows = []
        for entry in self.cache.entries():
            meta = read_meta(self.cache.meta_path(entry.variant_id)) or {}
            variant = meta.get("variant") or {}
            rows.append(
                {
                    "variant_id": entry.variant_id,
                    "status": entry.status,
                    "reason": entry.reason,
                    "active": entry.variant_id == active,
                    "size_bytes": variant.get("size_bytes"),
                    "precision": variant.get("precision"),
                    "top1_accuracy": variant.get("top1_accuracy"),
                }
            )
        return rows

    def state(self, browser_host: str | None = None) -> dict[str, Any]:
        holder = self._peer_agent
        service = holder.peer_service if holder else None
        current = self._current
        online = self.server_online()
        if online:
            self._heartbeat()
        return {
            "device_id": self.config.device_id,
            "server": self.server,
            "server_online": online,
            "dashboard": dashboard_info(self.server, browser_host),
            "device": {
                "platform": self.device.platform,
                "cpu_arch": self.device.cpu_arch,
                "ram_mb": self.device.ram_mb,
                "runtime": self.device.runtime,
            },
            "models": self.models(),
            "pending_feedback": len(self.queue.pending()),
            "last_detection": load_last_detection(self.config.cache_dir),
            "job": {"job_id": current.job_id, "status": current.status} if current else None,
            "sharing": {
                "enabled": bool(self.config.peer_port),
                "port": self.config.peer_port or None,
                "running": service is not None,
                "shared": service.shared_ids() if service else [],
                "served": service.served if service else 0,
            },
        }

    def variants(self) -> dict[str, Any]:
        """Model cho ô "Model muốn dùng": Registry của server (nếu kết nối được) + kho trên máy.

        Protocol 1.0 không có endpoint liệt kê variant nên đọc `/admin-api/variants` chỉ-đọc
        của server. Mất server thì chỉ còn model `complete` trên máy (detect offline được).
        """
        local = {m["variant_id"]: m for m in self.models()}
        rows: dict[str, dict[str, Any]] = {}
        error = None
        online = self.server_online()
        if online:
            try:
                resp = requests.get(f"{self.server}/admin-api/variants", timeout=3)
                resp.raise_for_status()
                remote = resp.json().get("variants", [])
            except (requests.RequestException, ValueError, AttributeError) as exc:
                remote = []
                error = f"Không đọc được danh sách model của server: {type(exc).__name__}"
            for v in remote:
                if v.get("distributable"):
                    rows[v["variant_id"]] = {
                        "variant_id": v["variant_id"],
                        "precision": v.get("precision"),
                        "size_bytes": v.get("size_bytes"),
                        "top1_accuracy": v.get("top1_accuracy"),
                        "on_server": True,
                    }
        for variant_id, m in local.items():
            if variant_id not in rows and (online or m["status"] == "complete"):
                rows[variant_id] = {
                    "variant_id": variant_id,
                    "precision": m.get("precision"),
                    "size_bytes": m.get("size_bytes"),
                    "top1_accuracy": m.get("top1_accuracy"),
                    "on_server": False,
                }
        for variant_id, row in rows.items():
            row["local_status"] = local.get(variant_id, {}).get("status")
        ordered = sorted(rows.values(), key=lambda r: (r["size_bytes"] or 0, r["variant_id"]))
        return {"server_online": online, "variants": ordered, "error": error}

    def samples(self) -> list[str]:
        if not SAMPLES_DIR.is_dir():
            return []
        return sorted(p.name for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in IMAGE_TYPES)

    def job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise WebError(404, "NOT_FOUND", f"Không có lượt nhận diện {job_id}.")
        return job

    # ------------------------------------------------------------ nhận diện

    def save_upload(self, filename: str, data: bytes) -> Path:
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_TYPES:
            raise WebError(400, "BAD_REQUEST", "Chỉ nhận ảnh .jpg, .jpeg, .png, .bmp hoặc .webp.")
        if not data:
            raise WebError(400, "BAD_REQUEST", "File ảnh rỗng.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise WebError(413, "BAD_REQUEST", "Ảnh lớn hơn 15 MB.")
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem)[:40] or "anh"
        folder = self.config.cache_dir / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{stem}{suffix}"
        path.write_bytes(data)
        return path

    def job_image(self, job_id: str) -> Path:
        path = self._job_images.get(job_id)
        if path is None or not path.is_file():
            raise WebError(404, "NOT_FOUND", "Không còn ảnh của lượt trước, hãy chọn lại ảnh.")
        return path

    def sample_path(self, name: str) -> Path:
        path = SAMPLES_DIR / Path(name).name
        if path.suffix.lower() not in IMAGE_TYPES or not path.is_file():
            raise WebError(404, "NOT_FOUND", f"Không có ảnh mẫu {name}.")
        return path

    def start_detect(self, image: Path, options: dict[str, str]) -> Job:
        bandwidth = _positive(options.get("bandwidth"), "Băng thông")
        deadline = _positive(options.get("deadline"), "Hạn tải") or 60.0
        preferred = (options.get("preferred") or "").strip() or None
        if preferred and not _VARIANT_ID.match(preferred):
            raise WebError(400, "BAD_REQUEST", f"Variant '{preferred}' không hợp lệ.")
        with self._lock:
            if self._current is not None and self._current.status == "running":
                raise WebError(409, "BUSY", "Đang có một lượt nhận diện chạy, hãy chờ nó xong.")
            self._counter += 1
            job = Job(
                job_id=f"j-{self._counter:04d}",
                image=image.name,
                started_at=datetime.now().isoformat(timespec="seconds"),
            )
            self._jobs[job.job_id] = job
            self._job_images[job.job_id] = image
            self._current = job
        config = AgentConfig(
            server=self.server,
            device_id=self.config.device_id,
            image=image,
            deadline_s=deadline,
            bandwidth_kbps=bandwidth,
            preferred_variant=preferred,
            peer_port=self.config.peer_port,
            cache_dir=self.config.cache_dir,
            runs=self.config.runs,
            labels=self.config.labels,
            # chia sẻ P2P cần server ghi nhận máy này làm peer nên luôn hỏi server
            serve=bool(self.config.peer_port),
            refresh=options.get("refresh") == "1",
        )
        threading.Thread(target=self._run, args=(job, config), daemon=True).start()
        return job

    def _run(self, job: Job, config: AgentConfig) -> None:
        # peer service của lượt trước giữ cổng; dừng nó để lượt này mở lại
        self._release_peer()
        agent = self.agent_cls(config, out=job.add)
        keep = False
        try:
            result = agent.run()
            job.finish(result_view(result))
            keep = agent.peer_service is not None
        except AgentError as exc:
            job.fail(str(exc))
        except ApiError as exc:
            if exc.code == "CONNECTION":
                job.fail(f"Mất kết nối server {self.server} giữa chừng: {exc.message}")
            else:
                job.fail(f"Server báo lỗi {exc.code}: {exc.message}")
        except DownloadError as exc:
            job.fail(f"Lỗi tải model: {exc}")
        except Exception as exc:  # ảnh hỏng, lỗi ONNX, lỗi đĩa: báo gọn trên giao diện
            job.fail(f"{type(exc).__name__}: {exc}")
        finally:
            if keep:
                self._peer_agent = agent
            else:
                agent.close()

    def _release_peer(self) -> None:
        holder, self._peer_agent = self._peer_agent, None
        if holder is not None:
            holder.close()

    def close(self) -> None:
        self._release_peer()

    # ------------------------------------------------------------ đánh giá

    def submit_feedback(self, data: dict[str, Any]) -> dict[str, Any]:
        """Giống client.feedback_cli nhưng không hỏi: ghi pending trước, rồi gửi nếu online."""
        cache_dir = self.config.cache_dir
        last = load_last_detection(cache_dir)
        variant_id = data.get("variant_id") or (last["variant_id"] if last else None)
        try:
            if not variant_id:
                raise FeedbackInputError("Chưa có lần nhận diện nào để đánh giá.")
            if not _VARIANT_ID.match(str(variant_id)):
                raise FeedbackInputError(f"Variant '{variant_id}' không hợp lệ.")
            rating = parse_rating(str(data.get("rating", "")))
            if not isinstance(data.get("correct"), bool):
                raise FeedbackInputError("Chọn dự đoán đúng hay sai.")
            correct = data["correct"]
            detection = last if last and last["variant_id"] == variant_id else None
            prediction = check_text(MAX_LABEL)(
                str(data.get("prediction") or (detection or {}).get("prediction") or "")
            )
            expected = check_text(MAX_LABEL)(str(data.get("expected_label") or ""))
            comment = check_text(MAX_COMMENT)(str(data.get("comment") or ""))
        except FeedbackInputError as exc:
            raise WebError(400, "BAD_REQUEST", str(exc)) from exc
        if expected is None and correct and prediction:
            expected = prediction
        confidence = (detection or {}).get("confidence")
        payload = build_manual_feedback(
            feedback_id=new_feedback_id(),
            device_id=self.config.device_id,
            variant_id=variant_id,
            rating=rating,
            correct=correct,
            prediction=prediction,
            confidence=confidence if isinstance(confidence, (int, float)) else None,
            expected_label=expected,
            comment=comment,
            request_id=(detection or {}).get("request_id"),
            detected_at=(detection or {}).get("detected_at"),
            created_at=utc_now(),
        )
        self.queue.add(payload)
        answer: dict[str, Any] = {"feedback_id": payload["feedback_id"], "status": STATUS_PENDING}
        self._reachable = (0.0, False)  # kiểm tra lại ngay, không dùng kết quả cũ
        if not self.server_online():
            answer["message"] = "Server chưa kết nối được: đã lưu chờ, sẽ gửi bù khi có mạng."
            if comment:
                answer["comment_status"] = "failed"
            return answer
        report = self.queue.sync(
            ApiClient(self.server), out=lambda _line: None, only={payload["feedback_id"]}
        )
        if report.sent:
            response = report.sent[0]["response"]
            answer.update(status="sent", evaluation=response.get("evaluation"))
        elif report.rejected:
            answer.update(status="rejected", message=report.rejected[0].get("error"))
        else:
            answer["message"] = f"Chưa gửi được ({report.error}); đã lưu chờ để gửi bù."
        if comment:
            answer["comment_status"] = self._send_comment(payload, comment)
        return answer

    def _send_comment(self, payload: dict[str, Any], comment: str) -> str:
        """Nhận xét chữ đi thêm vào /api/user-feedback (server chấm điểm cảm xúc).

        Không gửi feedback_id: server chống trùng theo feedback_id, sẽ bỏ nhận xét này.
        Lỗi không ảnh hưởng đánh giá chính đã gửi ở trên. Trả "sent" hoặc "failed".
        """
        body = {
            "device_id": self.config.device_id,
            "distributed_variant": payload["variant_id"],
            "feedback_text": comment,
            "request_id": payload.get("request_id"),
        }
        try:
            resp = requests.post(
                f"{self.server}/api/user-feedback", json=body, timeout=COMMENT_TIMEOUT_S
            )
        except requests.RequestException:
            return "failed"
        return "sent" if resp.status_code == 200 else "failed"

    def sync_feedback(self) -> dict[str, Any]:
        if not self.server_online():
            raise WebError(503, "OFFLINE", f"Không kết nối được server {self.server}.")
        report = self.queue.sync(ApiClient(self.server), out=lambda _line: None)
        return {
            "sent": len(report.sent),
            "rejected": len(report.rejected),
            "remaining": report.remaining,
            "error": report.error,
        }

    def feedback_history(self) -> list[dict[str, Any]]:
        records = self.queue.records()[-HISTORY_LIMIT:]
        return [
            {
                "feedback_id": r.get("feedback_id"),
                "variant_id": r.get("variant_id"),
                "rating": r.get("rating"),
                "correct": r.get("correct"),
                "prediction": r.get("prediction"),
                "status": r.get("status"),
                "created_at": r.get("created_at"),
                "verdict": (r.get("evaluation") or {}).get("verdict"),
                "error": r.get("error"),
            }
            for r in reversed(records)
        ]


def _positive(value: str | None, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except ValueError:
        raise WebError(400, "BAD_REQUEST", f"{label} phải là số.") from None
    if number <= 0:
        raise WebError(400, "BAD_REQUEST", f"{label} phải lớn hơn 0.")
    return number


# ---------------------------------------------------------------- HTTP


def make_handler(app: ClientWebApp) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PBL4ClientUI"

        def log_message(self, *args: Any) -> None:  # không in mỗi lượt poll ra console
            pass

        # -------------------------------------------------------- trả lời

        def _json(self, data: Any, status: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, exc: WebError) -> None:
            self._json({"error": {"code": exc.code, "message": exc.message}}, exc.status)

        def _file(self, root: Path, relative: str) -> None:
            path = (root / unquote(relative)).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise WebError(404, "NOT_FOUND", "Không có file này.")
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")  # không chạy JS cũ sau khi cập nhật
            self.end_headers()
            self.wfile.write(data)

        def _body(self, limit: int) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise WebError(413, "BAD_REQUEST", "Nội dung quá lớn.")
            return self.rfile.read(length) if length else b""

        def _json_body(self) -> dict[str, Any]:
            try:
                data = json.loads(self._body(64 * 1024).decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                raise WebError(400, "BAD_REQUEST", "Nội dung không phải JSON hợp lệ.") from None
            if not isinstance(data, dict):
                raise WebError(400, "BAD_REQUEST", "Nội dung phải là một đối tượng JSON.")
            return data

        # -------------------------------------------------------- route

        def do_GET(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            path = url.path
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if path in ("/", "/index.html"):
                    self._file(WEB_DIR, "index.html")
                elif path.startswith("/shared/") or path.startswith("/static/"):
                    root = WEB_DIR / "shared" if path.startswith("/shared/") else WEB_DIR
                    self._file(root, path.split("/", 2)[2])
                elif path.startswith("/samples/"):
                    self._file(SAMPLES_DIR, path[len("/samples/") :])
                elif path == "/api/state":
                    self._json(app.state(self.headers.get("Host")))
                elif path == "/api/samples":
                    self._json({"samples": app.samples()})
                elif path == "/api/variants":
                    self._json(app.variants())
                elif path == "/api/feedback":
                    self._json({"records": app.feedback_history()})
                elif path.startswith("/api/jobs/"):
                    since = int(query.get("since", "0") or 0)
                    self._json(app.job(path.rsplit("/", 1)[1]).view(max(0, since)))
                else:
                    raise WebError(404, "NOT_FOUND", f"Không có đường dẫn {path}.")
            except WebError as exc:
                self._error(exc)
            except ValueError:
                self._error(WebError(400, "BAD_REQUEST", "Tham số không hợp lệ."))

        def do_POST(self) -> None:  # noqa: N802
            url = urlsplit(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/api/detect":
                    if query.get("again"):  # chạy lại ảnh của lượt trước (vd. tải bản đề xuất)
                        image = app.job_image(query["again"])
                    elif query.get("sample"):
                        image = app.sample_path(query["sample"])
                    else:
                        data = self._body(MAX_UPLOAD_BYTES)
                        image = app.save_upload(query.get("name", "anh.jpg"), data)
                    job = app.start_detect(image, query)
                    self._json({"job_id": job.job_id}, 202)
                elif url.path == "/api/feedback":
                    self._json(app.submit_feedback(self._json_body()))
                elif url.path == "/api/sync":
                    self._json(app.sync_feedback())
                else:
                    raise WebError(404, "NOT_FOUND", f"Không có đường dẫn {url.path}.")
            except WebError as exc:
                self._error(exc)

    return Handler


def serve(app: ClientWebApp, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    httpd.daemon_threads = True
    return httpd


# ---------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> tuple[WebConfig, argparse.Namespace]:
    parser = argparse.ArgumentParser(
        prog="python -m client.web_ui",
        description="Giao diện web của client PBL4: nhận diện ảnh, tải model, gửi đánh giá.",
    )
    parser.add_argument("--server", default="http://localhost:8000", help="URL server")
    parser.add_argument("--device-id", default=None, help="mặc định: tên máy đã lọc ký tự")
    parser.add_argument("--cache-dir", type=Path, default=None, help="mặc định data/client/<id>")
    parser.add_argument("--host", default="127.0.0.1", help="0.0.0.0 để mở từ máy khác")
    parser.add_argument("--port", type=int, default=8080, help="cổng giao diện web")
    parser.add_argument("--peer-port", type=int, default=0, help="0 = không chia sẻ P2P")
    parser.add_argument("--runs", type=int, default=10, help="số lần chạy để lấy trung vị")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="file synset")
    parser.add_argument("--open", action="store_true", help="mở trình duyệt sau khi chạy")
    args = parser.parse_args(argv)
    if args.peer_port and not 1024 <= args.peer_port <= 65535:
        parser.error("--peer-port phải là 0 hoặc trong khoảng 1024-65535")
    if args.runs < 1:
        parser.error("--runs phải >= 1")
    device_id = sanitize_device_id(args.device_id) if args.device_id else default_device_id()
    if args.device_id and device_id != args.device_id:
        parser.error("--device-id chỉ gồm A-Z a-z 0-9 _ -, tối đa 32 ký tự")
    config = WebConfig(
        server=args.server,
        device_id=device_id,
        cache_dir=args.cache_dir or (REPO_ROOT / "data" / "client" / device_id),
        peer_port=args.peer_port,
        labels=args.labels,
        runs=args.runs,
    )
    return config, args


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    config, args = parse_args(argv)
    app = ClientWebApp(config)
    try:
        httpd = serve(app, args.host, args.port)
    except OSError as exc:
        print(f"[LỖI] Không mở được cổng {args.port}: {exc}. Dùng --port khác.")
        return 1
    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}"
    print(f"Giao diện client {config.device_id} đang chạy: {url}")
    print(f"Server: {app.server} · kho model: {config.cache_dir}")
    print("Bấm Ctrl+C để dừng.")
    if args.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Đã dừng giao diện client.")
    finally:
        httpd.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
