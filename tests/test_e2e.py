"""End-to-end: uvicorn thật trong thread + client agent (gọi hàm, không subprocess)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from conftest import REPO_ROOT, make_test_image, requires_models
from validate_examples import schema_errors

from client.agent import Agent, AgentConfig, verified_variants
from client.api import build_manual_feedback
from client.downloader import sha256_file
from client.feedback import FeedbackQueue, load_last_detection, new_feedback_id


@requires_models
def test_agent_full_flow_yolox_nano(tmp_path: Path, live_server) -> None:
    server = live_server()
    image = REPO_ROOT / "samples" / "dog.jpg"
    lines: list[str] = []
    config = AgentConfig(
        server=server.url,
        device_id="e2eA",
        image=image,
        bandwidth_kbps=600,  # chỉ yolox_nano tải kịp 60 s
        peer_port=0,
        cache_dir=tmp_path / "cache",
        runs=3,
    )
    result = Agent(config, out=lines.append).run()

    assert result.variant["id"] == "yolox_nano"
    assert result.model_path.is_file()
    assert sha256_file(result.model_path) == result.variant["sha256"]
    assert result.inference.top5[0].label == "dog"
    assert result.inference.detections[0].label == "dog"
    assert result.download is not None
    assert result.download.chunks_from_server == result.variant["chunk_count"]
    assert schema_errors("FeedbackRequest", result.feedback_payload) == []
    assert schema_errors("FeedbackResponse", result.feedback) == []
    assert result.feedback["action"] == "keep"

    settings = server.app.state.settings
    files = list(settings.feedback_dir.glob("feedback-*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    assert record["request_id"] == result.request_id
    assert record["variant_id"] == "yolox_nano"
    assert record["device_class"] == result.device_class
    assert record["top1_label"] == result.inference.top5[0].label

    logs = list(settings.logs_dir.glob("server-*.log"))
    assert logs and result.request_id in logs[0].read_text(encoding="utf-8")
    output = "\n".join(lines)
    assert "Vật thể" in output and "sha256 OK" in output


def request_count(server) -> int:
    logs = list(server.app.state.settings.logs_dir.glob("server-*.log"))
    return sum(log.read_text(encoding="utf-8").count("Xin model:") for log in logs)


@requires_models
def test_second_run_uses_local_model_without_request(tmp_path: Path, live_server) -> None:
    server = live_server()
    image = make_test_image(tmp_path / "anh.jpg")
    config = AgentConfig(
        server=server.url,
        device_id="e2eB",
        image=image,
        bandwidth_kbps=600,
        peer_port=0,
        cache_dir=tmp_path / "cache",
        runs=2,
    )
    first = Agent(config, out=lambda _: None).run()
    assert first.source == "server"
    assert "yolox_nano" in verified_variants(tmp_path / "cache")
    assert request_count(server) == 1

    lines: list[str] = []
    second = Agent(config, out=lines.append).run()
    assert second.source == "cache" and not second.offline
    assert second.variant["id"] == "yolox_nano"
    assert second.model_path == first.model_path
    assert second.download is None and second.request_id == ""
    assert second.inference.runs >= 1
    assert request_count(server) == 1, "có model hợp lệ thì không gửi /api/request"
    assert any("feedback_cli" in line for line in lines)

    # --refresh: hỏi server, server đã ghi nhận complete nên plan rỗng, không tải lại
    third = Agent(replace(config, refresh=True), out=lambda _: None).run()
    assert third.source == "server" and third.request_id != first.request_id
    assert third.download is None, "plan rỗng vì server đã ghi nhận complete"
    assert third.feedback_payload["chunks_from_server"] == 0
    assert request_count(server) == 2


@requires_models
def test_tampered_model_is_rejected_and_downloaded_again(tmp_path: Path, live_server) -> None:
    server = live_server()
    image = make_test_image(tmp_path / "anh.jpg")
    cache = tmp_path / "cache"
    config = AgentConfig(
        server=server.url,
        device_id="e2eE",
        image=image,
        bandwidth_kbps=600,
        peer_port=0,
        cache_dir=cache,
        runs=2,
    )
    first = Agent(config, out=lambda _: None).run()
    with first.model_path.open("r+b") as f:
        f.seek(1000)
        f.write(b"\xde\xad\xbe\xef")

    second = Agent(config, out=lambda _: None).run()
    assert second.source == "server", "model hỏng thì phải xin lại"
    assert second.download is not None
    assert second.download.chunks_from_server >= 1
    assert sha256_file(second.model_path) == second.variant["sha256"]
    meta = json.loads((cache / "yolox_nano" / "variant.json").read_text(encoding="utf-8"))
    assert meta["status"] == "complete"


@requires_models
def test_agent_sends_pending_feedback_when_server_back(tmp_path: Path, live_server) -> None:
    cache = tmp_path / "cache"
    queue = FeedbackQueue(cache)
    record = queue.add(
        build_manual_feedback(
            feedback_id=new_feedback_id(),
            device_id="e2eF",
            variant_id="yolox_nano",
            rating=4,
            correct=True,
            prediction="seashore",
            confidence=0.2,
            expected_label=None,
            comment=None,
            request_id=None,
            detected_at=None,
            created_at="2026-10-14T00:00:00Z",
        )
    )
    server = live_server()
    config = AgentConfig(
        server=server.url,
        device_id="e2eF",
        image=make_test_image(tmp_path / "anh.jpg"),
        bandwidth_kbps=600,
        peer_port=0,
        cache_dir=cache,
        runs=2,
    )
    Agent(config, out=lambda _: None).run()
    [stored] = queue.records()
    assert stored["feedback_id"] == record["feedback_id"] and stored["status"] == "sent"
    last = load_last_detection(cache)
    assert last is not None and last["variant_id"] == "yolox_nano"
    assert last["request_id"]


@requires_models
def test_no_variant_fits_exit_code(tmp_path: Path, live_server) -> None:
    from client.agent import EXIT_NO_VARIANT, main

    server = live_server()
    image = make_test_image(tmp_path / "anh.jpg")
    code = main(
        [
            "--server",
            server.url,
            "--device-id",
            "e2eC",
            "--image",
            str(image),
            "--bandwidth",
            "100",
            "--peer-port",
            "0",
            "--cache-dir",
            str(tmp_path / "c"),
        ]
    )
    assert code == EXIT_NO_VARIANT


@requires_models
def test_follow_switch_requests_smaller_variant(tmp_path: Path, live_server) -> None:
    """SLO latency rất thấp -> feedback switch -> --follow-switch xin lại bản nhỏ hơn."""
    server = live_server(latency_slo_ms=0.0001)
    image = make_test_image(tmp_path / "anh.jpg")
    config = AgentConfig(
        server=server.url,
        device_id="e2eD",
        image=image,
        bandwidth_kbps=3000,  # kịp yolox_tiny, ssd_mobilenet_v1_12_int8, yolox_nano
        peer_port=0,
        cache_dir=tmp_path / "cache",
        runs=2,
        follow_switch=True,
        mode=None,  # không khai báo mode: ngưỡng là PBL4_LATENCY_SLO_MS như protocol 1.0
    )
    result = Agent(config, out=lambda _: None).run()
    assert result.history == ["yolox_tiny", "yolox_nano"]  # bản nhỏ hơn có mAP cao nhất
    assert result.variant["id"] == "yolox_nano"
