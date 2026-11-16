"""Tham số dòng lệnh của `python -m client.agent`.

`main()` vẫn nằm trong `client/agent.py` (file chạy bằng `python -m client.agent`).
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from client.agent_types import AgentConfig
from client.device_info import device_id_from_arg
from client.paths import DEFAULT_LABELS


def parse_args(argv: Sequence[str] | None = None) -> AgentConfig:
    parser = argparse.ArgumentParser(
        prog="python -m client.agent",
        description="Client agent PBL4: xin model theo băng thông, tải, kiểm hash, chạy ONNX.",
    )
    parser.add_argument("--server", default="http://localhost:8000", help="URL server")
    parser.add_argument("--device-id", default=None, help="mặc định: tên máy đã lọc ký tự")
    parser.add_argument(
        "--image", type=Path, default=None, help="ảnh để chạy thử; bỏ trống thì mở hộp thoại chọn"
    )
    parser.add_argument("--deadline", type=float, default=60.0, help="hạn tải (giây)")
    parser.add_argument(
        "--bandwidth", type=float, default=None, help="kbps; bỏ trống thì đo bằng /api/probe"
    )
    parser.add_argument("--preferred-variant", default=None)
    parser.add_argument("--peer-port", type=int, default=9000, help="0 = không chia sẻ")
    parser.add_argument("--cache-dir", type=Path, default=None, help="mặc định data/client/<id>")
    parser.add_argument("--runs", type=int, default=20, help="số lần chạy để lấy trung vị")
    parser.add_argument(
        "--platform",
        default=None,
        choices=["android", "android_browser", "ios_browser", "windows", "linux"],
    )
    parser.add_argument(
        "--mode",
        default="photo",
        choices=["photo", "live", "none"],
        help="photo: model chạy một ảnh trong ~2,5 s; live: ~5 khung/giây; none: không lọc tốc độ",
    )
    parser.add_argument(
        "--follow-switch", action="store_true", help="tự xin lại khi feedback trả switch (1 lần)"
    )
    parser.add_argument(
        "--serve", action="store_true", help="giữ peer service chạy đến khi bấm Ctrl+C"
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="file synset")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="hỏi server dù máy đã có model hợp lệ (mặc định dùng model có sẵn)",
    )
    args = parser.parse_args(argv)

    if args.peer_port and not 1024 <= args.peer_port <= 65535:
        parser.error("--peer-port phải là 0 hoặc trong khoảng 1024-65535")
    if args.runs < 1:
        parser.error("--runs phải >= 1")
    if args.deadline <= 0:
        parser.error("--deadline phải > 0")
    if args.bandwidth is not None and args.bandwidth <= 0:
        parser.error("--bandwidth phải > 0")
    try:
        device_id = device_id_from_arg(args.device_id)
    except ValueError as exc:
        parser.error(str(exc))
    return AgentConfig(
        server=args.server,
        device_id=device_id,
        image=args.image,
        deadline_s=args.deadline,
        bandwidth_kbps=args.bandwidth,
        preferred_variant=args.preferred_variant,
        peer_port=args.peer_port,
        cache_dir=args.cache_dir,
        runs=args.runs,
        platform=args.platform,
        follow_switch=args.follow_switch,
        serve=args.serve,
        labels=args.labels,
        refresh=args.refresh,
        mode=None if args.mode == "none" else args.mode,
    )
