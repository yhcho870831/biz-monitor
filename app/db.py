from __future__ import annotations

import os
from typing import Callable

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def create_db_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite:")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # type: ignore
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            # Wait (instead of erroring) when another process/thread holds the
            # write lock — needed now that a background heartbeat thread writes
            # concurrently with the worker and scheduler.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw_path = database_url[len(prefix) :]
    if raw_path.startswith("/"):
        path = raw_path
    else:
        path = raw_path.replace("/", os.sep)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def create_session_factory(engine: Engine) -> Callable:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
