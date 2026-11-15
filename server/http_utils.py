"""Tiện ích HTTP dùng chung cho các route: phản hồi JSON, đọc và kiểm thân request,
file tĩnh không cache, header cách ly cho trang /app, và exception handler theo Protocol 1.0.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse as _BaseJSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from server.config import Settings
from server.errors import ProtocolError, bad_request, error_body

logger = logging.getLogger("pbl4.server")

# Trang /app được "cross-origin isolated" để onnxruntime-web dùng WASM đa luồng (SharedArrayBuffer).
# Mọi tài nguyên của trang đều cùng origin nên require-corp không chặn gì.
ISOLATION_HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
}

M = TypeVar("M", bound=BaseModel)


class NoCacheStaticFiles(StaticFiles):
    """Giao diện luôn hỏi lại server (ETag, trả 304 nếu không đổi): trình duyệt không chạy JS
    cũ sau khi cập nhật code. Chỉ có Last-Modified thì Chrome tự cache theo phỏng đoán."""

    def file_response(self, *args: Any, **kwargs: Any) -> Any:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


class IsolationHeaders:
    """Thêm ISOLATION_HEADERS cho response dưới `prefix`.

    Middleware ASGI thuần (không dùng @app.middleware("http")): BaseHTTPMiddleware bọc kênh
    nhận của request nên long-poll /api/signal không còn thấy trình duyệt đóng kết nối.
    """

    def __init__(self, app: Any, prefix: str) -> None:
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).update(ISOLATION_HEADERS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class JSONResponse(_BaseJSONResponse):
    """JSON kèm `charset=utf-8`: PowerShell 5.1 và vài client không tự đoán UTF-8, đọc sai dấu."""

    media_type = "application/json; charset=utf-8"


def json_model(model: BaseModel, status_code: int = 200) -> JSONResponse:
    return JSONResponse(model.model_dump(mode="json"), status_code=status_code)


def error_response(code: str, message: str, status_code: int, headers: Any = None) -> JSONResponse:
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


def add_exception_handlers(app: FastAPI) -> None:
    """Mọi lỗi trả theo khung lỗi của Protocol 1.0 (`{"error": {"code", "message"}}`)."""

    @app.exception_handler(ProtocolError)
    async def _protocol_error(_: Request, exc: ProtocolError) -> JSONResponse:
        return error_response(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Protocol dành 422 cho NO_VARIANT_FITS, nên lỗi validate luôn là 400
        return error_response("BAD_REQUEST", describe_validation_errors(list(exc.errors())), 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            message = f"Không có endpoint {request.url.path}."
        elif exc.status_code == 405:
            message = f"Phương thức {request.method} không dùng được cho {request.url.path}."
        else:
            message = str(exc.detail)
        code = "INTERNAL" if exc.status_code >= 500 else "BAD_REQUEST"
        return error_response(code, message, exc.status_code, getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Lỗi không mong đợi khi xử lý %s %s", request.method, request.url.path)
        return error_response("INTERNAL", "Lỗi phía server, xem log server.", 500)
