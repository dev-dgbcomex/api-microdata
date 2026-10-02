"""Fábrica de engines PostgreSQL (warehouse local e Neon)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session


def create_pg_engine(url: str, *, pool_size: int = 5, max_overflow: int = 5) -> Engine:
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )


def ping(engine: Engine) -> dict[str, object]:
    with engine.connect() as conn:
        return {
            "ok": True,
            "database": conn.execute(text("select current_database()")).scalar_one(),
            "user": conn.execute(text("select current_user")).scalar_one(),
            "schemas": sorted(
                row[0]
                for row in conn.execute(
                    text(
                        "select nspname from pg_namespace "
                        "where nspname not like 'pg_%' and nspname <> 'information_schema'"
                    )
                )
            ),
        }


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    session = Session(engine, future=True)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()