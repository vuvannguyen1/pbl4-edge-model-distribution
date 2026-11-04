"""Bật server và in link cho điện thoại: người dùng chỉ cần mở link, không chạy lệnh nào.

Chạy trên máy server, tại thư mục gốc repo:
    python -m server.serve                  # HTTP 8000 + HTTPS 8443
    python -m server.serve --no-https       # chỉ HTTP
    python -m server.serve --port 8000 --https-port 8443

| Link                          | Dùng được                                              |
|-------------------------------|--------------------------------------------------------|
| http://<IP LAN>:8000/app/     | Xin model, chụp ảnh từng tấm (input capture)            |
| https://<IP LAN>:8443/app/    | Như trên + camera trực tiếp, WASM đa luồng (trình duyệt |
|                               | chỉ cho mở camera liên tục trên HTTPS)                  |

Cổng HTTPS dùng chứng chỉ tự ký, tạo một lần trong data/certs/ (cần `pip install cryptography`
hoặc có openssl, Git for Windows có sẵn). Cổng này chỉ giải mã TLS rồi chuyển nguyên byte sang cổng
HTTP trong cùng tiến trình: vẫn một server, một trạng thái, dashboard thấy mọi thiết bị.
Client Python (client.agent, client.web_ui) vẫn dùng cổng HTTP như cũ, vì Protocol 1.0 yêu cầu URL
miếng là http://. Lệnh `uvicorn server.main:app ...` cũ vẫn chạy được, chỉ thiếu cổng HTTPS.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import os
import shutil
import ssl
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.config import REPO_ROOT
from server.netinfo import lan_ipv4

DEFAULT_PORT = 8000
DEFAULT_HTTPS_PORT = 8443
PIPE_BLOCK_BYTES = 64 * 1024
CERT_DAYS = 825
CERT_DIR = REPO_ROOT / "data" / "certs"
# Git for Windows có openssl nhưng không đưa vào PATH của cmd
OPENSSL_CANDIDATES = (
    Path(r"C:\Program Files\Git\mingw64\bin\openssl.exe"),
    Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
)


# ---------------------------------------------------------------- chứng chỉ tự ký


def _cert_with_cryptography(cert: Path, key: Path, ips: list[str]) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "PBL4 server")])
    now = datetime.now(timezone.utc)
    san = x509.SubjectAlternativeName(
        [x509.DNSName("localhost"), *(x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips)]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=CERT_DAYS))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    key.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))


def find_openssl() -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    return next((str(p) for p in OPENSSL_CANDIDATES if p.is_file()), None)


def _cert_with_openssl(cert: Path, key: Path, ips: list[str]) -> None:
    exe = find_openssl()
    if exe is None:
        raise RuntimeError(
            "chưa có thư viện tạo chứng chỉ: chạy `pip install cryptography` "
            "(hoặc cài Git for Windows để có openssl)"
        )
    san = ",".join(["DNS:localhost", *(f"IP:{ip}" for ip in ips)])
    subprocess.run(
        [
            exe,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            str(CERT_DAYS),
            "-subj",
            "/CN=PBL4 server",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-addext",
            f"subjectAltName={san}",
        ],
        check=True,
        capture_output=True,
    )


def ensure_cert(cert_dir: Path, ips: list[str]) -> tuple[Path, Path]:
    """Chứng chỉ tự ký cho các IP LAN; tạo lại khi máy có IP mới (đổi Wi-Fi/hotspot)."""
    cert, key, meta = cert_dir / "cert.pem", cert_dir / "key.pem", cert_dir / "cert.json"
    try:
        covered = set(json.loads(meta.read_text(encoding="utf-8"))["ips"])
    except (OSError, ValueError, KeyError, TypeError):
        covered = set()
    wanted = set(ips) | {"127.0.0.1"}
    if cert.is_file() and key.is_file() and wanted <= covered:
        return cert, key
    all_ips = sorted(wanted | covered)
    cert_dir.mkdir(parents=True, exist_ok=True)
    (cert_dir / ".gitignore").write_text("*\n", encoding="utf-8")  # khoá riêng không lên Git
    try:
        _cert_with_cryptography(cert, key, all_ips)
    except ImportError:
        _cert_with_openssl(cert, key, all_ips)
    meta.write_text(json.dumps({"ips": all_ips}), encoding="utf-8")
    return cert, key


# ---------------------------------------------------------------- cổng HTTPS


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(PIPE_BLOCK_BYTES):
            writer.write(data)
            await writer.drain()
    except OSError:  # gồm ssl.SSLError, ConnectionResetError
        pass
    finally:
        writer.close()


def tls_forwarder(target_host: str, target_port: int):
    """Handler cho asyncio.start_server(ssl=...): chuyển nguyên byte sang cổng HTTP.

    Server thấy request đến từ 127.0.0.1 với Host là cổng HTTPS; trang /app luôn tải miếng cùng
    origin nên không phụ thuộc URL server ghi trong plan.
    """

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(target_host, target_port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

    return handle


# ---------------------------------------------------------------- chạy


def banner(ip: str | None, port: int, https_port: int | None) -> str:
    """Một link cho client, một link dashboard, theo IP của mạng đang dùng."""
    host = ip or "<IP máy server>"
    client = f"https://{host}:{https_port}/app/" if https_port else f"http://{host}:{port}/app/"
    lines = [
        "",
        "=" * 72,
        f" Link cho client (điện thoại, laptop cùng mạng): {client}",
        f" Link dashboard của server:                      http://{host}:{port}/",
    ]
    if https_port:
        lines.append(" Lần đầu mở link client, trình duyệt cảnh báo: Nâng cao -> Tiếp tục.")
    ports = f"{port},{https_port}" if https_port else str(port)
    lines += [
        " Sai mạng (máy có nhiều card mạng): python -m server.serve --ip <IP đúng>",
        " Máy khác không mở được link: mở tường lửa (PowerShell quyền Administrator)",
        f'   New-NetFirewallRule -DisplayName "PBL4 server" -Direction Inbound -Protocol TCP '
        f"-LocalPort {ports} -Action Allow -Profile Any",
        "=" * 72,
        "",
    ]
    return "\n".join(lines)


def _preload_phobert() -> None:
    """Nạp sẵn PhoBERT (vài giây) để người gửi đánh giá đầu tiên không phải chờ."""
    log = logging.getLogger("pbl4.server")
    try:
        from selector.feedback_classifier import classify_user_feedback

        engine = classify_user_feedback("ok")["engine"]
    except Exception as exc:  # không chặn server: đánh giá vẫn chấm được bằng từ khoá
        log.warning("Không nạp được PhoBERT: %s", exc)
        return
    if engine == "neural":
        log.info("Đã nạp PhoBERT để chấm bình luận")
    else:
        log.warning("Thiếu PhoBERT, chấm bình luận theo từ khoá: pip install -e .[phobert]")


def _ignore_connection_reset(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Trên Windows, trình duyệt đóng trang làm asyncio in Traceback ConnectionResetError vô hại."""
    if not isinstance(context.get("exception"), ConnectionResetError):
        loop.default_exception_handler(context)


