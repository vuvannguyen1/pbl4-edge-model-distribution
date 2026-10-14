"""
feedback_classifier.py
Mô hình phân loại đánh giá / phản hồi của người dùng thành 5 nhãn:
1: Tệ (Rất tiêu cực)
2: Trung bình (Tiêu cực / Kém)
3: Khá (Trung tính / Tạm ổn)
4: Tốt (Tích cực / Hài lòng)
5: Rất tốt (Rất tích cực / Xuất sắc)
"""
from __future__ import annotations

import re
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any

LABEL_MAP = {
    1: "Tệ",
    2: "Trung bình",
    3: "Khá",
    4: "Tốt",
    5: "Rất tốt",
}

KEYWORDS_VERY_NEG = [
    "quá tệ", "rất tệ", "cực tệ", "kinh khủng", "quá chậm", "lag", "giật lag",
    "đơ máy", "treo máy", "đứng hình", "crash", "văng app", "rác", "quá lâu",
    "tải mãi không xong", "không dùng được", "thất vọng", "nhận diện sai hoàn toàn",
    "nhận dạng sai bét", "sai hết", "sai bét", "quá nóng máy"
]

KEYWORDS_NEG = [
    "tệ", "chậm", "hơi chậm", "lag nhẹ", "hơi đơ", "lâu", "tải hơi lâu",
    "sai", "chưa chuẩn", "chưa chính xác", "nóng máy", "tốn pin", "không ổn",
    "kém", "hơi kém", "chưa tốt", "khó chịu", "chờ lâu"
]

KEYWORDS_NEU = [
    "bình thường", "tạm được", "tạm ổn", "tàm tạm", "chấp nhận được", "khá",
    "cũng được", "trung bình", "ổn", "vừa phải", "không có gì đặc biệt"
]

KEYWORDS_POS = [
    "tốt", "nhanh", "mượt", "khá nhanh", "khá mượt", "chính xác", "hài lòng",
    "ổn định", "chuẩn", "dùng tốt", "chạy êm", "đúng ảnh", "dễ dùng"
]

KEYWORDS_VERY_POS = [
    "rất tốt", "quá tốt", "tuyệt vời", "xuất sắc", "cực nhanh", "siêu nhanh",
    "siêu mượt", "quá mượt", "chuẩn xác 100%", "hoàn hảo", "rất hài lòng",
    "cực kỳ hài lòng", "đỉnh", "tuyệt hảo", "quá đỉnh"
]


@dataclass(frozen=True)
class FeedbackResult:
    score: int
    label: str
    confidence: float
    text: str
    engine: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "label": self.label,
            "confidence": round(self.confidence, 4),
            "text": self.text,
            "engine": self.engine,
        }


def _normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text.strip().lower())
    text = re.sub(r"[!?,.:;\-~_]+", " ", text)
    return " ".join(text.split())


class FeedbackClassifier:
    """Bộ phân loại Feedback 5 nhãn cho tiếng Việt và đa ngôn ngữ."""

    def __init__(self, use_neural: bool = False, model_name: str | None = None) -> None:
        self.use_neural = use_neural
        self.neural_pipeline = None

        if use_neural:
            try:
                from transformers import pipeline
                model_id = model_name or "nlptown/bert-base-multilingual-uncased-sentiment"
                self.neural_pipeline = pipeline("sentiment-analysis", model=model_id)
            except Exception as e:
                sys.stderr.write(f"[Cảnh báo] Không thể nạp neural model ({e}), chuyển sang chế độ lexicon.\n")
                self.use_neural = False

    def classify_lexicon(self, text: str) -> FeedbackResult:
        normalized = _normalize_text(text)
        if not normalized:
            return FeedbackResult(score=3, label=LABEL_MAP[3], confidence=0.5, text=text, engine="lexicon")

        for kw in KEYWORDS_VERY_NEG:
            if kw in normalized:
                return FeedbackResult(score=1, label=LABEL_MAP[1], confidence=0.95, text=text, engine="lexicon")

        for kw in KEYWORDS_VERY_POS:
            if kw in normalized:
                return FeedbackResult(score=5, label=LABEL_MAP[5], confidence=0.95, text=text, engine="lexicon")

        for kw in KEYWORDS_NEG:
            if kw in normalized:
                return FeedbackResult(score=2, label=LABEL_MAP[2], confidence=0.85, text=text, engine="lexicon")

        for kw in KEYWORDS_POS:
            if kw in normalized:
                return FeedbackResult(score=4, label=LABEL_MAP[4], confidence=0.85, text=text, engine="lexicon")

        for kw in KEYWORDS_NEU:
            if kw in normalized:
                return FeedbackResult(score=3, label=LABEL_MAP[3], confidence=0.75, text=text, engine="lexicon")

        return FeedbackResult(score=3, label=LABEL_MAP[3], confidence=0.60, text=text, engine="lexicon")

    def classify(self, text: str) -> FeedbackResult:
        if self.use_neural and self.neural_pipeline:
            try:
                result = self.neural_pipeline(text[:512])[0]
                label_str = result["label"]
                match = re.search(r"(\d)", label_str)
                score = int(match.group(1)) if match else 3
                score = max(1, min(5, score))
                confidence = float(result.get("score", 0.8))
                return FeedbackResult(
                    score=score,
                    label=LABEL_MAP[score],
                    confidence=confidence,
                    text=text,
                    engine="neural",
                )
            except Exception:
                pass

        return self.classify_lexicon(text)


_default_classifier: FeedbackClassifier | None = None


def get_classifier() -> FeedbackClassifier:
    global _default_classifier
    if _default_classifier is None:
        _default_classifier = FeedbackClassifier(use_neural=False)
    return _default_classifier


def classify_user_feedback(text: str) -> dict[str, Any]:
    return get_classifier().classify(text).to_dict()
