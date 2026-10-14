"""Ứng dụng FastAPI: tạo app, đăng ký route và exception handler.

Chạy:
    uvicorn server.main:app --host 0.0.0.0 --port 8000

File này chỉ điều phối HTTP; logic nghiệp vụ nằm trong `server/service.py`.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.config import PROTOCOL_VERSION, Settings
from server.errors import ProtocolError, bad_request, error_body
from server.ratelimit import TokenBucket
from server.registry import Registry
from server.schemas import FeedbackRequest, HaveRequest, ModelRequest, RegisterRequest
from server.selector import ModelSelector, default_selector
from server.service import DistributionService

logger = logging.getLogger("pbl4.server")

# Khối nhỏ để khi giới hạn tốc độ thấp và 4 kết nối song song, mỗi kết nối vẫn nhận
# byte đều đặn (client chờ tối đa 10 s không có byte nào).
STREAM_BLOCK_BYTES = 16 * 1024
PROBE_MIN_KB = 16
PROBE_MAX_KB = 1024
PROBE_DEFAULT_KB = 256
OCTET_STREAM = "application/octet-stream"

M = TypeVar("M", bound=BaseModel)


# ---------------------------------------------------------------- logging


class DailyFileHandler(logging.Handler):
    """Ghi log vào <logs_dir>/server-YYYYMMDD.log, tự đổi file khi sang ngày mới."""

    def __init__(self, logs_dir: Path) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self._day = ""
        self._handler: logging.FileHandler | None = None

    def emit(self, record: logging.LogRecord) -> None:
        day = datetime.now().strftime("%Y%m%d")
        if day != self._day or self._handler is None:
            if self._handler is not None:
                self._handler.close()
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            self._handler = logging.FileHandler(
                self.logs_dir / f"server-{day}.log", encoding="utf-8"
            )
            self._handler.setFormatter(self.formatter)
            self._day = day
        self._handler.emit(record)

    def close(self) -> None:
        if self._handler is not None:
            self._handler.close()
        super().close()


def setup_logging(logs_dir: Path) -> None:
    """Log ra console và file theo ngày. Gọi lại nhiều lần không bị nhân đôi handler."""
    if os.name == "nt" and hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass
    root = logging.getLogger("pbl4")
    for handler in list(root.handlers):
        if getattr(handler, "_pbl4", False):
            root.removeHandler(handler)
            handler.close()
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    file_handler = DailyFileHandler(logs_dir)
    for handler in (console, file_handler):
        handler.setFormatter(formatter)
        handler._pbl4 = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False


# ---------------------------------------------------------------- helper HTTP


def _json(model: BaseModel, status_code: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status_code)


def _error_response(code: str, message: str, status_code: int, headers: Any = None) -> JSONResponse:
    return JSONResponse(error_body(code, message), status_code=status_code, headers=headers)


_PYDANTIC_MESSAGES = {
    "missing": "thiếu trường bắt buộc",
    "extra_forbidden": "trường thừa, không có trong protocol 1.0",
}


def describe_validation_errors(errors: list[Any]) -> str:
    """Tóm tắt lỗi validate cho client, không lộ thông tin nội bộ."""
    parts: list[str] = []
    for err in errors[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body",)) or "<gốc>"
        msg = _PYDANTIC_MESSAGES.get(err.get("type", ""), err.get("msg", "không hợp lệ"))
        parts.append(f"'{loc}': {msg}")
    more = f" (và {len(errors) - 5} lỗi khác)" if len(errors) > 5 else ""
    return "Dữ liệu không hợp lệ: " + "; ".join(parts) + more + "."


async def read_json(request: Request) -> Any:
    body = await request.body()
    if not body:
        raise bad_request("Thiếu nội dung JSON.")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise bad_request("Nội dung không phải JSON UTF-8 hợp lệ.") from exc


def parse_model(model_cls: type[M], data: Any) -> M:
    if not isinstance(data, dict):
        raise bad_request("Nội dung phải là một đối tượng JSON.")
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise bad_request(describe_validation_errors(exc.errors())) from exc


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def base_url_for(request: Request, settings: Settings) -> str:
    if settings.public_base_url:
        return settings.public_base_url
    return str(request.base_url).rstrip("/")


async def stream_file_range(
    path: Path, offset: int, size: int, bucket: TokenBucket
) -> AsyncIterator[bytes]:
    """Đọc đúng `size` byte tại `offset` theo từng khối nhỏ; không nạp cả file."""
    with path.open("rb") as f:
        f.seek(offset)
        remaining = size
        while remaining > 0:
            n = min(STREAM_BLOCK_BYTES, remaining)
            await bucket.acquire(n)
            data = f.read(n)
            if not data:
                logger.error("File %s ngắn hơn dự kiến khi stream", path.name)
                return
            remaining -= len(data)
            yield data


async def stream_random(size: int, bucket: TokenBucket) -> AsyncIterator[bytes]:
    remaining = size
    while remaining > 0:
        n = min(STREAM_BLOCK_BYTES, remaining)
        await bucket.acquire(n)
        remaining -= n
        yield os.urandom(n)


# ---------------------------------------------------------------- app


def create_app(settings: Settings | None = None, selector: ModelSelector | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    setup_logging(settings.logs_dir)
    registry = Registry(settings.registry_path, settings.model_store)
    service = DistributionService(
        settings=settings, registry=registry, selector=selector or default_selector()
    )
    bucket = TokenBucket(settings.rate_limit_kbps)

    app = FastAPI(
        title="PBL4 Edge Model Distribution",
        version=PROTOCOL_VERSION,
        description="Server phân phối model ONNX theo docs/protocol.md (Protocol 1.0).",
    )
    app.state.settings = settings
    app.state.service = service
    app.state.bucket = bucket

    ready = registry.ready_variants()
    logger.info(
        "Server sẵn sàng: protocol %s, %d variant ready (%s), selector %s, giới hạn tốc độ %s, "
        "peer %s, SLO latency %s ms, dữ liệu tại %s",
        PROTOCOL_VERSION,
        len(ready),
        ", ".join(v.variant_id for v in ready) or "không có",
        service.selector.version,
        f"{settings.rate_limit_kbps:g} kbps" if settings.rate_limit_kbps else "không giới hạn",
        "bật" if settings.peer_enabled else "tắt",
        f"{settings.latency_slo_ms:g}",
        settings.data_dir,
    )

    # ------------------------------------------------------------ lỗi

    @app.exception_handler(ProtocolError)
    async def _protocol_error(_: Request, exc: ProtocolError) -> JSONResponse:
        return _error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Protocol dành 422 cho NO_VARIANT_FITS, nên lỗi validate luôn là 400
        return _error_response("BAD_REQUEST", describe_validation_errors(list(exc.errors())), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            message = f"Không có endpoint {request.url.path}."
        elif exc.status_code == 405:
            message = f"Phương thức {request.method} không dùng được cho {request.url.path}."
        else:
            message = str(exc.detail)
        code = "INTERNAL" if exc.status_code >= 500 else "BAD_REQUEST"
        return _error_response(code, message, exc.status_code, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Lỗi không mong đợi khi xử lý %s %s", request.method, request.url.path)
        return _error_response("INTERNAL", "Lỗi phía server, xem log server.", 500)

    # ------------------------------------------------------------ route

    @app.post("/api/register")
    async def register(request: Request) -> JSONResponse:
        data = await read_json(request)
        # protocol_version sai phải là 409, nên kiểm trước khi validate cả gói
        if isinstance(data, dict) and "protocol_version" in data:
            version = data["protocol_version"]
            if version != PROTOCOL_VERSION:
                raise ProtocolError(
                    "VERSION_MISMATCH",
                    f"Server dùng protocol {PROTOCOL_VERSION}, client gửi {version!r}.",
                )
        req = parse_model(RegisterRequest, data)
        return _json(service.register(req, client_ip(request)))

    @app.get("/api/probe")
    async def probe(size_kb: int = PROBE_DEFAULT_KB) -> StreamingResponse:
        if not PROBE_MIN_KB <= size_kb <= PROBE_MAX_KB:
            raise bad_request(f"size_kb phải từ {PROBE_MIN_KB} đến {PROBE_MAX_KB}, nhận {size_kb}.")
        size = size_kb * 1024
        return StreamingResponse(
            stream_random(size, bucket),
            media_type=OCTET_STREAM,
            headers={"Content-Length": str(size), "Cache-Control": "no-store"},
        )

    @app.post("/api/request")
    async def request_model(request: Request) -> JSONResponse:
        req = parse_model(ModelRequest, await read_json(request))
        return _json(service.request_model(req, base_url_for(request, settings)))

    @app.get("/api/chunks/{variant_id}/{index}")
    async def get_chunk(variant_id: str, index: int) -> StreamingResponse:
        location = service.locate_chunk(variant_id, index)
        return StreamingResponse(
            stream_file_range(location.path, location.offset, location.size_bytes, bucket),
            media_type=OCTET_STREAM,
            headers={"Content-Length": str(location.size_bytes)},
        )

    @app.post("/api/have")
    async def have(request: Request) -> JSONResponse:
        req = parse_model(HaveRequest, await read_json(request))
        return _json(service.have(req))

    @app.post("/api/feedback")
    async def feedback(request: Request) -> JSONResponse:
        req = parse_model(FeedbackRequest, await read_json(request))
        return _json(service.feedback(req))

    return app


app = create_app()
