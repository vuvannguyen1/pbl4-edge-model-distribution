"""Một lượt nhận diện của giao diện web client: ghi lại các dòng `Agent` in ra và rút ra
bước hiện tại, tiến độ tải từng miếng, kết quả.

Tiến độ được đọc bằng regex trên chính các dòng `client.agent` và `client.downloader` in ra,
nên định dạng hai loại dòng đó là hợp đồng với module này.
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from client.agent import AgentResult

# Dòng agent/downloader in ra: "  [  5/14] miếng   4 ← peer ..." và "══ [5/7] Tải model ═══"
CHUNK_LINE = re.compile(r"\[\s*(\d+)/(\d+)\] miếng\s+\d+ ← (peer|server)")
SECTION_LINE = re.compile(r"^══ (?:\[(\d)/7\] )?(.+?) ═+$")


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
        "detections": [asdict(d) for d in inference.detections],
        "load_ms": round(inference.load_ms, 1),
        "latency_ms_median": round(inference.latency_ms_median, 2),
        "runs": inference.runs,
        "peak_ram_mb": inference.peak_ram_mb,
        "decision": result.feedback or None,
        "history": result.history,
    }
