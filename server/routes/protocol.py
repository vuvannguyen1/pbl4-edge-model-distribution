"""Route Protocol 1.0 (`/api/*`): chỉ đọc/ghi HTTP, logic nằm trong `server/service.py`."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from server.config import PROTOCOL_VERSION, Settings
from server.errors import ProtocolError, bad_request
from server.http_utils import (
    JSONResponse,
    base_url_for,
    client_ip,
    json_model,
    parse_model,
    read_json,
)
from server.ratelimit import TokenBucket
from server.schemas import (
    AnnounceRequest,
    FeedbackRequest,
    HaveRequest,
    ManualFeedbackRequest,
    ModelRequest,
    RegisterRequest,
    SignalMessage,
)
from server.service import DistributionService
from server.streaming import stream_file_range, stream_random

PROBE_MIN_KB = 16
PROBE_MAX_KB = 1024
PROBE_DEFAULT_KB = 256
OCTET_STREAM = "application/octet-stream"
# Dashboard gửi header này khi "xin model thử": server ghi request là source="web"
SOURCE_HEADER = "X-PBL4-Source"
WEB_SOURCE = "web-ui"


def add_protocol_routes(
    app: FastAPI, service: DistributionService, settings: Settings, bucket: TokenBucket
) -> None:
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
        return json_model(service.register(req, client_ip(request)))

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
        source = "web" if request.headers.get(SOURCE_HEADER) == WEB_SOURCE else "client"
        return json_model(service.request_model(req, base_url_for(request, settings), source))

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
        return json_model(service.have(req))

    @app.post("/api/feedback")
    async def feedback(request: Request) -> JSONResponse:
        data = await read_json(request)
        # có feedback_id: đánh giá thủ công từ client.feedback_cli; không có: số đo của agent
        if isinstance(data, dict) and "feedback_id" in data:
            # chạy ở luồng riêng: lần đầu nạp PhoBERT mất vài giây, không được chặn event loop
            # (đang stream miếng model và long-poll signaling)
            req = parse_model(ManualFeedbackRequest, data)
            return json_model(await run_in_threadpool(service.manual_feedback, req))
        return json_model(service.feedback(parse_model(FeedbackRequest, data)))

    @app.post("/api/announce")
    async def announce(request: Request) -> JSONResponse:
        return json_model(service.announce(parse_model(AnnounceRequest, await read_json(request))))

    # ------------------------------------------------------------ signaling WebRTC

    @app.post("/api/signal")
    async def signal_post(request: Request) -> JSONResponse:
        msg = parse_model(SignalMessage, await read_json(request))
        if service.devices.get(msg.from_device) is None:
            raise ProtocolError("UNKNOWN_DEVICE", f"Thiết bị '{msg.from_device}' chưa đăng ký.")
        delivered = service.signals.post(msg.model_dump())
        return JSONResponse({"ok": True, "delivered": delivered})

    @app.get("/api/signal/{device_id}")
    async def signal_wait(request: Request, device_id: str, wait: float = 20.0) -> JSONResponse:
        if service.devices.get(device_id) is None:
            raise ProtocolError("UNKNOWN_DEVICE", f"Thiết bị '{device_id}' chưa đăng ký.")
        messages = await service.signals.wait(device_id, wait, request.is_disconnected)
        return JSONResponse({"messages": messages})

    @app.post("/api/user-feedback")
    async def user_feedback_endpoint(request: Request) -> JSONResponse:
        from server.user_feedback_handler import record_user_feedback

        data = await read_json(request)
        if not isinstance(data, dict):
            raise bad_request("Nội dung phải là một đối tượng JSON.")
        res = record_user_feedback(data, settings.user_feedback_log)
        return JSONResponse(res)
