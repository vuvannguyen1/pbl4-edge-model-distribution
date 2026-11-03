"""Khởi tạo engine, sessionmaker và kết nối CSDL cho hệ thống."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from server.db.models import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_default_database_url() -> str:
    """Trả về chuỗi kết nối từ biến môi trường hoặc mặc định SQLite tại data/pbl4.db."""
    url = os.environ.get("PBL4_DATABASE_URL")
    if url:
        return url

    # Mặc định lưu SQLite tại data/pbl4.db
    data_dir = os.environ.get("PBL4_DATA_DIR")
    if data_dir:
        db_path = Path(data_dir).resolve() / "pbl4.db"
    else:
        repo_root = Path(__file__).resolve().parent.parent.parent
        db_path = repo_root / "data" / "pbl4.db"

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Đường dẫn chuẩn cho SQLite URI trên Windows/Linux
    return f"sqlite:///{db_path.as_posix()}"


def init_engine(database_url: str | None = None, echo: bool = False) -> Engine:
    """Tạo engine kết nối CSDL."""
    global _engine, _SessionFactory
    url = database_url or get_default_database_url()
    if _engine is not None:  # create_app gọi lại (test): đóng kết nối cũ, tránh khóa file SQLite
        _engine.dispose()

    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    _engine = create_engine(url, echo=echo, connect_args=connect_args)
    _SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return _engine


def get_engine() -> Engine:
    """Lấy engine hiện tại, nếu chưa có thì khởi tạo mặc định."""
    global _engine
    if _engine is None:
        return init_engine()
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Lấy SessionFactory hiện tại."""
    global _SessionFactory
    if _SessionFactory is None:
        init_engine()
    assert _SessionFactory is not None
    return _SessionFactory


def init_db(engine: Engine | None = None) -> None:
    """Tạo tất cả các bảng nếu chưa tồn tại."""
    target_engine = engine or get_engine()
    Base.metadata.create_all(bind=target_engine)


def get_db() -> Generator[Session, None, None]:
    """Generator cung cấp session cho FastAPI dependencies hoặc ngữ cảnh xử lý."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()

