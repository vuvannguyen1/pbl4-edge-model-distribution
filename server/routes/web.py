"""Giao diện web do server phục vụ: dashboard `/ui`, trang client `/app`, và các chuyển hướng.

Thứ tự khai báo có ý nghĩa (Starlette khớp theo thứ tự): `/ui/shared` mount trước `/ui`;
`/app/links.json` và `/app` khai báo trước khi mount `/app`.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from server.config import REPO_ROOT, Settings
from server.http_utils import IsolationHeaders, JSONResponse, NoCacheStaticFiles

SERVER_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = SERVER_DIR / "static"
# CSS/JS/font/icon dùng chung với giao diện client; nằm trong client/ vì client phải
# chạy độc lập (Termux chỉ có thư mục client/)
SHARED_UI_DIR = REPO_ROOT / "client" / "web" / "shared"
# Giao diện client /app (điện thoại, laptop): xin model phát hiện vật thể, chạy trên camera
# bằng onnxruntime-web, chia sẻ model P2P qua WebRTC
WEBCLIENT_DIR = SERVER_DIR / "webclient"
CLIENT_PATH = "/app"
# Windows không phải lúc nào cũng biết .mjs/.wasm: onnxruntime-web cần đúng MIME để nạp
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("application/wasm", ".wasm")


def add_web_routes(app: FastAPI, settings: Settings) -> None:
    # ------------------------------------------------------------ dashboard web

    # /ui/shared phải mount trước /ui (Starlette khớp theo thứ tự)
    app.mount("/ui/shared", NoCacheStaticFiles(directory=SHARED_UI_DIR), name="ui-shared")
    app.mount("/ui", NoCacheStaticFiles(directory=STATIC_DIR, html=True), name="ui")

    # ------------------------------------------------------------ trang điện thoại

    # Route phải khai báo trước mount /app (Starlette khớp theo thứ tự)
    @app.get(f"{CLIENT_PATH}/links.json", include_in_schema=False)
    async def client_links() -> JSONResponse:
        return JSONResponse({"https_port": settings.https_port})

    @app.get(CLIENT_PATH, include_in_schema=False)
    async def client_root() -> RedirectResponse:
        return RedirectResponse(f"{CLIENT_PATH}/")

    # Link cũ /m/ (bản thử đầu tiên) vẫn dùng được
    @app.get("/m", include_in_schema=False)
    @app.get("/m/{rest:path}", include_in_schema=False)
    async def legacy_mobile(rest: str = "") -> RedirectResponse:
        return RedirectResponse(f"{CLIENT_PATH}/")

    app.add_middleware(IsolationHeaders, prefix=f"{CLIENT_PATH}/")
    app.mount(CLIENT_PATH, NoCacheStaticFiles(directory=WEBCLIENT_DIR, html=True), name="client")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/ui/")
