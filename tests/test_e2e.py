"""End-to-end: uvicorn thật trong thread + client agent (gọi hàm, không subprocess)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import make_test_image, requires_models
from validate_examples import schema_errors

from client.agent import Agent, AgentConfig, verified_variants
from client.downloader import sha256_file


@requires_models
def test_agent_full_flow_mobilenetv2_int8(tmp_path: Path, live_server) -> None:
    server = live_server()
    image = make_test_image(tmp_path / "anh.jpg")
    lines: list[str] = []
    config = AgentConfig(
        server=server.url,
        device_id="e2eA",
        image=image,
        bandwidth_kbps=600,  # chỉ mobilenetv2_int8 tải kịp 60 s
        peer_port=0,
        cache_dir=tmp_path / "cache",
        runs=3,
    )
    result = Agent(config, out=lines.append).run()

    assert result.variant["id"] == "mobilenetv2_int8"
    assert result.model_path.is_file()
    assert sha256_file(result.model_path) == result.variant["sha256"]
    assert len(result.inference.top5) == 5
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
    assert record["variant_id"] == "mobilenetv2_int8"
    assert record["device_class"] == result.device_class
    assert record["top1_label"] == result.inference.top5[0].label

    logs = list(settings.logs_dir.glob("server-*.log"))
    assert logs and result.request_id in logs[0].read_text(encoding="utf-8")
    output = "\n".join(lines)
    assert "Nhãn" in output and "sha256 OK" in output


@requires_models
def test_second_run_uses_cached_file_with_empty_plan(tmp_path: Path, live_server) -> None:
    server = live_server()
    image = make_test_image(tmp_path / "anh.jpg")
    config = AgentConfig(
        server=server.url, device_id="e2eB", image=image, bandwidth_kbps=600,
        peer_port=0, cache_dir=tmp_path / "cache", runs=2,
    )
    first = Agent(config, out=lambda _: None).run()
    assert "mobilenetv2_int8" in verified_variants(tmp_path / "cache")
    second = Agent(config, out=lambda _: None).run()
    assert second.request_id != first.request_id
    assert second.download is None, "plan rỗng vì server đã ghi nhận complete"
    assert second.feedback_payload["chunks_from_server"] == 0


@requires_models
def test_no_variant_fits_exit_code(tmp_path: Path, live_server) -> None:
    from client.agent import EXIT_NO_VARIANT, main

    server = live_server()
    image = make_test_image(tmp_path / "anh.jpg")
    code = main([
        "--server", server.url, "--device-id", "e2eC", "--image", str(image),
        "--bandwidth", "100", "--peer-port", "0", "--cache-dir", str(tmp_path / "c"),
    ])
    assert code == EXIT_NO_VARIANT


@requires_models
def test_follow_switch_requests_smaller_variant(tmp_path: Path, live_server) -> None:
    """SLO latency rất thấp -> feedback switch -> --follow-switch xin lại bản nhỏ hơn."""
    server = live_server(latency_slo_ms=0.0001)
    image = make_test_image(tmp_path / "anh.jpg")
    config = AgentConfig(
        server=server.url, device_id="e2eD", image=image, bandwidth_kbps=2000,
        peer_port=0, cache_dir=tmp_path / "cache", runs=2, follow_switch=True,
    )
    result = Agent(config, out=lambda _: None).run()
    assert result.history == ["mobilenetv2_fp32", "mobilenetv2_int8"]
    assert result.variant["id"] == "mobilenetv2_int8"
