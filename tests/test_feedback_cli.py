"""Đánh giá thủ công phía client: hàng chờ JSONL, gửi bù, CLI nhập bằng terminal."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

from validate_examples import schema_errors

from client.api import ApiError, build_manual_feedback
from client.feedback import (
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_SENT,
    FeedbackQueue,
    load_last_detection,
    new_feedback_id,
    save_last_detection,
)
from client.feedback_cli import main


def closed_port_url() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def payload(created_at: str = "2026-10-14T00:00:00Z", **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "feedback_id": new_feedback_id(),
        "device_id": "phoneA",
        "variant_id": "mobilenetv2_int8",
        "rating": 5,
        "correct": True,
        "prediction": "Labrador retriever",
        "confidence": 0.8,
        "expected_label": "Labrador retriever",
        "comment": "Kết quả đúng",
        "request_id": None,
        "detected_at": None,
        "created_at": created_at,
    }
    values.update(overrides)
    return build_manual_feedback(**values)


class FakeApi:
    """Server giả: trả lời theo danh sách hành vi ("ok", "down", "bad", "5xx", "noack")."""

    def __init__(self, *behaviours: str) -> None:
        self.behaviours = list(behaviours)
        self.sent: list[dict[str, Any]] = []

    def submit_feedback(self, body: dict[str, Any]) -> dict[str, Any]:
        behaviour = self.behaviours.pop(0) if self.behaviours else "ok"
        if behaviour == "down":
            raise ApiError(None, "CONNECTION", "server tắt")
        if behaviour == "bad":
            raise ApiError(400, "BAD_REQUEST", "Dữ liệu không hợp lệ")
        if behaviour == "5xx":
            raise ApiError(500, "INTERNAL", "lỗi server")
        self.sent.append(body)
        if behaviour == "noack":
            return {"ok": True, "feedback_id": "khac"}
        return {
            "ok": True,
            "feedback_id": body["feedback_id"],
            "status": "accepted",
            "received_at": "2026-10-14T00:00:01+00:00",
            "evaluation": {"verdict": "positive", "needs_review": False, "reason": "ok"},
        }


def statuses(queue: FeedbackQueue) -> list[str]:
    return [r["status"] for r in queue.records()]


# ---------------------------------------------------------------- payload & hàng chờ


def test_manual_payload_matches_schema() -> None:
    body = payload(expected_label="  ", comment="")
    assert schema_errors("ManualFeedbackRequest", body) == []
    assert body["expected_label"] is None and body["comment"] is None
    assert new_feedback_id() != new_feedback_id()


def test_queue_keeps_pending_when_server_down(tmp_path: Path) -> None:
    queue = FeedbackQueue(tmp_path)
    first = queue.add(payload("2026-10-14T00:00:00Z"))
    queue.add(payload("2026-10-14T00:00:05Z"))
    report = queue.sync(FakeApi("down"), out=lambda _: None)
    assert report.error and report.remaining == 2 and not report.sent
    assert statuses(queue) == [STATUS_PENDING, STATUS_PENDING]
    assert queue.pending()[0]["feedback_id"] == first["feedback_id"]


def test_queue_sync_in_order_and_marks_sent_only_after_ack(tmp_path: Path) -> None:
    queue = FeedbackQueue(tmp_path)
    late = queue.add(payload("2026-10-14T00:05:00Z"))
    early = queue.add(payload("2026-10-14T00:01:00Z"))
    api = FakeApi("ok", "down")
    report = queue.sync(api, out=lambda _: None)
    assert [b["feedback_id"] for b in api.sent] == [early["feedback_id"]]  # cũ trước
    assert report.remaining == 1
    by_id = {r["feedback_id"]: r for r in queue.records()}
    assert by_id[early["feedback_id"]]["status"] == STATUS_SENT
    assert by_id[early["feedback_id"]]["evaluation"]["verdict"] == "positive"
    assert by_id[late["feedback_id"]]["status"] == STATUS_PENDING

    queue.sync(FakeApi("ok"), out=lambda _: None)  # server mở lại
    assert statuses(queue) == [STATUS_SENT, STATUS_SENT]


def test_queue_without_ack_or_5xx_stays_pending(tmp_path: Path) -> None:
    queue = FeedbackQueue(tmp_path)
    queue.add(payload())
    assert queue.sync(FakeApi("noack"), out=lambda _: None).remaining == 1
    assert queue.sync(FakeApi("5xx"), out=lambda _: None).remaining == 1
    assert statuses(queue) == [STATUS_PENDING]


def test_queue_rejected_does_not_block_others(tmp_path: Path) -> None:
    queue = FeedbackQueue(tmp_path)
    queue.add(payload("2026-10-14T00:00:00Z"))
    queue.add(payload("2026-10-14T00:00:01Z"))
    report = queue.sync(FakeApi("bad", "ok"), out=lambda _: None)
    assert len(report.rejected) == 1 and len(report.sent) == 1
    assert statuses(queue) == [STATUS_REJECTED, STATUS_SENT]
    assert "BAD_REQUEST" in queue.records()[0]["error"]


def test_queue_preserves_unreadable_lines(tmp_path: Path) -> None:
    queue = FeedbackQueue(tmp_path)
    record = queue.add(payload())
    with queue.path.open("a", encoding="utf-8") as f:
        f.write("{dòng hỏng\n")
    queue.update(record["feedback_id"], status=STATUS_SENT)
    lines = queue.path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "{dòng hỏng"
    assert json.loads(lines[0])["status"] == STATUS_SENT


def test_last_detection_roundtrip(tmp_path: Path) -> None:
    assert load_last_detection(tmp_path) is None
    save_last_detection(
        tmp_path,
        variant_id="mobilenetv2_int8",
        image=Path("a.jpg"),
        prediction="cat",
        confidence=0.5,
        request_id="r-0001",
    )
    last = load_last_detection(tmp_path)
    assert last is not None
    assert last["variant_id"] == "mobilenetv2_int8" and last["prediction"] == "cat"
    assert last["detected_at"].endswith("Z")


# ---------------------------------------------------------------- CLI


def answers(*values: str):
    queue = list(values)
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return queue.pop(0)

    ask.prompts = prompts  # type: ignore[attr-defined]
    return ask


def test_cli_prompts_and_saves_pending_when_server_down(tmp_path: Path) -> None:
    cache = tmp_path / "phoneA"
    save_last_detection(
        cache,
        variant_id="mobilenetv2_int8",
        image=Path("dog.jpg"),
        prediction="Labrador retriever",
        confidence=0.82,
    )
    ask = answers("9", "4", "n", "golden retriever", "Nhầm giống chó")
    lines: list[str] = []
    code = main(
        ["--device-id", "phoneA", "--cache-dir", str(cache), "--server", closed_port_url()],
        ask=ask,
        out=lines.append,
    )
    assert code == 0
    assert any("1 đến 5" in line for line in lines), "điểm 9 bị hỏi lại"
    assert "Labrador retriever" in ask.prompts[2]  # type: ignore[attr-defined]
    [record] = FeedbackQueue(cache).records()
    assert record["status"] == STATUS_PENDING
    assert record["rating"] == 4 and record["correct"] is False
    assert record["prediction"] == "Labrador retriever"
    assert record["expected_label"] == "golden retriever"
    assert record["variant_id"] == "mobilenetv2_int8"
    assert record["detected_at"] and record["feedback_id"].startswith("fb-")
    assert any("--sync-pending" in line for line in lines)


def test_cli_needs_variant_when_no_detection(tmp_path: Path) -> None:
    lines: list[str] = []
    code = main(
        ["--cache-dir", str(tmp_path / "rong"), "--server", closed_port_url()],
        ask=answers(),
        out=lines.append,
    )
    assert code == 1 and "--variant-id" in lines[-1]


def test_cli_sends_directly_when_server_up_then_sync_pending(tmp_path: Path, live_server) -> None:
    cache = tmp_path / "phoneA"
    # lúc server tắt: lưu pending
    offline_args = ["--cache-dir", str(cache), "--variant-id", "mobilenetv2_int8"]
    assert (
        main(
            [*offline_args, "--server", closed_port_url(), "--rating", "3", "--correct", "y"],
            ask=answers("", ""),
            out=lambda _: None,
        )
        == 0
    )
    assert statuses(FeedbackQueue(cache)) == [STATUS_PENDING]

    server = live_server()
    # server mở lại: lệnh đồng bộ gửi bù
    lines: list[str] = []
    code = main(["--sync-pending", "--server", server.url], out=lines.append, base=tmp_path)
    assert code == 0, lines
    assert statuses(FeedbackQueue(cache)) == [STATUS_SENT]
    assert main(["--sync-pending", "--server", server.url], base=tmp_path, out=lambda _: None) == 0

    # server đang chạy: gửi thẳng, qua quy trình đánh giá
    lines = []
    code = main(
        [
            *offline_args,
            "--server",
            server.url,
            "--rating",
            "1",
            "--correct",
            "no",
            "--expected-label",
            "cat",
            "--comment",
            "sai",
        ],
        ask=answers(),
        out=lines.append,
    )
    assert code == 0
    assert statuses(FeedbackQueue(cache)) == [STATUS_SENT, STATUS_SENT]
    assert any("negative" in line for line in lines)

    feedback_dir = server.app.state.settings.feedback_dir
    stored = [
        json.loads(ln)
        for path in feedback_dir.glob("manual-feedback-*.jsonl")
        for ln in path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(stored) == 2
    assert {r["device_id"] for r in stored} == {"phoneA"}  # lấy từ tên thư mục cache


def test_cli_sync_pending_keeps_when_server_down(tmp_path: Path) -> None:
    cache = tmp_path / "phoneA"
    FeedbackQueue(cache).add(payload())
    lines: list[str] = []
    code = main(["--sync-pending", "--server", closed_port_url()], out=lines.append, base=tmp_path)
    assert code == 1
    assert statuses(FeedbackQueue(cache)) == [STATUS_PENDING]
    assert "giữ nguyên 1" in lines[-1]


# ---------------------------------------------------------------- Enter để thoát


def _detected(cache: Path) -> None:
    save_last_detection(
        cache,
        variant_id="mobilenetv2_int8",
        image=Path("cat.jpg"),
        prediction="tiger cat",
        confidence=0.6,
    )


def test_cli_empty_rating_exits_without_saving(tmp_path: Path) -> None:
    cache = tmp_path / "phoneA"
    _detected(cache)
    lines: list[str] = []
    ask = answers("")
    code = main(
        ["--cache-dir", str(cache), "--server", closed_port_url()], ask=ask, out=lines.append
    )
    assert code == 0
    assert len(ask.prompts) == 1  # type: ignore[attr-defined]
    assert FeedbackQueue(cache).records() == []
    assert "không được lưu" in lines[-1]


def test_cli_empty_yes_no_exits_without_saving(tmp_path: Path) -> None:
    cache = tmp_path / "phoneA"
    _detected(cache)
    lines: list[str] = []
    ask = answers("4", "   ")
    code = main(
        ["--cache-dir", str(cache), "--server", closed_port_url()], ask=ask, out=lines.append
    )
    assert code == 0
    assert len(ask.prompts) == 2  # type: ignore[attr-defined]
    assert FeedbackQueue(cache).records() == []


def test_cli_empty_label_and_comment_still_skip(tmp_path: Path) -> None:
    """Enter ở nhãn thực tế/nhận xét vẫn là bỏ qua, không thoát."""
    cache = tmp_path / "phoneA"
    _detected(cache)
    code = main(
        ["--cache-dir", str(cache), "--server", closed_port_url()],
        ask=answers("3", "n", "", ""),
        out=lambda _: None,
    )
    assert code == 0
    [record] = FeedbackQueue(cache).records()
    assert record["expected_label"] is None and record["comment"] is None


# ---------------------------------------------------------------- hỏi gửi đánh giá lần trước


def test_cli_asks_and_sends_only_previous_when_yes(tmp_path: Path, live_server) -> None:
    cache = tmp_path / "phoneA"
    _detected(cache)
    old = FeedbackQueue(cache).add(payload(device_id="phoneA"))
    server = live_server()
    lines: list[str] = []
    ask = answers("y")
    code = main(["--cache-dir", str(cache), "--server", server.url], ask=ask, out=lines.append)
    assert code == 0
    assert ask.prompts == ["Gửi 1 đánh giá lần trước? (y/n): "]  # type: ignore[attr-defined]
    [record] = FeedbackQueue(cache).records()  # không tạo đánh giá mới
    assert record["feedback_id"] == old["feedback_id"] and record["status"] == STATUS_SENT
    assert any("Kết quả đúng" in line for line in lines), "liệt kê đánh giá lần trước"


def test_cli_no_keeps_previous_pending_and_sends_only_new(tmp_path: Path, live_server) -> None:
    cache = tmp_path / "phoneA"
    _detected(cache)
    old = FeedbackQueue(cache).add(payload(device_id="phoneA"))
    server = live_server()
    code = main(
        ["--cache-dir", str(cache), "--server", server.url],
        ask=answers("x", "n", "2", "n", "cat", ""),
        out=lambda _: None,
    )
    assert code == 0
    by_id = {r["feedback_id"]: r for r in FeedbackQueue(cache).records()}
    assert by_id.pop(old["feedback_id"])["status"] == STATUS_PENDING
    [new] = by_id.values()
    assert new["status"] == STATUS_SENT and new["rating"] == 2


def test_cli_does_not_ask_about_previous_when_offline(tmp_path: Path) -> None:
    cache = tmp_path / "phoneA"
    _detected(cache)
    FeedbackQueue(cache).add(payload(device_id="phoneA"))
    ask = answers("5", "y", "")
    code = main(["--cache-dir", str(cache), "--server", closed_port_url()], ask=ask, out=print)
    assert code == 0
    assert not any("lần trước?" in p for p in ask.prompts)  # type: ignore[attr-defined]
    assert statuses(FeedbackQueue(cache)) == [STATUS_PENDING, STATUS_PENDING]
