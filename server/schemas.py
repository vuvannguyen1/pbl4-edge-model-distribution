"""Model Pydantic khớp từng $defs trong docs/protocol.schema.json.

- Mọi model đặt `extra="forbid"` vì schema dùng `additionalProperties: false`.
- Model của tin nhắn client gửi lên đặt thêm `strict=True` để không tự ép kiểu
  (ví dụ "6144" -> 6144), đúng tinh thần của schema.
- Ràng buộc có điều kiện (if/then/else) và `uniqueItems` được viết bằng validator.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

DeviceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
VariantId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,64}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
HttpUrl = Annotated[str, StringConstraints(pattern=r"^http://")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
Ratio = Annotated[float, Field(ge=0, le=1)]
NullableRatio = Annotated[float | None, Field(ge=0, le=1)]
NullableNonNeg = Annotated[float | None, Field(ge=0)]
PositiveOrNull = Annotated[float | None, Field(gt=0)]

Platform = Literal["android", "android_browser", "ios_browser", "windows", "linux"]
DEVICE_CLASS_PATTERN = r"^[a-z_]+_(arm64|arm32|x64)_(lt4gb|4-8gb|gt8gb)$"


class _Incoming(BaseModel):
    """Tin nhắn client gửi: cấm trường thừa, không ép kiểu."""

    model_config = ConfigDict(extra="forbid", strict=True)


class _Outgoing(BaseModel):
    """Tin nhắn server trả: cấm trường thừa."""

    model_config = ConfigDict(extra="forbid")


def _ensure_unique(values: list) -> list:
    if len(values) != len(set(values)):
        raise ValueError("các phần tử phải khác nhau (uniqueItems)")
    return values


# ---------------------------------------------------------------- Đăng ký


class RegisterRequest(_Incoming):
    protocol_version: Literal["1.0"]
    device_id: DeviceId
    platform: Platform
    cpu_arch: NonEmptyStr
    ram_mb: int = Field(ge=1)
    runtime: NonEmptyStr
    peer_port: int | None = Field(ge=1024, le=65535)


class RegisterResponse(_Outgoing):
    protocol_version: Literal["1.0"] = "1.0"
    device_id: DeviceId
    device_class: Annotated[str, StringConstraints(pattern=DEVICE_CLASS_PATTERN)]
    chunk_size: Literal[1048576] = 1048576
    peer_enabled: bool


# ---------------------------------------------------------------- Xin model


class ModelRequest(_Incoming):
    device_id: DeviceId
    task: Literal["image_classification"]
    bandwidth_kbps: PositiveOrNull
    deadline_s: float = Field(gt=0)
    free_storage_mb: NullableNonNeg
    preferred_variant: VariantId | None
    have_variants: list[VariantId]

    _unique_have = field_validator("have_variants")(_ensure_unique)


class Normalize(_Outgoing):
    mean: list[float] = Field(min_length=3, max_length=3)
    std: list[Annotated[float, Field(gt=0)]] = Field(min_length=3, max_length=3)


class Variant(_Outgoing):
    id: VariantId
    file: Annotated[str, StringConstraints(pattern=r"\.onnx$")]
    base_model: NonEmptyStr
    level: int = Field(ge=0)
    precision: Literal["fp32", "fp16", "int8"]
    size_bytes: int = Field(ge=1)
    sha256: Sha256
    chunk_size: Literal[1048576]
    chunk_count: int = Field(ge=1)
    top1_accuracy: Ratio
    input_shape: list[Annotated[int, Field(ge=1)]] = Field(min_length=1)
    input_dtype: Literal["float", "uint8"]
    normalize: Normalize


class PlanItem(_Outgoing):
    index: int = Field(ge=0)
    offset: int = Field(ge=0)
    size_bytes: int = Field(ge=1, le=1048576)
    sha256: Sha256
    source: Literal["server", "peer"]
    url: HttpUrl
    fallback_url: HttpUrl | None

    @model_validator(mode="after")
    def _fallback_matches_source(self) -> PlanItem:
        if self.source == "peer" and self.fallback_url is None:
            raise ValueError("miếng từ peer phải có fallback_url")
        if self.source == "server" and self.fallback_url is not None:
            raise ValueError("miếng từ server phải có fallback_url = null")
        return self


class ModelResponse(_Outgoing):
    request_id: NonEmptyStr
    variant: Variant
    reason: str
    estimated_download_s: NullableNonNeg
    plan: list[PlanItem]


# ---------------------------------------------------------------- Tiến độ


class BadChunk(_Incoming):
    index: int = Field(ge=0)
    source_url: HttpUrl


class HaveRequest(_Incoming):
    device_id: DeviceId
    request_id: NonEmptyStr
    variant_id: VariantId
    chunks: list[Annotated[int, Field(ge=0)]]
    complete: bool
    bandwidth_kbps: PositiveOrNull
    bad_chunks: list[BadChunk]

    _unique_chunks = field_validator("chunks")(_ensure_unique)


class HaveResponse(_Outgoing):
    ok: Literal[True] = True
    action: Literal["continue", "switch"]
    new_variant_id: VariantId | None
    reason: str | None

    @model_validator(mode="after")
    def _switch_has_target(self) -> HaveResponse:
        if self.action == "switch" and (self.new_variant_id is None or self.reason is None):
            raise ValueError("switch phải có new_variant_id và reason")
        if self.action == "continue" and self.new_variant_id is not None:
            raise ValueError("continue phải có new_variant_id = null")
        return self


# ---------------------------------------------------------------- Phản hồi


class FeedbackRequest(_Incoming):
    device_id: DeviceId
    request_id: NonEmptyStr
    variant_id: VariantId
    download_ms: float = Field(ge=0)
    chunks_from_peer: int = Field(ge=0)
    chunks_from_server: int = Field(ge=0)
    load_ms: float = Field(ge=0)
    latency_ms_median: float = Field(ge=0)
    runs: int = Field(ge=1)
    peak_ram_mb: NullableNonNeg
    top1_label: str | None
    top1_confidence: NullableRatio


class FeedbackResponse(_Outgoing):
    action: Literal["keep", "switch"]
    new_variant_id: VariantId | None
    applies_to_class: NonEmptyStr
    reason: str

    @model_validator(mode="after")
    def _switch_has_target(self) -> FeedbackResponse:
        if self.action == "switch" and self.new_variant_id is None:
            raise ValueError("switch phải có new_variant_id")
        if self.action == "keep" and self.new_variant_id is not None:
            raise ValueError("keep phải có new_variant_id = null")
        return self


# ---------------------------------------------------------------- Lỗi

ErrorCode = Literal[
    "BAD_REQUEST",
    "UNKNOWN_DEVICE",
    "UNKNOWN_VARIANT",
    "CHUNK_OUT_OF_RANGE",
    "VERSION_MISMATCH",
    "NO_VARIANT_FITS",
    "INTERNAL",
]


class ErrorDetail(_Outgoing):
    code: ErrorCode
    message: str


class ErrorResponse(_Outgoing):
    error: ErrorDetail
