"""Model Selector Version 1: bộ lọc cứng, rule-based baseline và fallback.

Luồng chọn (tong_quan_he_thong.md, mục 7.1):

    Registry (status=ready) -> apply_hard_filters() -> ModelSelector.select()

Gắn ML Selector của thành viên khác
-----------------------------------
1. Viết class kế thừa `ModelSelector`, đặt `version` (ví dụ "ml-v1") và cài
   `select(context, candidates)`. `candidates` đã qua bộ lọc cứng, nên selector
   chỉ cần xếp hạng; kết quả phải là một `variant_id` thuộc `candidates`.
2. Bọc nó trong `FallbackSelector(primary=MLSelector(...), fallback=RuleBasedSelector())`
   rồi truyền vào `create_app(selector=...)` trong `server/main.py`.
3. Nếu ML Selector ném lỗi hoặc trả `variant_id` lạ, `FallbackSelector` tự dùng
   rule-based và ghi log, nên luồng phân phối không bao giờ bị chặn.
`MLSelectorPlaceholder` bên dưới là khung mẫu, hiện chưa được bật.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field

from server.registry import RegistryVariant

logger = logging.getLogger("pbl4.server.selector")

BYTES_PER_MB = 1024 * 1024


def fmt_num(value: float, digits: int = 1) -> str:
    """60.0 -> "60", 41.27 -> "41.3", fmt_num(18.5, 2) -> "18.5": số gọn cho câu lý do."""
    text = f"{round(float(value), digits):.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def download_seconds(size_bytes: int, bandwidth_kbps: float) -> float:
    """Thời gian tải = size_bytes × 8 / 1000 / bandwidth_kbps (1 kbps = 1000 bit/s)."""
    return size_bytes * 8 / 1000 / bandwidth_kbps


def estimate_download_s(size_bytes: int, bandwidth_kbps: float | None) -> float | None:
    if not bandwidth_kbps:
        return None
    return round(download_seconds(size_bytes, bandwidth_kbps), 1)


@dataclass(frozen=True)
class SelectionContext:
    """Thông tin thiết bị và nội dung /api/request dùng để chọn variant."""

    device_id: str
    device_class: str
    platform: str
    cpu_arch: str
    ram_mb: int
    runtime: str
    task: str
    bandwidth_kbps: float | None  # băng thông dùng để tính (request hoặc giới hạn server)
    deadline_s: float
    free_storage_mb: float | None
    class_preferred_variant: str | None = None  # ghi nhớ từ feedback của lớp thiết bị


@dataclass(frozen=True)
class Candidate:
    variant: RegistryVariant
    estimated_download_s: float | None

    @property
    def variant_id(self) -> str:
        return self.variant.variant_id


@dataclass(frozen=True)
class Rejection:
    variant_id: str
    reason: str


@dataclass(frozen=True)
class SelectionResult:
    variant_id: str
    reason: str
    rejected: tuple[Rejection, ...] = field(default_factory=tuple)
    selector_version: str = ""


class NoVariantFits(Exception):
    """Không còn ứng viên nào sau bộ lọc cứng."""

    def __init__(self, message: str, rejections: Sequence[Rejection]) -> None:
        super().__init__(message)
        self.message = message
        self.rejections = tuple(rejections)


# --------------------------------------------------------------- bộ lọc cứng


def apply_hard_filters(
    variants: Sequence[RegistryVariant], context: SelectionContext
) -> tuple[list[Candidate], list[Rejection]]:
    """Loại variant sai task, không đủ dung lượng hoặc không tải kịp hạn.

    `variants` lấy từ Registry (chỉ bản `ready`). Trả (ứng viên, bị loại kèm lý do).
    """
    candidates: list[Candidate] = []
    rejections: list[Rejection] = []
    for variant in variants:
        if variant.task_id != context.task:
            rejections.append(Rejection(variant.variant_id, f"khác task ({variant.task_id})"))
            continue
        if (
            context.free_storage_mb is not None
            and variant.size_bytes > context.free_storage_mb * BYTES_PER_MB
        ):
            need_mb = variant.size_bytes / BYTES_PER_MB
            rejections.append(
                Rejection(
                    variant.variant_id,
                    f"cần {fmt_num(need_mb)} MB nhưng chỉ còn "
                    f"{fmt_num(context.free_storage_mb)} MB trống",
                )
            )
            continue
        estimate = estimate_download_s(variant.size_bytes, context.bandwidth_kbps)
        if context.bandwidth_kbps:
            exact = download_seconds(variant.size_bytes, context.bandwidth_kbps)
            if exact > context.deadline_s:
                rejections.append(
                    Rejection(
                        variant.variant_id,
                        f"cần {fmt_num(exact)} s > hạn {fmt_num(context.deadline_s)} s",
                    )
                )
                continue
        candidates.append(Candidate(variant, estimate))
    return candidates, rejections


def no_fit_message(context: SelectionContext, rejections: Sequence[Rejection]) -> str:
    if context.bandwidth_kbps:
        head = (
            f"Không có phiên bản nào tải kịp trong {fmt_num(context.deadline_s)} giây "
            f"ở {fmt_num(context.bandwidth_kbps)} kbps"
        )
    else:
        head = "Không có phiên bản nào phù hợp"
    if context.free_storage_mb is not None:
        head += f" với {fmt_num(context.free_storage_mb)} MB trống"
    details = "; ".join(f"{r.variant_id}: {r.reason}" for r in rejections)
    return f"{head}." + (f" Chi tiết: {details}." if details else "")


# --------------------------------------------------------------- selector


class ModelSelector(ABC):
    """Interface chung: chọn một variant trong danh sách ứng viên đã lọc."""

    version: str = "abstract"

    @abstractmethod
    def select(
        self, context: SelectionContext, candidates: Sequence[Candidate]
    ) -> SelectionResult:
        """Trả variant được chọn. `candidates` không rỗng và đã qua bộ lọc cứng."""


def _describe(candidate: Candidate) -> str:
    text = f"top-1 {candidate.variant.top1_accuracy:.4f}"
    if candidate.estimated_download_s is not None:
        text += f", tải ước tính {fmt_num(candidate.estimated_download_s)} s"
    return text


class RuleBasedSelector(ModelSelector):
    """Baseline rule-v1.

    1. Nếu lớp thiết bị đã được ghi nhớ một variant (từ feedback) và variant đó
       vẫn qua bộ lọc cứng, chọn nó để các máy cùng lớp dùng chung file (P2P).
    2. Ngược lại chọn ứng viên có `top1_accuracy` cao nhất; bằng nhau thì file nhỏ hơn.
    Không dùng tỉ lệ accuracy/byte vì tỉ lệ đó luôn chọn variant nhỏ nhất.
    """

    version = "rule-v1"

    def select(
        self, context: SelectionContext, candidates: Sequence[Candidate]
    ) -> SelectionResult:
        if not candidates:
            raise NoVariantFits(no_fit_message(context, []), [])

        by_id = {c.variant_id: c for c in candidates}
        remembered = context.class_preferred_variant
        if remembered is not None and remembered in by_id:
            chosen = by_id[remembered]
            reason = (
                f"Lớp {context.device_class} đang dùng {chosen.variant_id} "
                f"(ghi nhớ từ feedback), vẫn đạt hạn và dung lượng ({_describe(chosen)}); "
                "giữ để các máy cùng lớp dùng chung file và chia sẻ P2P"
            )
            rejected = tuple(
                Rejection(c.variant_id, f"lớp thiết bị đang dùng {chosen.variant_id}")
                for c in candidates
                if c is not chosen
            )
            return SelectionResult(chosen.variant_id, reason, rejected, self.version)

        ranked = sorted(
            candidates,
            key=lambda c: (-c.variant.top1_accuracy, c.variant.size_bytes, c.variant_id),
        )
        chosen = ranked[0]
        if context.bandwidth_kbps:
            head = (
                f"Băng thông {fmt_num(context.bandwidth_kbps)} kbps, "
                f"hạn {fmt_num(context.deadline_s)} s"
            )
        else:
            head = "Chưa biết băng thông nên không kiểm tra hạn tải"
        reason = (
            f"{head}: chọn {chosen.variant_id} ({_describe(chosen)}), "
            f"top-1 cao nhất trong {len(candidates)} ứng viên đạt điều kiện"
        )
        rejected = tuple(
            Rejection(
                c.variant_id,
                f"top-1 {c.variant.top1_accuracy:.4f} thấp hơn "
                f"{chosen.variant.top1_accuracy:.4f}"
                if c.variant.top1_accuracy < chosen.variant.top1_accuracy
                else "cùng top-1 nhưng file lớn hơn",
            )
            for c in ranked[1:]
        )
        return SelectionResult(chosen.variant_id, reason, rejected, self.version)


class FallbackSelector(ModelSelector):
    """Dùng `primary`; nếu nó lỗi hoặc trả variant ngoài tập ứng viên thì dùng `fallback`."""

    def __init__(self, primary: ModelSelector, fallback: ModelSelector) -> None:
        self.primary = primary
        self.fallback = fallback
        self.version = primary.version

    def select(
        self, context: SelectionContext, candidates: Sequence[Candidate]
    ) -> SelectionResult:
        allowed = {c.variant_id for c in candidates}
        try:
            result = self.primary.select(context, candidates)
        except NoVariantFits:
            raise
        except Exception as exc:  # selector chính lỗi bất kỳ -> không để chặn phân phối
            logger.warning(
                "Selector %s lỗi (%s: %s), chuyển sang %s",
                self.primary.version,
                type(exc).__name__,
                exc,
                self.fallback.version,
            )
            return self.fallback.select(context, candidates)
        if result.variant_id not in allowed:
            logger.warning(
                "Selector %s trả '%s' không thuộc ứng viên %s, chuyển sang %s",
                self.primary.version,
                result.variant_id,
                sorted(allowed),
                self.fallback.version,
            )
            return self.fallback.select(context, candidates)
        return result


class MLSelectorPlaceholder(ModelSelector):
    """Khung mẫu cho ML Selector (chưa bật).

    Khi có mô hình đã huấn luyện và đánh giá so với baseline: nạp mô hình trong
    `__init__`, chấm điểm từng ứng viên trong `select` từ đặc trưng của
    `context` (device_class, bandwidth_kbps, deadline_s, ...) và của
    `candidate.variant` (size_bytes, precision, top1_accuracy, ...), rồi trả
    `SelectionResult` với `selector_version = self.version`.
    """

    version = "ml-placeholder"

    def select(
        self, context: SelectionContext, candidates: Sequence[Candidate]
    ) -> SelectionResult:
        raise NotImplementedError("ML Selector chưa được huấn luyện")


def default_selector() -> ModelSelector:
    """Selector mặc định của server: rule-based (có thể bọc ML Selector sau này)."""
    return RuleBasedSelector()
