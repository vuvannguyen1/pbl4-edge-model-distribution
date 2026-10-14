"""Tải model theo plan: tối đa 4 miếng song song, kiểm SHA-256, fallback, ghi theo offset.

Quy tắc (mục 8 của protocol):
- Mỗi miếng tải theo `url` (chờ tối đa 10 s không nhận được byte nào), tự tính
  SHA-256 và so với `plan[].sha256`; khớp mới ghi vào file `.part` tại `offset`.
- Sai hash hoặc lỗi mạng: ghi vào `bad_chunks` (index, source_url), thử lại từ
  `fallback_url` nếu có; tối đa 3 lần mỗi miếng.
- Báo `/api/have` mỗi 4 miếng; `bandwidth_kbps` = số byte nhận từ server (đếm theo
  từng khối, kể cả miếng đang tải dở) chia thời gian kể từ lần báo trước; `null`
  nếu khoảng đó chỉ nhận từ peer.
- Đủ miếng: kiểm SHA-256 toàn file; khớp mới đổi tên `.part` thành tên thật và
  báo `complete: true`.
- Miếng đã có sẵn đúng hash trong `.part` hoặc file cũ được dùng lại (tải tiếp).

Kho cục bộ: mỗi variant một thư mục `<cache_dir>/<variant_id>/` (file model và
`variant.json`), cộng `<cache_dir>/active.json` ghi variant đang dùng. Model nằm
lại trên đĩa để `client.detect` chạy offline ở các lần sau.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from client.api import ApiClient, build_have, new_session

MAX_PARALLEL = 4
CHUNK_TIMEOUT_S = 10.0
MAX_ATTEMPTS = 3
REPORT_EVERY = 4
READ_BLOCK = 64 * 1024
BYTES_PER_MB = 1024 * 1024
META_FILE = "variant.json"  # khối variant lưu cạnh file model đã kiểm hash
ACTIVE_FILE = "active.json"  # variant đang dùng, ở gốc cache_dir

Logger = Callable[[str], None]


class DownloadError(Exception):
    """Không tải đủ miếng hoặc hash toàn file sai."""


class SwitchRequested(Exception):
    """Server trả `switch` trong /api/have."""

    def __init__(self, new_variant_id: str, reason: str | None) -> None:
        super().__init__(f"server yêu cầu đổi sang {new_variant_id}")
        self.new_variant_id = new_variant_id
        self.reason = reason


@dataclass
class ChunkOutcome:
    index: int
    ok: bool
    source: str | None = None  # "peer" | "server" của lần tải thành công
    url: str | None = None
    size: int = 0
    elapsed_s: float = 0.0
    bad: list[tuple[int, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class DownloadResult:
    path: Path
    chunks_from_peer: int = 0
    chunks_from_server: int = 0
    chunks_reused: int = 0
    bad_chunks: list[tuple[int, str]] = field(default_factory=list)
    sources: dict[int, str] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(BYTES_PER_MB):
            digest.update(block)
    return digest.hexdigest()


def variant_paths(cache_dir: Path, variant: dict[str, Any]) -> tuple[Path, Path]:
    """(file hoàn chỉnh, file .part) trong cache_dir/<variant_id>/."""
    folder = cache_dir / variant["id"]
    final = folder / variant["file"]
    return final, final.with_name(final.name + ".part")


def describe_error(exc: Exception) -> str:
    """Mô tả lỗi mạng ngắn gọn cho console."""
    if isinstance(exc, requests.ConnectionError):
        return "không kết nối được (peer tắt, firewall hoặc AP isolation)"
    if isinstance(exc, requests.Timeout):
        return "quá 10 s không nhận được dữ liệu"
    text = str(exc)
    return f"{type(exc).__name__}: {text[:120]}{'…' if len(text) > 120 else ''}"


def save_variant_meta(cache_dir: Path, variant: dict[str, Any]) -> None:
    """Ghi khối variant cạnh file model, chỉ gọi sau khi SHA-256 toàn file khớp."""
    final, _ = variant_paths(cache_dir, variant)
    (final.parent / META_FILE).write_text(json.dumps(variant, ensure_ascii=False), encoding="utf-8")


def verified_variants(cache_dir: Path) -> dict[str, tuple[dict[str, Any], Path]]:
    """Kho cục bộ: variant đủ file và SHA-256 toàn file khớp -> (khối variant, file).

    Khóa theo `variant_id` (thư mục riêng cho mỗi variant), không suy từ tên file.
    """
    found: dict[str, tuple[dict[str, Any], Path]] = {}
    if not cache_dir.is_dir():
        return found
    for meta_path in sorted(cache_dir.glob(f"*/{META_FILE}")):
        try:
            variant = json.loads(meta_path.read_text(encoding="utf-8"))
            final, _ = variant_paths(cache_dir, variant)
            if meta_path.parent.name != variant["id"]:
                continue
            if (
                final.is_file()
                and final.stat().st_size == variant["size_bytes"]
                and sha256_file(final) == variant["sha256"]
            ):
                found[variant["id"]] = (variant, final)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def save_active(cache_dir: Path, variant_id: str) -> None:
    """Ghi variant đang dùng, để lần chạy offline sau chọn đúng model này."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / ACTIVE_FILE).write_text(json.dumps({"variant_id": variant_id}), encoding="utf-8")


