"""HTTP của giao diện web client: file tĩnh và API cục bộ `/api/*`.

Web server dùng thư viện chuẩn (như `client/peer.py`) để chạy được trên Termux mà không cần
cài thêm gói.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from client.webui_app import MAX_UPLOAD_BYTES, SAMPLES_DIR, ClientWebApp, WebError

WEB_DIR = Path(__file__).resolve().parent / "web"


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
