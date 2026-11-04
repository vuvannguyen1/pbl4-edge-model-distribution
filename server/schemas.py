"""Model Pydantic khớp từng $defs trong docs/protocol.schema.json.

- Mọi model đặt `extra="forbid"` vì schema dùng `additionalProperties: false`.
- Model của tin nhắn client gửi lên đặt thêm `strict=True` để không tự ép kiểu
  (ví dụ "6144" -> 6144), đúng tinh thần của schema.
- Ràng buộc có điều kiện (if/then/else) và `uniqueItems` được viết bằng validator.
- Phần mở rộng cho phát hiện vật thể (capabilities, mode, khối detection, peer WebRTC,
  /api/announce, /api/signal) đều là trường tuỳ chọn: client 1.0 cũ gửi/nhận như trước.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

DeviceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]
VariantId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,64}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
HttpUrl = Annotated[str, StringConstraints(pattern=r"^http://")]
# Miếng từ peer: HTTP (client Python) hoặc WebRTC DataChannel (trình duyệt, webrtc://<device_id>/...)
ChunkUrl = Annotated[str, StringConstraints(pattern=r"^(http|webrtc)://")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
Ratio = Annotated[float, Field(ge=0, le=1)]
NullableRatio = Annotated[float | None, Field(ge=0, le=1)]
NullableNonNeg = Annotated[float | None, Field(ge=0)]
PositiveOrNull = Annotated[float | None, Field(gt=0)]

Platform = Literal["android", "android_browser", "ios_browser", "web_desktop", "windows", "linux"]
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


class Capabilities(_Incoming):
    """Năng lực thiết bị (tuỳ chọn). Trình duyệt gửi để Selector ước lượng tốc độ suy luận."""

    threads: int = Field(default=1, ge=1, le=64)  # số luồng onnxruntime dùng được
    webgpu: bool = False
    camera: bool = False
    p2p: Literal["webrtc", "http", "none"] = "none"  # cách thiết bị chia sẻ miếng cho máy khác


# Tên người dùng đặt cho máy (tiếng Việt được), khác `device_id` chỉ gồm ký tự không dấu
DisplayName = Annotated[str, StringConstraints(pattern=r"^[^\x00-\x1f\x7f]{1,32}$")]


class RegisterRequest(_Incoming):
    protocol_version: Literal["1.0"]
    device_id: DeviceId
    platform: Platform
    cpu_arch: NonEmptyStr
    ram_mb: int = Field(ge=1)
    runtime: NonEmptyStr
    peer_port: int | None = Field(ge=1024, le=65535)
    capabilities: Capabilities | None = None
    display_name: DisplayName | None = None


class RegisterResponse(_Outgoing):
    protocol_version: Literal["1.0"] = "1.0"
    device_id: DeviceId
    device_class: Annotated[str, StringConstraints(pattern=DEVICE_CLASS_PATTERN)]
    chunk_size: Literal[1048576] = 1048576
    peer_enabled: bool


# ---------------------------------------------------------------- Xin model


class ModelRequest(_Incoming):
    device_id: DeviceId
    task: Literal["image_classification", "object_detection"]
    bandwidth_kbps: PositiveOrNull
    deadline_s: float = Field(gt=0)
    free_storage_mb: NullableNonNeg
    preferred_variant: VariantId | None
    have_variants: list[VariantId]
    # live: camera liên tục, ưu tiên tốc độ; photo: ảnh chụp, cho phép model nặng hơn.
    # null (client 1.0): không lọc theo năng lực tính toán.
    mode: Literal["live", "photo"] | None = None
    target_fps: float | None = Field(default=None, gt=0, le=120)

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
    # Mở rộng cho phát hiện vật thể (null với model phân loại ảnh)
    task: str | None = None
    map50_95: NullableRatio = None
    gflops: NullableNonNeg = None
    runtimes: list[str] | None = None
    detection: dict[str, Any] | None = None  # contract.detection của Model Card


class PlanItem(_Outgoing):
    index: int = Field(ge=0)
    offset: int = Field(ge=0)
    size_bytes: int = Field(ge=1, le=1048576)
    sha256: Sha256
    source: Literal["server", "peer"]
    url: ChunkUrl
    fallback_url: HttpUrl | None

    @model_validator(mode="after")
    def _fallback_matches_source(self) -> PlanItem:
        if self.source == "peer" and self.fallback_url is None:
            raise ValueError("miếng từ peer phải có fallback_url")
        if self.source == "server" and self.fallback_url is not None:
            raise ValueError("miếng từ server phải có fallback_url = null")
        return self


class PeerInfo(_Outgoing):
    device_id: DeviceId
    name: str


class ModelResponse(_Outgoing):
    request_id: NonEmptyStr
    variant: Variant
    reason: str
    estimated_download_s: NullableNonNeg
    plan: list[PlanItem]
    peers: list[PeerInfo] = Field(default_factory=list)  # máy gửi miếng, để báo nguồn nhận model


# ---------------------------------------------------------------- Tiến độ


class BadChunk(_Incoming):
    index: int = Field(ge=0)
    source_url: ChunkUrl


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
    # Phát hiện vật thể (tuỳ chọn): FPS đo khi chạy camera, số vật thể ở lượt đo
    fps: NullableNonNeg = None
    detections: int | None = Field(default=None, ge=0)


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


# ---------------------------------------------------------------- Đánh giá thủ công

FeedbackId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200)]


def _check_timestamp(value: str | None) -> str | None:
    if value is not None:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("phải là thời điểm ISO 8601") from exc
    return value


class ManualFeedbackRequest(_Incoming):
    """Đánh giá người dùng nhập bằng `client.feedback_cli`; phân biệt nhờ `feedback_id`."""

    feedback_id: FeedbackId
    device_id: DeviceId
    variant_id: VariantId
    rating: int = Field(ge=1, le=5)
    correct: bool | None = None  # giao diện /app không còn hỏi đúng/sai
    prediction: ShortText | None
    confidence: NullableRatio
    expected_label: ShortText | None
    comment: Annotated[str, StringConstraints(min_length=1, max_length=1000)] | None
    request_id: ShortText | None
    detected_at: str | None
    created_at: NonEmptyStr

    _timestamps = field_validator("detected_at", "created_at")(_check_timestamp)


class ManualEvaluation(_Outgoing):
    verdict: Literal["positive", "neutral", "negative"]
    needs_review: bool
    variant_feedback_count: int = Field(ge=1)
    variant_correct_rate: NullableRatio  # null khi chưa lượt nào trả lời đúng/sai
    variant_avg_rating: float = Field(ge=1, le=5)
    variant_avg_score: float | None = Field(default=None, ge=1, le=5)
    reason: str
    # Điểm tổng hợp lượt này (sao × 0,2 + PhoBERT × 0,8); null ở bản ghi cũ
    final_score: float | None = Field(default=None, ge=1, le=5)
    comment_score: int | None = Field(default=None, ge=1, le=5)  # PhoBERT, rời rạc 1..5
    comment_engine: str | None = None  # "neural" (PhoBERT) hoặc "lexicon" (từ khoá dự phòng)


class ManualFeedbackResponse(_Outgoing):
    ok: Literal[True] = True
    feedback_id: FeedbackId
    status: Literal["accepted", "duplicate"]
    received_at: NonEmptyStr
    evaluation: ManualEvaluation


# ---------------------------------------------------------------- Khai báo model có sẵn


class HeldVariant(_Incoming):
    variant_id: VariantId
    sha256: Sha256


class AnnounceRequest(_Incoming):
    """Thiết bị báo đã có đủ model (vd. trình duyệt mở lại trang, model lấy từ kho trên máy) để
    server ghép P2P mà không phải lập một lượt tải. SHA-256 phải khớp Registry."""

    device_id: DeviceId
    variants: list[HeldVariant] = Field(max_length=32)


class AnnounceResponse(_Outgoing):
    ok: Literal[True] = True
    accepted: list[VariantId]
    rejected: list[VariantId]


# ---------------------------------------------------------------- Signaling WebRTC

SessionId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class SignalMessage(_Incoming):
    """Tin nhắn trao đổi SDP giữa hai trình duyệt; server chỉ chuyển tiếp, không đọc `data`."""

    from_device: DeviceId
    to_device: DeviceId
    type: Literal["offer", "answer", "candidate", "bye"]
    session: SessionId
    data: dict[str, Any] | None = None


# ---------------------------------------------------------------- Lỗi

ErrorCode = Literal[
    "BAD_REQUEST",
    "UNKNOWN_DEVICE",
    "UNKNOWN_VARIANT",
    "CHUNK_OUT_OF_RANGE",
    "VERSION_MISMATCH",
    "NO_VARIANT_FITS",
    "NAME_TAKEN",
    "INTERNAL",
]


class ErrorDetail(_Outgoing):
    code: ErrorCode
    message: str


class ErrorResponse(_Outgoing):
    error: ErrorDetail