def load_active(cache_dir: Path) -> str | None:
    """variant_id trong active.json, None nếu chưa có hoặc file hỏng."""
    try:
        data = json.loads((cache_dir / ACTIVE_FILE).read_text(encoding="utf-8"))
        variant_id = data["variant_id"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return variant_id if isinstance(variant_id, str) else None


def choose_local(
    found: dict[str, tuple[dict[str, Any], Path]],
    requested: str | None = None,
    active: str | None = None,
) -> tuple[dict[str, Any], Path, str]:
    """Chọn model trong kho cục bộ (kết quả của `verified_variants`).

    Thứ tự: `requested` (bắt buộc phải có) -> `active` -> top-1 cao nhất, bằng nhau
    thì file nhỏ hơn. Trả (khối variant, file, lý do chọn). Không có model nào phù
    hợp thì ném LookupError.
    """
    if requested is not None:
        if requested not in found:
            have = ", ".join(sorted(found)) or "không có"
            raise LookupError(f"Cache không có {requested} hợp lệ (đang có: {have}).")
        variant, path = found[requested]
        return variant, path, "theo yêu cầu"
    if not found:
        raise LookupError("Cache chưa có model nào hợp lệ.")
    if active is not None and active in found:
        variant, path = found[active]
        return variant, path, "model đang dùng, ghi trong active.json"
    variant, path = min(
        found.values(), key=lambda vp: (-vp[0]["top1_accuracy"], vp[0]["size_bytes"], vp[0]["id"])
    )
    return variant, path, "top-1 cao nhất trong cache"


def attempts_for(item: dict[str, Any], max_attempts: int = MAX_ATTEMPTS) -> list[tuple[str, str]]:
    """Danh sách (url, nguồn) theo thứ tự thử: nguồn chính trước, rồi fallback_url."""
    first = (item["url"], item["source"])
    fallback = item.get("fallback_url")
    retry = (fallback, "server") if fallback else first
    return [first] + [retry] * (max_attempts - 1)


class ChunkDownloader:
    def __init__(
        self,
        api: ApiClient,
        device_id: str,
        *,
        max_workers: int = MAX_PARALLEL,
        timeout_s: float = CHUNK_TIMEOUT_S,
        max_attempts: int = MAX_ATTEMPTS,
        report_every: int = REPORT_EVERY,
        log: Logger = print,
    ) -> None:
        self.api = api
        self.device_id = device_id
        self.max_workers = max(1, min(MAX_PARALLEL, max_workers))
        self.timeout_s = timeout_s
        self.max_attempts = max_attempts
        self.report_every = report_every
        self.log = log
        self._local = threading.local()
        # byte nhận từ server, cộng dồn theo từng khối khi đang tải (để đo băng thông thật)
        self._server_bytes = 0
        self._bytes_lock = threading.Lock()

    # ------------------------------------------------------------ tải một miếng

    def _session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._local.session = new_session()
        return session

    def _take_server_bytes(self) -> int:
        with self._bytes_lock:
            taken, self._server_bytes = self._server_bytes, 0
            return taken

    def _fetch(self, url: str, expected_size: int, from_server: bool = False) -> bytes:
        with self._session().get(url, stream=True, timeout=self.timeout_s) as resp:
            if resp.status_code != 200:
                raise OSError(f"HTTP {resp.status_code}")
            parts: list[bytes] = []
            received = 0
            for block in resp.iter_content(READ_BLOCK):
                received += len(block)
                if from_server:
                    with self._bytes_lock:
                        self._server_bytes += len(block)
                if received > expected_size:
                    raise OSError(f"nhận nhiều hơn {expected_size} byte")
                parts.append(block)
            return b"".join(parts)

    def _download_chunk(
        self, item: dict[str, Any], writer: Callable[[int, bytes], None]
    ) -> ChunkOutcome:
        outcome = ChunkOutcome(index=item["index"], ok=False)
        for url, source in attempts_for(item, self.max_attempts):
            start = time.perf_counter()
            try:
                data = self._fetch(url, item["size_bytes"], from_server=source == "server")
            except (requests.RequestException, OSError) as exc:
                outcome.bad.append((item["index"], url))
                outcome.errors.append(f"{source} {url}: {describe_error(exc)}")
                continue
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != item["size_bytes"] or digest != item["sha256"]:
                outcome.bad.append((item["index"], url))
                outcome.errors.append(
                    f"{source} {url}: sai sha256 ({len(data)} byte, {digest[:12]}…)"
                )
                continue
            writer(item["offset"], data)
            outcome.ok = True
            outcome.source = source
            outcome.url = url
            outcome.size = len(data)
            outcome.elapsed_s = time.perf_counter() - start
            return outcome
        return outcome

    # ------------------------------------------------------------ dùng lại miếng có sẵn

    def _reusable(self, path: Path, size_bytes: int, plan: Sequence[dict[str, Any]]) -> set[int]:
        if not path.is_file() or path.stat().st_size != size_bytes:
            return set()
        ok: set[int] = set()
        with path.open("rb") as f:
            for item in plan:
                f.seek(item["offset"])
                if hashlib.sha256(f.read(item["size_bytes"])).hexdigest() == item["sha256"]:
                    ok.add(item["index"])
        return ok

    # ------------------------------------------------------------ toàn bộ

    def download(
        self,
        request_id: str,
        variant: dict[str, Any],
        plan: Sequence[dict[str, Any]],
        cache_dir: Path,
    ) -> DownloadResult:
        final, part = variant_paths(cache_dir, variant)
        final.parent.mkdir(parents=True, exist_ok=True)
        size_bytes = variant["size_bytes"]
        result = DownloadResult(path=final)

        if final.is_file():
            if final.stat().st_size == size_bytes and sha256_file(final) == variant["sha256"]:
                reused = {item["index"] for item in plan}
            else:
                final.unlink()
                reused = set()
        else:
            reused = self._reusable(part, size_bytes, plan)
        if final.is_file():
            target = final
        else:
            target = part
            if not reused:
                with part.open("wb") as f:
                    f.truncate(size_bytes)  # tạo trước đủ kích thước, ghi theo offset
        if reused:
            self.log(f"  Dùng lại {len(reused)} miếng đã có đúng hash trên đĩa")
        result.chunks_reused = len(reused)

        todo = [item for item in plan if item["index"] not in reused]
        pending_chunks: list[int] = sorted(reused)
        pending_bad: list[tuple[int, str]] = []
        failures: list[ChunkOutcome] = []
        self._take_server_bytes()
        window = {"start": time.perf_counter()}
        lock = threading.Lock()
        done_count = len(reused)
        total = len(plan)

        def report(complete: bool) -> None:
            elapsed = time.perf_counter() - window["start"]
            server_bytes = self._take_server_bytes()
            bandwidth = server_bytes * 8 / 1000 / elapsed if server_bytes and elapsed > 0 else None
            payload = build_have(
                device_id=self.device_id,
                request_id=request_id,
                variant_id=variant["id"],
                chunks=pending_chunks,
                complete=complete,
                bandwidth_kbps=bandwidth,
                bad_chunks=pending_bad,
            )
            response = self.api.have(payload)
            bw_text = f"{bandwidth:,.0f} kbps" if bandwidth else "null"
            self.log(
                f"  → /api/have: {len(payload['chunks'])} miếng mới, complete={complete}, "
                f"băng thông server {bw_text}, bad_chunks={len(payload['bad_chunks'])} "
                f"← {response.get('action')}"
            )
            pending_chunks.clear()
            pending_bad.clear()
            window["start"] = time.perf_counter()
            if response.get("action") == "switch" and response.get("new_variant_id"):
                raise SwitchRequested(response["new_variant_id"], response.get("reason"))

        handle = None if target is final else part.open("r+b")

        def write_at(offset: int, data: bytes) -> None:
            assert handle is not None
            with lock:
                handle.seek(offset)
                handle.write(data)

        executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="chunk")
        try:
            futures: set[Future[ChunkOutcome]] = {
                executor.submit(self._download_chunk, item, write_at) for item in todo
            }
            while futures:
                finished, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in finished:
                    outcome = future.result()
                    for err in outcome.errors:
                        self.log(f"  [cảnh báo] miếng {outcome.index}: {err}")
                    pending_bad.extend(outcome.bad)
                    result.bad_chunks.extend(outcome.bad)
                    if not outcome.ok:
                        failures.append(outcome)
                        self.log(f"  [LỖI] miếng {outcome.index}: hết {self.max_attempts} lần thử")
                        continue
                    done_count += 1
                    result.sources[outcome.index] = outcome.source or "server"
                    if outcome.source == "peer":
                        result.chunks_from_peer += 1
                    else:
                        result.chunks_from_server += 1
                    pending_chunks.append(outcome.index)
                    arrow = "peer  " if outcome.source == "peer" else "server"
                    self.log(
                        f"  [{done_count:>3}/{total}] miếng {outcome.index:>3} ← {arrow} "
                        f"{outcome.size / BYTES_PER_MB:5.2f} MB  {outcome.elapsed_s:6.2f} s  "
                        "sha256 OK"
                    )
                    # lần báo cuối gộp với complete=true, không báo hai lần liền nhau
                    if len(pending_chunks) >= self.report_every and futures:
                        report(complete=False)
        except SwitchRequested:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            if handle is not None:
                handle.close()

        if failures:
            if pending_chunks or pending_bad:
                report(complete=False)
            failed = ", ".join(str(f.index) for f in failures)
            raise DownloadError(f"Không tải được miếng {failed} sau {self.max_attempts} lần thử")

        if target is part:
            digest = sha256_file(part)
            if digest != variant["sha256"]:
                if pending_chunks or pending_bad:
                    report(complete=False)
                part.unlink(missing_ok=True)
                raise DownloadError(
                    f"SHA-256 toàn file sai: {digest[:16]}… khác {variant['sha256'][:16]}…"
                )
            os.replace(part, final)
        self.log(f"  SHA-256 toàn file khớp {variant['sha256'][:16]}… → {final.name}")
        report(complete=True)
        return result
