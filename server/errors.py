"""Lỗi giao thức và error envelope theo mục 6 của docs/protocol.md."""

from __future__ import annotations

from typing import Any

# code -> HTTP status, đúng bảng mục 6 của protocol
ERROR_STATUS: dict[str, int] = {
    "BAD_REQUEST": 400,
    "UNKNOWN_DEVICE": 404,
    "UNKNOWN_VARIANT": 404,
    "CHUNK_OUT_OF_RANGE": 404,
    "VERSION_MISMATCH": 409,
    "NO_VARIANT_FITS": 422,
    "INTERNAL": 500,
}


class ProtocolError(Exception):
    """Lỗi trả cho client dưới dạng {"error": {"code", "message"}}.

    `message` là câu tiếng Việt cho người đọc; không được chứa đường dẫn hệ
    thống, `storage_path` hay stack trace.
    """

    def __init__(self, code: str, message: str, http_status: int | None = None) -> None:
        if code not in ERROR_STATUS:
            raise ValueError(f"mã lỗi không có trong protocol: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status if http_status is not None else ERROR_STATUS[code]

    def to_body(self) -> dict[str, Any]:
        return error_body(self.code, self.message)


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def bad_request(message: str) -> ProtocolError:
    return ProtocolError("BAD_REQUEST", message)


def unknown_device(device_id: str) -> ProtocolError:
    return ProtocolError(
        "UNKNOWN_DEVICE", f"Thiết bị '{device_id}' chưa đăng ký, hãy gọi /api/register trước."
    )


def unknown_variant(variant_id: str) -> ProtocolError:
    return ProtocolError(
        "UNKNOWN_VARIANT",
        f"Phiên bản '{variant_id}' không có trong Registry hoặc chưa ở trạng thái ready.",
    )
