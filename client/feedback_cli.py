"""Nhập đánh giá kết quả detect bằng terminal và gửi tới server (POST /api/feedback).

Chạy (sau `client.agent` hoặc `client.detect`):
    python -m client.feedback_cli --device-id phoneA --variant-id mobilenetv2_int8
    python -m client.feedback_cli --sync-pending --server http://<server-ip>:8000

CLI hỏi điểm (1-5), dự đoán có đúng không, nhãn thực tế (nếu biết) và nhận xét; thông
tin model và lần detect lấy từ `last_detect.json` trong cache của thiết bị. Các câu
hỏi có thể trả lời trước bằng tham số (`--rating 5 --correct yes ...`). Ở câu hỏi điểm
và câu hỏi đúng/sai, nhấn Enter mà không nhập gì là thoát: đánh giá không được lưu.

Đánh giá được ghi `pending` vào `feedback_queue.jsonl` trước, rồi mới gửi nếu server
đang chạy; server tắt thì giữ `pending`, không phải nhập lại. Lần chạy sau, nếu server
đã chạy mà còn đánh giá `pending`, CLI hỏi có gửi các đánh giá đó không: `y` thì chỉ
gửi chúng (không đánh giá lại), `n` thì giữ `pending` và nhập đánh giá mới (chỉ gửi
đánh giá mới). `--sync-pending` gửi bù theo thứ tự thời gian (không có `--device-id`/
`--cache-dir` thì gửi cho mọi thiết bị trong `data/client/`). Chỉ đổi sang `sent` khi
server xác nhận.

Mã thoát: 0 đã gửi, đã lưu chờ gửi bù hoặc người dùng thoát; với `--sync-pending`:
0 không còn pending, 1 còn pending (server tắt/lỗi) hoặc nhập sai.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from client.agent import REPO_ROOT
from client.api import ApiClient, build_manual_feedback
from client.device_info import default_device_id, sanitize_device_id
from client.feedback import (
    QUEUE_FILE,
    FeedbackQueue,
    SyncReport,
    load_last_detection,
    new_feedback_id,
    utc_now,
)
from client.monitor import server_reachable

CLIENT_DATA = REPO_ROOT / "data" / "client"
EXIT_OK = 0
EXIT_ERROR = 1
MAX_LABEL = 200
MAX_COMMENT = 1000
_VARIANT_ID = re.compile(r"^[a-z0-9_]{1,64}$")
_YES = {"y", "yes", "c", "co", "có", "d", "dung", "đúng", "1", "true"}
_NO = {"n", "no", "k", "khong", "không", "s", "sai", "0", "false"}

Printer = Callable[[str], None]
Asker = Callable[[str], str]


class FeedbackInputError(Exception):
    """Thiếu thông tin hoặc giá trị nhập không hợp lệ."""


class FeedbackCancelled(Exception):
    """Người dùng nhấn Enter bỏ trống ở câu hỏi điểm hoặc đúng/sai: thoát, không lưu."""


# ---------------------------------------------------------------- kiểm giá trị


def parse_rating(text: str) -> int:
    try:
        value = int(text.strip())
    except ValueError:
        raise FeedbackInputError("Điểm phải là số nguyên từ 1 đến 5.") from None
    if not 1 <= value <= 5:
        raise FeedbackInputError("Điểm phải từ 1 đến 5.")
    return value


def parse_yes_no(text: str) -> bool:
    value = text.strip().lower()
    if value in _YES:
        return True
    if value in _NO:
        return False
    raise FeedbackInputError("Trả lời y (có) hoặc n (không).")


def check_text(limit: int) -> Callable[[str], str | None]:
    def parse(text: str) -> str | None:
        value = text.strip()
        if len(value) > limit:
            raise FeedbackInputError(f"Tối đa {limit} ký tự (đang có {len(value)}).")
        return value or None

    return parse


def ask_until_valid(
    ask: Asker,
    out: Printer,
    prompt: str,
    parse: Callable[[str], Any],
    cancel_on_empty: bool = False,
) -> Any:
    while True:
        text = ask(prompt)
        if cancel_on_empty and not text.strip():
            raise FeedbackCancelled
        try:
            return parse(text)
        except FeedbackInputError as exc:
            out(f"  {exc}")


# ---------------------------------------------------------------- in kết quả


def print_report(report: SyncReport, out: Printer) -> None:
    for item in report.sent:
        response = item["response"]
        evaluation = response["evaluation"]
        note = (
            " (server đã nhận trước đó, không ghi trùng)"
            if response["status"] == "duplicate"
            else ""
        )
        out(f"  ✓ {item['feedback_id']}: server xác nhận{note}")
        out(
            f"    Đánh giá: {evaluation['verdict']}"
            + (" – CẦN XEM LẠI VARIANT" if evaluation["needs_review"] else "")
        )
        out(f"    {evaluation['reason']}")
    for item in report.rejected:
        out(f"  ✗ {item['feedback_id']}: server từ chối ({item['error']}), đánh dấu rejected")
    if report.error:
        out(f"  Dừng gửi: {report.error}. Còn {report.remaining} đánh giá pending, sẽ gửi bù sau.")


def sync_queue(
    queue: FeedbackQueue, server: str, out: Printer, only: set[str] | None = None
) -> SyncReport:
    report = queue.sync(ApiClient(server), out=out, only=only)
    print_report(report, out)
    return report


def describe_pending(records: Sequence[dict[str, Any]], out: Printer) -> None:
    for record in records:
        verdict = "đúng" if record.get("correct") else "sai"
        prediction = record.get("prediction") or "?"
        line = (
            f"  - {record.get('created_at')}  {record.get('variant_id')}: "
            f"{record.get('rating')} sao, dự đoán '{prediction}' {verdict}"
        )
        if record.get("comment"):
            line += f", nhận xét: {record['comment']}"
        out(line)


# ---------------------------------------------------------------- lệnh


def cache_dirs_with_queue(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(p.parent for p in base.glob(f"*/{QUEUE_FILE}"))


def run_sync(cache_dirs: Sequence[Path], server: str, out: Printer) -> int:
    queues = [(d, FeedbackQueue(d)) for d in cache_dirs]
    waiting = [(d, q, len(q.pending())) for d, q in queues]
    total = sum(n for _, _, n in waiting)
    if total == 0:
        out("Không có đánh giá nào đang chờ gửi.")
        return EXIT_OK
    if not server_reachable(ApiClient(server).server_url):
        out(f"Không kết nối được server {server}: giữ nguyên {total} đánh giá pending.")
        return EXIT_ERROR
    remaining = 0
    for cache_dir, queue, count in waiting:
        if count == 0:
            continue
        out(f"{cache_dir}: gửi bù {count} đánh giá pending (POST /api/feedback)")
        remaining += sync_queue(queue, server, out).remaining
    out("")
    out("XONG: đã gửi hết." if remaining == 0 else f"Còn {remaining} đánh giá pending.")
    return EXIT_OK if remaining == 0 else EXIT_ERROR


def collect_feedback(
    args: argparse.Namespace, device_id: str, cache_dir: Path, ask: Asker, out: Printer
) -> dict[str, Any]:
    """Hỏi những câu chưa có trong tham số, trả payload đã kiểm (chưa gửi)."""
    last = load_last_detection(cache_dir)
    variant_id = args.variant_id or (last["variant_id"] if last else None)
    if not variant_id:
        raise FeedbackInputError(
            f"Chưa biết model cần đánh giá: chưa có lần detect nào trong {cache_dir}. "
            "Dùng --variant-id."
        )
    if not _VARIANT_ID.match(variant_id):
        raise FeedbackInputError(f"--variant-id '{variant_id}' không hợp lệ (a-z 0-9 _).")
    detection = last if last and last["variant_id"] == variant_id else None
    prediction = args.prediction or (detection.get("prediction") if detection else None)
    prediction = check_text(MAX_LABEL)(prediction or "")

    out("Đánh giá kết quả nhận diện")
    out(f"  Thiết bị : {device_id}")
    out(f"  Model    : {variant_id}")
    if detection is not None:
        confidence = detection.get("confidence")
        conf_text = f" ({confidence * 100:.1f} %)" if isinstance(confidence, float) else ""
        out(f"  Lần detect: {detection.get('detected_at')} – ảnh {detection.get('image')}")
        out(f"  Dự đoán  : {prediction}{conf_text}")
    elif prediction:
        out(f"  Dự đoán  : {prediction}")
    out("")

    out("(Nhấn Enter bỏ trống ở câu hỏi điểm hoặc đúng/sai để thoát, không lưu đánh giá.)")
    rating = (
        parse_rating(str(args.rating))
        if args.rating is not None
        else ask_until_valid(ask, out, "Điểm đánh giá (1-5): ", parse_rating, cancel_on_empty=True)
    )
    question = (
        f"Dự đoán '{prediction}' có đúng không? (y/n): "
        if prediction
        else ("Kết quả nhận diện có đúng không? (y/n): ")
    )
    correct = (
        parse_yes_no(args.correct)
        if args.correct is not None
        else ask_until_valid(ask, out, question, parse_yes_no, cancel_on_empty=True)
    )
    if args.expected_label is not None:
        expected = check_text(MAX_LABEL)(args.expected_label)
    elif correct and prediction:
        expected = prediction
    else:
        expected = ask_until_valid(
            ask, out, "Nhãn thực tế (Enter nếu không biết): ", check_text(MAX_LABEL)
        )
    comment = (
        check_text(MAX_COMMENT)(args.comment)
        if args.comment is not None
        else ask_until_valid(ask, out, "Nhận xét (Enter để bỏ qua): ", check_text(MAX_COMMENT))
    )
    confidence = detection.get("confidence") if detection else None
    return build_manual_feedback(
        feedback_id=new_feedback_id(),
        device_id=device_id,
        variant_id=variant_id,
        rating=rating,
        correct=correct,
        prediction=prediction,
        confidence=confidence if isinstance(confidence, (int, float)) else None,
        expected_label=expected,
        comment=comment,
        request_id=detection.get("request_id") if detection else None,
        detected_at=detection.get("detected_at") if detection else None,
        created_at=utc_now(),
    )


def run_feedback(
    args: argparse.Namespace, device_id: str, cache_dir: Path, ask: Asker, out: Printer
) -> int:
    queue = FeedbackQueue(cache_dir)
    server = ApiClient(args.server).server_url
    pending = queue.pending()
    if pending:
        if server_reachable(server):
            out(f"Server {server} đang chạy. Còn {len(pending)} đánh giá lần trước chưa gửi:")
            describe_pending(pending, out)
            question = f"Gửi {len(pending)} đánh giá lần trước? (y/n): "
            if ask_until_valid(ask, out, question, parse_yes_no):
                out("Gửi các đánh giá lần trước (POST /api/feedback), không cần đánh giá lại...")
                sync_queue(queue, server, out)
                return EXIT_OK
            out("Giữ các đánh giá lần trước ở trạng thái pending; nhập đánh giá mới.")
        else:
            out(f"Còn {len(pending)} đánh giá lần trước đang chờ (server chưa kết nối được).")
        out("")

    try:
        payload = collect_feedback(args, device_id, cache_dir, ask, out)
    except FeedbackCancelled:
        out("")
        out("Đã thoát đánh giá; đánh giá này không được lưu vào hàng chờ.")
        return EXIT_OK
    queue.add(payload)
    out("")
    out(f"Đã lưu {payload['feedback_id']} vào {queue.path.name} (pending).")
    if not server_reachable(server):
        out(
            f"Không kết nối được server {server}: giữ pending, không cần nhập lại. Gửi bù khi "
            f"server chạy: python -m client.feedback_cli --sync-pending --server {server}"
        )
        return EXIT_OK
    out(f"Server {server} đang chạy, gửi đánh giá này (POST /api/feedback)...")
    sync_queue(queue, server, out, only={payload["feedback_id"]})
    return EXIT_OK


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m client.feedback_cli",
        description="Nhập đánh giá kết quả detect, gửi server hoặc lưu chờ gửi bù khi offline.",
    )
    parser.add_argument("--server", default="http://localhost:8000", help="URL server")
    parser.add_argument("--device-id", default=None, help="mặc định: tên máy đã lọc ký tự")
    parser.add_argument("--cache-dir", type=Path, default=None, help="mặc định data/client/<id>")
    parser.add_argument("--variant-id", default=None, help="mặc định: model của lần detect cuối")
    parser.add_argument(
        "--prediction", default=None, help="nhãn model dự đoán (mặc định: lần cuối)"
    )
    parser.add_argument("--rating", type=int, default=None, help="điểm 1-5 (bỏ trống thì hỏi)")
    parser.add_argument("--correct", default=None, help="yes/no: dự đoán có đúng không")
    parser.add_argument("--expected-label", default=None, help="nhãn thực tế nếu biết")
    parser.add_argument("--comment", default=None, help="nhận xét tùy chọn")
    parser.add_argument(
        "--sync-pending", action="store_true", help="chỉ gửi bù các đánh giá đang pending"
    )
    return parser.parse_args(argv)


def resolve_device(args: argparse.Namespace) -> tuple[str, Path]:
    if args.device_id:
        device_id = sanitize_device_id(args.device_id)
        if device_id != args.device_id:
            raise FeedbackInputError("--device-id chỉ gồm A-Z a-z 0-9 _ -, tối đa 32 ký tự")
    elif args.cache_dir is not None:
        device_id = sanitize_device_id(args.cache_dir.resolve().name)
    else:
        device_id = default_device_id()
    return device_id, args.cache_dir or (CLIENT_DATA / device_id)


def main(
    argv: Sequence[str] | None = None,
    ask: Asker = input,
    out: Printer = print,
    base: Path = CLIENT_DATA,
) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    try:
        device_id, cache_dir = resolve_device(args)
        if args.sync_pending:
            dirs = [cache_dir] if args.device_id or args.cache_dir else cache_dirs_with_queue(base)
            return run_sync(dirs, args.server, out)
        return run_feedback(args, device_id, cache_dir, ask, out)
    except FeedbackInputError as exc:
        out(f"[LỖI] {exc}")
        return EXIT_ERROR
    except (EOFError, KeyboardInterrupt):
        out("\nĐã hủy, chưa lưu đánh giá.")
        return EXIT_ERROR
    except (OSError, TimeoutError) as exc:
        out(f"[LỖI] Không ghi được hàng chờ đánh giá: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
