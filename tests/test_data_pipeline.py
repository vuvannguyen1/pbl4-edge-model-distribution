from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from server.db.models import Base, DataFileRecord, SelectorWeightRecord
from server.db.repository import apply_selector_feedback
from server.db.sync import sync_data_to_db


def test_sync_data_to_db_upserts_file_content(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "selector").mkdir()
    source = data_dir / "selector" / "samples.csv"
    source.write_text("x,y\n1,2\n", encoding="utf-8")

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert sync_data_to_db(session, data_dir) == 1
        record = session.scalar(
            select(DataFileRecord).where(DataFileRecord.relative_path == "selector/samples.csv")
        )
        assert record is not None
        assert record.content == source.read_bytes()


def test_selector_feedback_neutral_reward_and_penalty() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        apply_selector_feedback(
            session,
            task_id="object_detection",
            variant_id="detector_a",
            device_class="android_arm64_4-8gb",
            sentiment_score=3,
            feedback_id="neutral",
        )
        apply_selector_feedback(
            session,
            task_id="object_detection",
            variant_id="detector_a",
            device_class="android_arm64_4-8gb",
            sentiment_score=5,
            feedback_id="positive",
        )
        apply_selector_feedback(
            session,
            task_id="object_detection",
            variant_id="detector_a",
            device_class="android_arm64_4-8gb",
            sentiment_score=1,
            feedback_id="negative",
        )
        record = session.scalar(select(SelectorWeightRecord))
        assert record is not None
        assert record.weight == 0.0
