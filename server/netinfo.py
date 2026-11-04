"""Địa chỉ IPv4 của máy server trên mạng LAN (in link cho điện thoại, mã QR trên dashboard)."""

from __future__ import annotations

import ipaddress
import socket


def lan_ipv4() -> list[str]:
    """IPv4 của máy trên mạng LAN, card mạng ra Internet đứng đầu; bỏ loopback và 169.254.x.x."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # UDP không gửi gói nào, chỉ để hệ điều hành chọn card mạng
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        found += [
            info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        ]
    except OSError:
        pass
    result: list[str] = []
    for ip in found:
        addr = ipaddress.ip_address(ip)
        if not (addr.is_loopback or addr.is_link_local) and ip not in result:
            result.append(ip)
    return result
