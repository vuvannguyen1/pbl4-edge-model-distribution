"""Model giữ lại trên client: active.json, chọn model trong cache, detect offline, agent offline."""

from __future__ import annotations

import shutil
import socket
from pathlib import Path
from typing import Any

import pytest
import requests
from conftest import MODEL_STORE, make_test_image, real_registry_variants, requires_models

from client.agent import Agent, AgentConfig, AgentError
from client.detect import DetectError, collect_images, detect, find_cache_dir, main
from client.downloader import (
    choose_local,
    load_active,
    save_active,
    save_variant_meta,
    variant_paths,
)
from server.registry import to_protocol_variant

# ---------------------------------------------------------------- helper


def fake_variant(variant_id: str, top1: float, size: int) -> dict[str, Any]:
    return {"id": variant_id, "top1_accuracy": top1, "size_bytes": size}


def install_real_model(cache_dir: Path, variant_id: str) -> Path:
    """Chép model thật từ Model Store vào cache như thể client.agent đã tải về."""
    entry = next(v for v in real_registry_variants() if v["variant_id"] == variant_id)
    variant = to_protocol_variant(entry).model_dump(mode="json")
    final, _ = variant_paths(cache_dir, variant)
    final.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MODEL_STORE / entry["storage_path"], final)
    save_variant_meta(cache_dir, variant)
    return final


def closed_port_url() -> str:
    """URL tới một cổng không có ai nghe: giả lập server đã tắt."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mọi request HTTP đều lỗi: chứng minh detect không gọi server."""

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("detect không được gọi mạng")

    monkeypatch.setattr(requests.Session, "request", refuse)


# ---------------------------------------------------------------- active.json


def test_active_roundtrip(tmp_path: Path) -> None:
    assert load_active(tmp_path) is None
    save_active(tmp_path, "mobilenetv2_int8")
    assert load_active(tmp_path) == "mobilenetv2_int8"
    save_active(tmp_path, "resnet50_int8")
    assert load_active(tmp_path) == "resnet50_int8"


def test_active_corrupt_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "active.json").write_text("{không phải json", encoding="utf-8")
    assert load_active(tmp_path) is None


# ---------------------------------------------------------------- chọn model trong cache


FOUND = {
    "small": (fake_variant("small", 0.68, 100), Path("small.onnx")),
    "big": (fake_variant("big", 0.75, 900), Path("big.onnx")),
    "same": (fake_variant("same", 0.75, 500), Path("same.onnx")),
}


def test_choose_local_requested_wins() -> None:
    variant, path, _ = choose_local(FOUND, requested="small", active="big")
    assert variant["id"] == "small" and path == Path("small.onnx")


def test_choose_local_requested_missing_raises() -> None:
    with pytest.raises(LookupError, match="khong_co"):
        choose_local(FOUND, requested="khong_co")


def test_choose_local_active_before_top1() -> None:
    variant, _, why = choose_local(FOUND, active="small")
    assert variant["id"] == "small" and "active.json" in why


def test_choose_local_top1_then_smaller_file() -> None:
    variant, _, why = choose_local(FOUND, active="da_bi_xoa")
    assert variant["id"] == "same"  # top-1 0.75 như "big" nhưng file nhỏ hơn
    assert "top-1" in why


def test_choose_local_empty_raises() -> None:
    with pytest.raises(LookupError):
        choose_local({})


# ---------------------------------------------------------------- tìm cache và ảnh


def _device_with_model(base: Path, name: str) -> Path:
    folder = base / name / "some_variant"
    folder.mkdir(parents=True)
    (folder / "variant.json").write_text("{}", encoding="utf-8")
    return base / name


def test_find_cache_dir_explicit(tmp_path: Path) -> None:
    assert find_cache_dir(tmp_path / "x", "ignored", base=tmp_path) == tmp_path / "x"
    assert find_cache_dir(None, "laptopB", base=tmp_path) == tmp_path / "laptopB"


def test_find_cache_dir_single_device(tmp_path: Path) -> None:
    device = _device_with_model(tmp_path, "laptopB")
    (tmp_path / "empty_device").mkdir()  # thư mục không có model không tính
    assert find_cache_dir(None, None, base=tmp_path) == device


def test_find_cache_dir_none_or_many(tmp_path: Path) -> None:
    with pytest.raises(DetectError, match="Chưa có model"):
        find_cache_dir(None, None, base=tmp_path / "khong_ton_tai")
    _device_with_model(tmp_path, "laptopA")
    _device_with_model(tmp_path, "laptopB")
    with pytest.raises(DetectError, match="laptopA, laptopB"):
        find_cache_dir(None, None, base=tmp_path)


def test_collect_images(tmp_path: Path) -> None:
    folder = tmp_path / "anh"
    folder.mkdir()
    b = make_test_image(folder / "b.JPG")
    a = make_test_image(folder / "a.png")
    (folder / "ghi_chu.txt").write_text("x", encoding="utf-8")
    single = make_test_image(tmp_path / "le.jpg")
    assert collect_images([single, folder]) == [single, a, b]
    with pytest.raises(DetectError, match="Không thấy"):
        collect_images([tmp_path / "khong_co.jpg"])
    empty = tmp_path / "rong"
    empty.mkdir()
    with pytest.raises(DetectError, match="Không có ảnh"):
        collect_images([empty])


