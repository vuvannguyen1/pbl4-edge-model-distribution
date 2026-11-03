"""Đẩy toàn bộ file trong data/ vào bảng data_files của database."""

from __future__ import annotations

from server.config import Settings
from server.db import get_session_factory, init_db, sync_data_to_db


def main() -> int:
    settings = Settings.from_env()
    init_db()
    with get_session_factory()() as session:
        count = sync_data_to_db(session, settings.data_dir)
    print(f"Synced {count} files from {settings.data_dir} into the database")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
