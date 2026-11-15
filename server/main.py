"""Ứng dụng FastAPI: tạo app, đăng ký route và exception handler.

Chạy:
    uvicorn server.main:app --host 0.0.0.0 --port 8000

File này chỉ lắp ráp app. Route nằm trong `server/routes/` và `server/admin_api.py`; logic
nghiệp vụ nằm trong `server/service.py`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from server.admin_api import router as admin_router
from server.config import MODEL_CARDS_DIR, PROTOCOL_VERSION, Settings
from server.http_utils import JSONResponse, add_exception_handlers
from server.logging_setup import setup_logging
from server.ratelimit import TokenBucket
from server.registry import Registry
from server.routes.protocol import add_protocol_routes
from server.routes.web import CLIENT_PATH, add_web_routes
from server.selector import ModelSelector, default_selector
from server.service import DistributionService

logger = logging.getLogger("pbl4.server")


def _init_database(settings: Settings) -> None:
    """Khởi tạo CSDL SQL (theo settings.data_dir, để test không chạm data/ thật) và đồng bộ
    Registry ban đầu. CSDL lỗi không chặn phân phối: luồng Protocol chỉ dựa vào Registry."""
    try:
        from server.db import (
            get_session_factory,
            init_db,
            init_engine,
            sync_data_to_db,
            sync_registry_to_db,
        )

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        init_engine(settings.database_url)
        init_db()
        with get_session_factory()() as db_session:
            sync_registry_to_db(db_session, settings.registry_path, MODEL_CARDS_DIR)
            sync_data_to_db(db_session, settings.data_dir)
    except Exception as exc:
        logger.warning("Không khởi tạo được CSDL (%s): %s", settings.database_url, exc)


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
        default_response_class=JSONResponse,
    )
    app.state.settings = settings
    app.state.service = service
    app.state.bucket = bucket

    _init_database(settings)

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
    logger.info(
        "Giao diện client (xin model phát hiện vật thể, chạy trên camera): "
        "http://<IP máy server>:<cổng>%s/ — chạy `python -m server.serve` để có link HTTPS",
        CLIENT_PATH,
    )

    # Thứ tự dưới đây là một phần của hành vi: route /api, router /admin-api, rồi các mount
    # giao diện (xem server/routes/web.py).
    add_exception_handlers(app)
    add_protocol_routes(app, service, settings, bucket)
    app.include_router(admin_router)
    add_web_routes(app, settings)

    return app


app = create_app()
