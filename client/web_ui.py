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
import sys
import webbrowser
from pathlib import Path

import requests  # noqa: F401 - test thay client.web_ui.requests.get

from client.device_info import device_id_from_arg
from client.paths import DEFAULT_LABELS, default_cache_dir
from client.webui_app import (  # noqa: F401 - giữ đường import cũ
    ClientWebApp,
    WebConfig,
    WebError,
    dashboard_info,
)
from client.webui_http import WEB_DIR, make_handler, serve  # noqa: F401
from client.webui_jobs import Job, result_view  # noqa: F401

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
    try:
        device_id = device_id_from_arg(args.device_id)
    except ValueError as exc:
        parser.error(str(exc))
    config = WebConfig(
        server=args.server,
        device_id=device_id,
        cache_dir=args.cache_dir or default_cache_dir(device_id),
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