async def _serve(server, host: str, https_port: int | None, ssl_ctx, ip: str | None) -> None:
    asyncio.get_running_loop().set_exception_handler(_ignore_connection_reset)
    forwarder = None
    if ssl_ctx is not None:
        target = "127.0.0.1" if host in ("", "0.0.0.0") else host
        forwarder = await asyncio.start_server(
            tls_forwarder(target, server.config.port), host or None, https_port, ssl=ssl_ctx
        )
    task = asyncio.create_task(server.serve())
    while not server.started and not task.done():
        await asyncio.sleep(0.1)
    if server.started:
        print(banner(ip, server.config.port, https_port if forwarder else None), flush=True)
        threading.Thread(target=_preload_phobert, daemon=True).start()
    try:
        await task
    finally:
        if forwarder is not None:
            forwarder.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bật server PBL4 và in link cho điện thoại.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--https-port", type=int, default=DEFAULT_HTTPS_PORT)
    parser.add_argument("--no-https", action="store_true", help="không mở cổng HTTPS")
    parser.add_argument(
        "--ip", default=None, help="IP in trên link; mặc định: IP của card mạng ra Internet"
    )
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    ips = lan_ipv4()
    if args.ip and args.ip not in ips:
        ips.append(args.ip)
    link_ip = args.ip or (ips[0] if ips else None)
    ssl_ctx = None
    if not args.no_https:
        try:
            cert, key = ensure_cert(CERT_DIR, ips)
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(cert, key)
        except (RuntimeError, OSError, subprocess.CalledProcessError, ssl.SSLError) as exc:
            print(f"[cảnh báo] Không bật được HTTPS ({exc}); chỉ chạy HTTP.", flush=True)
            ssl_ctx = None
    # server.main đọc các biến này khi uvicorn import app: trang /app qua HTTP chỉ sang bản HTTPS,
    # dashboard in link/QR đúng cổng
    os.environ["PBL4_HTTP_PORT"] = str(args.port)
    os.environ["PBL4_LINK_IP"] = link_ip or ""
    if ssl_ctx is not None:
        os.environ["PBL4_HTTPS_PORT"] = str(args.https_port)
    else:
        os.environ.pop("PBL4_HTTPS_PORT", None)

    import uvicorn

    server = uvicorn.Server(uvicorn.Config("server.main:app", host=args.host, port=args.port))
    try:
        asyncio.run(_serve(server, args.host, args.https_port, ssl_ctx, link_ip))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
