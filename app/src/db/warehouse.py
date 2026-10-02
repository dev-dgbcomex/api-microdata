"""Warehouse: Postgres local (schemas raw/core/marts/etl)."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine

from src.config import get_settings
from src.db.postgres import create_pg_engine


@lru_cache
def engine() -> Engine:
    return create_pg_engine(get_settings().database_url_local, pool_size=8, max_overflow=8)


def dispose() -> None:
    engine().dispose()