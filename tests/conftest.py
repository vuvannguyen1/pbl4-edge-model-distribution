"""Cấu hình và fixture dùng chung cho pytest."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_STORE = REPO_ROOT / "models"
REGISTRY_PATH = REPO_ROOT / "registry" / "model_registry.json"
EXAMPLES_DIR = REPO_ROOT / "docs" / "examples"

# Cho phép `import validate_examples` (tools/protocol/ không phải package)
sys.path.insert(0, str(REPO_ROOT / "tools" / "protocol"))
sys.path.insert(0, str(REPO_ROOT))

from server.config import CHUNK_SIZE, Settings  # noqa: E402

NORMALIZE = {"mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]}


def load_example(prefix: str) -> dict[str, Any]:
    path = next(EXAMPLES_DIR.glob(f"{prefix}_*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def real_registry_variants() -> list[dict[str, Any]]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))["variants"]


def real_models_present() -> bool:
    """True nếu mọi variant ready trong Registry thật đều có file .onnx."""
    try:
        variants = real_registry_variants()
    except OSError:
        return False
    return all(
        (MODEL_STORE / v["storage_path"]).is_file() for v in variants if v["status"] == "ready"
    )


requires_models = pytest.mark.skipif(
    not real_models_present(), reason="thiếu file .onnx (chạy tools/download_models.py)"
)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "registry_path": REGISTRY_PATH,
        "model_store": MODEL_STORE,
        "rate_limit_kbps": 0.0,
        "public_base_url": "",
        "peer_enabled": True,
        "latency_slo_ms": 500.0,
        "data_dir": tmp_path / "data",
    }
    values.update(overrides)
    return Settings(**values)


def fake_entry(variant_id: str, data: bytes, top1: float, storage_path: str) -> dict[str, Any]:
    chunks = [
        {
            "index": i,
            "offset": i * CHUNK_SIZE,
            "size_bytes": len(data[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]),
            "sha256": hashlib.sha256(data[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]).hexdigest(),
        }
        for i in range((len(data) + CHUNK_SIZE - 1) // CHUNK_SIZE)
    ]
    return {
        "variant_id": variant_id,
        "model_id": variant_id.split("_")[0],
        "version": "1",
        "task_id": "image_classification",
        "file": Path(storage_path).name,
        "storage_path": storage_path,
        "base_model": variant_id.split("_")[0],
        "family": "Fake",
        "level": 0,
        "precision": "fp32",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "chunk_size": CHUNK_SIZE,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "top1_accuracy": top1,
        "input_shape": [1, 3, 224, 224],
        "input_dtype": "float",
        "normalize": NORMALIZE,
        "status": "ready",
    }


FakeStoreFactory = Callable[..., tuple[Path, Path]]


@pytest.fixture
def fake_store(tmp_path: Path) -> FakeStoreFactory:
    """Tạo Model Store + Registry giả với file byte ngẫu nhiên (không cần .onnx thật).

    Gọi: fake_store([("a_fp32", 2_500_000, 0.7), ...], extra_entries=[...])
    Trả (registry_path, model_store).
    """

    def factory(
        specs: list[tuple[str, int, float]], extra_entries: list[dict[str, Any]] | None = None
    ) -> tuple[Path, Path]:
        store = tmp_path / "store"
        (store / "CV").mkdir(parents=True, exist_ok=True)
        variants = []
        for variant_id, size, top1 in specs:
            data = os.urandom(size)
            rel = f"CV/{variant_id}.onnx"
            (store / "CV" / f"{variant_id}.onnx").write_bytes(data)
            variants.append(fake_entry(variant_id, data, top1, rel))
        variants.extend(extra_entries or [])
        registry = tmp_path / "registry.json"
        registry.write_text(
            json.dumps({"schema_version": "1.0", "chunk_size": CHUNK_SIZE, "variants": variants}),
            encoding="utf-8",
        )
        return registry, store

    return factory


@pytest.fixture
def app_factory(tmp_path: Path) -> Iterator[Callable[..., Any]]:
    """Tạo app FastAPI với Settings riêng (data_dir trong tmp_path)."""
    from server.main import create_app

    def factory(selector: Any = None, **overrides: Any) -> Any:
        return create_app(make_settings(tmp_path, **overrides), selector=selector)

    yield factory


@pytest.fixture
def client(app_factory: Callable[..., Any]) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    with TestClient(app_factory()) as test_client:
        yield test_client


# ---------------------------------------------------------------- server thật trong thread


class LiveServer:
    def __init__(self, app: Any) -> None:
        import socket
        import threading

        import uvicorn

        self.app = app
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(app, log_level="warning", lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self._sock]}, daemon=True
        )

    def start(self) -> LiveServer:
        import time

        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("uvicorn không khởi động được")
            time.sleep(0.02)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self._sock.close()


@pytest.fixture
def live_server(app_factory: Callable[..., Any]) -> Iterator[Callable[..., LiveServer]]:
    """Chạy uvicorn trong thread trên cổng ngẫu nhiên. Gọi live_server(**settings)."""
    servers: list[LiveServer] = []

    def start(**overrides: Any) -> LiveServer:
        server = LiveServer(app_factory(**overrides)).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.stop()


def make_test_image(path: Path, size: tuple[int, int] = (320, 240)) -> Path:
    """Ảnh màu tự sinh bằng Pillow (không phụ thuộc mạng)."""
    from PIL import Image

    width, height = size
    img = Image.new("RGB", size)
    pixels = img.load()
    for x in range(width):
        for y in range(height):
            pixels[x, y] = ((x * 255) // width, (y * 255) // height, 128)
    img.save(path, format="JPEG")
    return path
