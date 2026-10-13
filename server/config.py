"""Cấu hình server, đọc từ biến môi trường và có giá trị mặc định.

| Biến                   | Mặc định                       |
|------------------------|--------------------------------|
| PBL4_REGISTRY_PATH     | registry/model_registry.json   |
| PBL4_MODEL_STORE       | models                         |
| PBL4_RATE_LIMIT_KBPS   | 0 (không giới hạn)             |
| PBL4_PUBLIC_BASE_URL   | rỗng (lấy từ header Host)      |
| PBL4_PEER_ENABLED      | true                           |
| PBL4_LATENCY_SLO_MS    | 500                            |
| PBL4_DATA_DIR          | data                           |

Đường dẫn tương đối tính từ gốc repo, không tính từ thư mục đang đứng.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CHUNK_SIZE = 1024 * 1024  # 1 MiB, theo Protocol 1.0
PROTOCOL_VERSION = "1.0"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (REPO_ROOT / path)


def _parse_bool(name: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ValueError(f"{name}={value!r} không phải giá trị true/false")


def _parse_non_negative(name: str, value: str) -> float:
    number = float(value)
    if number < 0:
        raise ValueError(f"{name} phải >= 0, nhận {value!r}")
    return number


@dataclass(frozen=True)
class Settings:
    registry_path: Path
    model_store: Path
    rate_limit_kbps: float
    public_base_url: str
    peer_enabled: bool
    latency_slo_ms: float
    data_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            registry_path=_resolve_path(
                env.get("PBL4_REGISTRY_PATH", "registry/model_registry.json")
            ),
            model_store=_resolve_path(env.get("PBL4_MODEL_STORE", "models")),
            rate_limit_kbps=_parse_non_negative(
                "PBL4_RATE_LIMIT_KBPS", env.get("PBL4_RATE_LIMIT_KBPS", "0")
            ),
            public_base_url=env.get("PBL4_PUBLIC_BASE_URL", "").strip().rstrip("/"),
            peer_enabled=_parse_bool("PBL4_PEER_ENABLED", env.get("PBL4_PEER_ENABLED", "true")),
            latency_slo_ms=_parse_non_negative(
                "PBL4_LATENCY_SLO_MS", env.get("PBL4_LATENCY_SLO_MS", "500")
            ),
            data_dir=_resolve_path(env.get("PBL4_DATA_DIR", "data")),
        )

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def feedback_dir(self) -> Path:
        return self.data_dir / "feedback"
