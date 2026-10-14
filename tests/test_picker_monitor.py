"""Chọn ảnh trên máy (hộp thoại / gõ đường dẫn) và theo dõi kết nối tới server."""

from __future__ import annotations

import socket
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import make_test_image, requires_models
from test_detect import closed_port_url, install_real_model

import client.agent as agent_module
import client.detect as detect_module
import client.picker as picker_module
from client.agent import Agent, AgentConfig
from client.monitor import ServerMonitor, server_reachable
from client.picker import parse_typed_paths, pick_images

# ---------------------------------------------------------------- chọn ảnh


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('"C:\\anh cua toi\\cho.jpg" C:\\meo.png', ["C:\\anh cua toi\\cho.jpg", "C:\\meo.png"]),
        ("'D:\\a.jpg'", ["D:\\a.jpg"]),
        ('"C:\\thieu ngoac dong.jpg', ["C:\\thieu ngoac dong.jpg"]),
        ("   ", []),
        ("", []),
    ],
)
def test_parse_typed_paths(text: str, expected: list[str]) -> None:
    assert parse_typed_paths(text) == [Path(p) for p in expected]


def test_pick_falls_back_to_prompt_without_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(picker_module, "pick_with_dialog", lambda multiple, title: None)
    lines: list[str] = []
    typed = '"C:\\a b.jpg" C:\\c.jpg'
    assert pick_images(True, out=lines.append, read=lambda _: typed) == [
        Path("C:\\a b.jpg"),
        Path("C:\\c.jpg"),
    ]
    assert pick_images(False, out=lines.append, read=lambda _: typed) == [Path("C:\\a b.jpg")]
    assert any("hộp thoại" in line for line in lines)

    def eof(_: str) -> str:
        raise EOFError

    assert pick_images(True, out=lines.append, read=eof) == []


def test_pick_uses_dialog_result(monkeypatch: pytest.MonkeyPatch) -> None:
    chosen = [Path("x.jpg")]
    monkeypatch.setattr(picker_module, "pick_with_dialog", lambda multiple, title: chosen)
    assert pick_images(read=lambda _: pytest.fail("không được hỏi terminal")) == chosen


@requires_models
def test_detect_without_image_loops_until_cancel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    cache = tmp_path / "cache"
    install_real_model(cache, "mobilenetv2_int8")
    single = make_test_image(tmp_path / "le.jpg")
    folder = tmp_path / "anh"
    folder.mkdir()
    make_test_image(folder / "1.jpg")
    make_test_image(folder / "2.jpg")
    picks: Iterator[list[Path]] = iter(
        [[single], [tmp_path / "khong_co.jpg"], [folder], []]  # lần cuối = bấm Cancel
    )
    monkeypatch.setattr(detect_module, "pick_images", lambda: next(picks))

    assert detect_module.main(["--cache-dir", str(cache)]) == 0
    output = capsys.readouterr().out
    assert output.count("Nạp model") == 1  # nạp model đúng một lần cho cả phiên
    assert "Không thấy ảnh" in output  # chọn sai không làm dừng phiên
    assert "XONG: 3/3" in output


def test_agent_without_image_and_cancelled_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(agent_module, "pick_images", lambda multiple: [])
    code = agent_module.main(
        ["--server", closed_port_url(), "--peer-port", "0", "--cache-dir", str(tmp_path)]
    )
    assert code == 1
    assert "Chưa chọn ảnh" in capsys.readouterr().out


# ---------------------------------------------------------------- theo dõi server


def test_monitor_reports_only_state_changes() -> None:
    states = iter([True, False, False, True, True])
    lines: list[str] = []
    monitor = ServerMonitor("http://10.0.0.1:8000", out=lines.append, check=lambda: next(states))
    for _ in range(5):
        monitor.poll()
    assert len(lines) == 2
    assert "MẤT KẾT NỐI" in lines[0] and "http://10.0.0.1:8000" in lines[0]
    assert "client.detect" in lines[0]
    assert "kết nối lại" in lines[1]
    assert monitor.connected


def test_server_reachable() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert server_reachable(f"http://127.0.0.1:{port}")
    assert not server_reachable(closed_port_url(), timeout_s=1)
    assert not server_reachable("khong-phai-url")


def test_monitor_thread_detects_server_closing() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    lines: list[str] = []
    monitor = ServerMonitor(url, out=lines.append, interval_s=0.05)
    monitor.start()
    try:
        time.sleep(0.2)
        assert lines == []  # server còn mở: không in gì
        listener.close()
        deadline = time.monotonic() + 5
        while not lines and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lines and "MẤT KẾT NỐI" in lines[0]
    finally:
        monitor.stop()
        listener.close()
    assert not monitor.running


@requires_models
def test_agent_stops_monitor_after_run(tmp_path: Path, live_server) -> None:
    server = live_server()
    agent = Agent(
        AgentConfig(
            server=server.url,
            device_id="monitorA",
            image=make_test_image(tmp_path / "anh.jpg"),
            bandwidth_kbps=600,
            peer_port=0,
            cache_dir=tmp_path / "cache",
            runs=2,
        ),
        out=lambda _: None,
    )
    agent.run()
    assert agent.monitor is None  # không --serve: dừng theo dõi khi xong
