"""Logic của giao diện web client, tách khỏi HTTP để test được.

Mỗi lần "Nhận diện" chạy đúng `client.agent.Agent` trong một luồng nền; đánh giá đi qua
`FeedbackQueue` và các hàm kiểm tra của `client.feedback_input`.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from client.agent import Agent, AgentConfig, AgentError
from client.api import ApiClient, ApiError, build_manual_feedback, normalize_server_url
from client.device_info import collect_device_info
from client.downloader import DownloadError
from client.feedback import (
    STATUS_PENDING,
    FeedbackQueue,
    load_last_detection,
    new_feedback_id,
    utc_now,
)
from client.feedback_input import (
    MAX_COMMENT,
    MAX_LABEL,
    VARIANT_ID_PATTERN,
    FeedbackInputError,
    check_text,
    parse_rating,
)
from client.model_cache import ModelCache, load_active, read_meta
from client.monitor import server_reachable
from client.paths import DEFAULT_LABELS, IMAGE_SUFFIXES, REPO_ROOT
from client.webui_jobs import Job, result_view

SAMPLES_DIR = REPO_ROOT / "samples"
IMAGE_TYPES = IMAGE_SUFFIXES
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
REACHABLE_TTL_S = 2.0
HEARTBEAT_EVERY_S = 10.0  # trang đang mở thì báo server máy này còn chạy
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


class WebError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


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
        if preferred and not VARIANT_ID_PATTERN.match(preferred):
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
            if not VARIANT_ID_PATTERN.match(str(variant_id)):
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
        if self.server_online():
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
        else:
            answer["message"] = "Server chưa kết nối được: đã lưu chờ, sẽ gửi bù khi có mạng."
        if comment:
            # nhận xét nằm trong chính đánh giá (trường comment); server chấm nó khi nhận
            # đánh giá, kể cả lúc gửi bù, nên số phận nhận xét đi theo đánh giá chính
            answer["comment_status"] = {"sent": "sent", "rejected": "failed"}.get(
                answer["status"], "pending"
            )
        return answer

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