# ---------------------------------------------------------------- detect offline (model thật)


@requires_models
def test_detect_many_images_offline(tmp_path: Path, no_network: None, capsys) -> None:
    cache = tmp_path / "cache"
    install_real_model(cache, "mobilenetv2_int8")
    folder = tmp_path / "anh"
    folder.mkdir()
    make_test_image(folder / "1.jpg")
    make_test_image(folder / "2.jpg", (200, 300))
    single = make_test_image(tmp_path / "le.jpg")

    code = main(["--image", str(single), str(folder), "--cache-dir", str(cache)])

    output = capsys.readouterr().out
    assert code == 0
    assert "mobilenetv2_int8" in output and "SHA-256 khớp" in output
    assert "[1/3]" in output and "[3/3]" in output
    assert output.count("Xác suất") == 3
    assert "XONG: 3/3" in output


@requires_models
def test_detect_prefers_active_variant(tmp_path: Path, no_network: None) -> None:
    cache = tmp_path / "cache"
    install_real_model(cache, "mobilenetv2_int8")
    install_real_model(cache, "mobilenetv2_fp32")  # top-1 cao hơn int8
    image = make_test_image(tmp_path / "anh.jpg")

    assert detect([image], cache, out=lambda _: None).variant["id"] == "mobilenetv2_fp32"
    save_active(cache, "mobilenetv2_int8")
    assert detect([image], cache, out=lambda _: None).variant["id"] == "mobilenetv2_int8"
    chosen = detect([image], cache, variant_id="mobilenetv2_fp32", out=lambda _: None)
    assert chosen.variant["id"] == "mobilenetv2_fp32"


@requires_models
def test_detect_refuses_tampered_model(tmp_path: Path, no_network: None, capsys) -> None:
    cache = tmp_path / "cache"
    model = install_real_model(cache, "mobilenetv2_int8")
    with model.open("r+b") as f:
        f.seek(100)
        f.write(b"\x00\x01\x02\x03")
    image = make_test_image(tmp_path / "anh.jpg")

    assert main(["--image", str(image), "--cache-dir", str(cache)]) == 1
    assert "chưa có model nào hợp lệ" in capsys.readouterr().out


@requires_models
def test_detect_bad_image_does_not_stop_others(tmp_path: Path, no_network: None) -> None:
    cache = tmp_path / "cache"
    install_real_model(cache, "mobilenetv2_int8")
    bad = tmp_path / "hong.jpg"
    bad.write_bytes(b"khong phai anh")
    good = make_test_image(tmp_path / "tot.jpg")

    result = detect([bad, good], cache, out=lambda _: None)
    assert result.failures == 1
    assert result.results[0][1] is None and result.results[1][1] is not None


# ---------------------------------------------------------------- agent: active.json + offline


@requires_models
def test_agent_saves_active_then_runs_offline_when_server_down(tmp_path: Path, live_server) -> None:
    server = live_server()
    cache = tmp_path / "cache"
    image = make_test_image(tmp_path / "anh.jpg")
    online = Agent(
        AgentConfig(
            server=server.url,
            device_id="offlineA",
            image=image,
            bandwidth_kbps=600,  # chỉ mobilenetv2_int8 tải kịp
            peer_port=0,
            cache_dir=cache,
            runs=2,
        ),
        out=lambda _: None,
    ).run()
    assert not online.offline
    assert load_active(cache) == "mobilenetv2_int8"

    lines: list[str] = []
    offline = Agent(
        AgentConfig(
            server=closed_port_url(),
            device_id="offlineA",
            image=image,
            peer_port=0,
            cache_dir=cache,
            runs=2,
        ),
        out=lines.append,
    ).run()
    assert offline.offline
    assert offline.variant["id"] == "mobilenetv2_int8"
    assert offline.model_path == online.model_path
    assert len(offline.inference.top5) == 5
    assert offline.feedback == {} and offline.download is None
    assert any("Chuyển sang chế độ offline" in line for line in lines)


@requires_models
def test_agent_refresh_falls_back_offline_when_server_down(tmp_path: Path) -> None:
    """--refresh buộc hỏi server; server tắt thì vẫn detect bằng model complete có sẵn."""
    cache = tmp_path / "cache"
    install_real_model(cache, "mobilenetv2_int8")
    lines: list[str] = []
    result = Agent(
        AgentConfig(
            server=closed_port_url(),
            device_id="offlineC",
            image=make_test_image(tmp_path / "anh.jpg"),
            peer_port=0,
            cache_dir=cache,
            runs=1,
            refresh=True,
        ),
        out=lines.append,
    ).run()
    assert result.offline and result.variant["id"] == "mobilenetv2_int8"
    assert any("Chuyển sang chế độ offline" in line for line in lines)
    assert install_real_model(cache, "mobilenetv2_int8").is_file(), "offline không xóa model"


def test_agent_offline_without_cache_fails_clearly(tmp_path: Path) -> None:
    config = AgentConfig(
        server=closed_port_url(),
        device_id="offlineB",
        image=make_test_image(tmp_path / "anh.jpg"),
        peer_port=0,
        cache_dir=tmp_path / "cache_rong",
    )
    with pytest.raises(AgentError, match="chưa thể detect offline.*Hãy chạy lại khi có kết nối"):
        Agent(config, out=lambda _: None).run()
