"""Giao diện web của client (`client/web_ui.py`): tiến trình, nhận diện, đánh giá, HTTP."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from client.agent import AgentResult
from client.feedback import FeedbackQueue
from client.inference import InferenceResult, Prediction
from client.web_ui import ClientWebApp, Job, WebConfig, WebError, serve

OFFLINE_SERVER = "http://127.0.0.1:9"  # cổng discard: luôn từ chối kết nối


class FakeAgent:
    """Thay client.agent.Agent: in vài dòng đúng định dạng thật rồi trả kết quả."""

    def __init__(self, config, out=print) -> None:
        self.config = config
        self.out = out
        self.peer_service = None
        self.closed = False

    def run(self) -> AgentResult:
        self.out("")
        self.out("══ [5/7] Tải model (2 miếng, tối đa 4 song song) " + "═" * 10)
        self.out("  [  1/2] miếng   0 ← peer     1.00 MB    0.01 s  sha256 OK")
        self.out("  [  2/2] miếng   1 ← server   0.50 MB    0.01 s  sha256 OK")
        self.out("══ Model có sẵn trên máy (không gửi /api/request) " + "═" * 10)
        return AgentResult(
            device_class="windows_x64_gt8gb",
            request_id="r-0001",
            variant={"id": "mobilenetv2_int8", "precision": "int8", "size_bytes": 10},
            model_path=Path("m.onnx"),
            download_ms=12.0,
            download=None,
            inference=InferenceResult(
                input_name="input",
                load_ms=5.0,
                latency_ms_median=1.5,
                runs=3,
                peak_ram_mb=None,
                top5=[Prediction(index=208, label="Labrador retriever", probability=0.6)],
            ),
            feedback_payload={},
            feedback={},
            source="cache",
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def app(tmp_path: Path) -> ClientWebApp:
    config = WebConfig(server=OFFLINE_SERVER, device_id="webA", cache_dir=tmp_path / "cache")
    return ClientWebApp(config, agent_cls=FakeAgent)


def wait_done(job: Job, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = job.view()
        if view["status"] != "running":
            return view
        time.sleep(0.02)
    raise AssertionError("job chưa xong")


# ---------------------------------------------------------------- tiến trình


def test_job_parses_sections_and_chunks() -> None:
    job = Job(job_id="j-1", image="a.jpg", started_at="now")
    job.add("══ [5/7] Tải model (2 miếng, tối đa 4 song song) ══════")
    job.add("  [  1/2] miếng   0 ← peer     1.00 MB    0.01 s  sha256 OK")
    job.add("  [  2/2] miếng   1 ← server   0.50 MB    0.01 s  sha256 OK")
    view = job.view()
    assert view["sections"] == [{"step": 5, "title": "Tải model (2 miếng, tối đa 4 song song)"}]
    assert view["chunks"] == {"done": 2, "total": 2, "peer": 1, "server": 1}
    assert job.view(since=2)["lines"] == [job.lines[2]]


# ---------------------------------------------------------------- nhận diện


def test_detect_runs_agent_and_returns_result(app: ClientWebApp, tmp_path: Path) -> None:
    image = app.save_upload("cho cung.JPG", b"fake-jpeg")
    assert image.parent == app.config.cache_dir / "uploads" and image.suffix == ".jpg"
    view = wait_done(app.start_detect(image, {"bandwidth": "2000", "refresh": "1"}))
    assert view["status"] == "done", view["error"]
    assert view["result"]["top5"][0]["label"] == "Labrador retriever"
    assert view["result"]["source"] == "cache"
    assert [s["step"] for s in view["sections"]] == [5, None]


def test_detect_rejects_bad_input(app: ClientWebApp) -> None:
    with pytest.raises(WebError) as exc:
        app.save_upload("virus.exe", b"x")
    assert exc.value.status == 400
    image = app.save_upload("a.png", b"x")
    with pytest.raises(WebError):
        app.start_detect(image, {"bandwidth": "-5"})
    with pytest.raises(WebError):
        app.start_detect(image, {"preferred": "../etc"})


def test_only_one_detection_at_a_time(app: ClientWebApp) -> None:
    release = threading.Event()

    class SlowAgent(FakeAgent):
        def run(self) -> AgentResult:
            release.wait(5)
            return super().run()

    app.agent_cls = SlowAgent
    image = app.save_upload("a.png", b"x")
    first = app.start_detect(image, {})
    with pytest.raises(WebError) as exc:
        app.start_detect(image, {})
    assert exc.value.code == "BUSY"
    release.set()
    assert wait_done(first)["status"] == "done"


# ---------------------------------------------------------------- danh sách model


def _fake_models(app: ClientWebApp, rows: list[dict]) -> None:
    app.models = lambda: rows  # type: ignore[method-assign]


def test_variants_offline_only_local_complete(app: ClientWebApp) -> None:
    _fake_models(
        app,
        [
            {"variant_id": "mobilenetv2_int8", "status": "complete", "size_bytes": 3},
            {"variant_id": "resnet50_int8", "status": "invalid", "size_bytes": 25},
        ],
    )
    data = app.variants()
    assert data["server_online"] is False
    assert [v["variant_id"] for v in data["variants"]] == ["mobilenetv2_int8"]
    assert data["variants"][0]["local_status"] == "complete"


def test_variants_online_merges_server_registry(app: ClientWebApp, monkeypatch) -> None:
    _fake_models(app, [{"variant_id": "mobilenetv2_int8", "status": "complete", "size_bytes": 3}])
    app.server_online = lambda: True  # type: ignore[method-assign]

    class Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {
                "variants": [
                    {"variant_id": "resnet50_fp32", "size_bytes": 98, "distributable": True},
                    {"variant_id": "mobilenetv2_int8", "size_bytes": 3, "distributable": True},
                    {"variant_id": "draft_one", "size_bytes": 1, "distributable": False},
                ]
            }

    monkeypatch.setattr("client.web_ui.requests.get", lambda *a, **k: Resp())
    data = app.variants()
    ids = [v["variant_id"] for v in data["variants"]]
    assert ids == ["mobilenetv2_int8", "resnet50_fp32"]  # bỏ bản chưa phân phối, sắp theo cỡ
    assert data["variants"][0]["local_status"] == "complete"
    assert data["variants"][1]["local_status"] is None and data["variants"][1]["on_server"]


# ---------------------------------------------------------------- đánh giá


def test_feedback_offline_is_kept_pending(app: ClientWebApp) -> None:
    answer = app.submit_feedback(
        {"variant_id": "mobilenetv2_int8", "rating": 4, "correct": True, "comment": "ok"}
    )
    assert answer["status"] == "pending"
    (record,) = FeedbackQueue(app.config.cache_dir).pending()
    assert record["feedback_id"] == answer["feedback_id"]
    assert record["device_id"] == "webA"
    assert app.feedback_history()[0]["status"] == "pending"


@pytest.mark.parametrize(
    "data",
    [
        {"rating": 4, "correct": True},  # chưa có lần detect nào, không có variant
        {"variant_id": "mobilenetv2_int8", "rating": 9, "correct": True},
        {"variant_id": "mobilenetv2_int8", "rating": 3, "correct": "yes"},
        {"variant_id": "mobilenetv2_int8", "rating": 3, "correct": False, "comment": "x" * 1001},
    ],
)
def test_feedback_validation(app: ClientWebApp, data: dict) -> None:
    with pytest.raises(WebError) as exc:
        app.submit_feedback(data)
    assert exc.value.status == 400


def test_sync_when_offline(app: ClientWebApp) -> None:
    with pytest.raises(WebError) as exc:
        app.sync_feedback()
    assert exc.value.status == 503


# ---------------------------------------------------------------- HTTP


@pytest.fixture
def http_app(app: ClientWebApp):
    httpd = serve(app, "127.0.0.1", 0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def request(port: int, method: str, path: str, body: bytes | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type"), data


def test_http_static_and_state(http_app: int) -> None:
    status, ctype, body = request(http_app, "GET", "/")
    assert status == 200 and "text/html" in ctype and b"PBL4 Client" in body
    assert request(http_app, "GET", "/shared/css/tokens.css")[0] == 200
    assert request(http_app, "GET", "/static/js/client.js")[0] == 200
    status, _, body = request(http_app, "GET", "/api/state")
    state = json.loads(body)
    assert status == 200 and state["device_id"] == "webA" and state["server_online"] is False


def test_http_blocks_path_traversal(http_app: int) -> None:
    for path in ("/shared/../../web_ui.py", "/static/../web_ui.py", "/samples/../pyproject.toml"):
        status, _, _ = request(http_app, "GET", path)
        assert status == 404, path


def test_http_detect_upload_and_job(http_app: int) -> None:
    status, _, body = request(http_app, "POST", "/api/detect?name=a.jpg", b"fake")
    assert status == 202
    job_id = json.loads(body)["job_id"]
    for _ in range(100):
        _, _, body = request(http_app, "GET", f"/api/jobs/{job_id}")
        job = json.loads(body)
        if job["status"] != "running":
            break
        time.sleep(0.02)
    assert job["status"] == "done"
    status, _, body = request(http_app, "POST", "/api/detect?name=a.txt", b"x")
    assert status == 400 and json.loads(body)["error"]["code"] == "BAD_REQUEST"
    assert request(http_app, "GET", "/api/jobs/j-9999")[0] == 404
