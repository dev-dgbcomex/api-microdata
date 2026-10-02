"""Neon dgbcomex: schemas próprios da API (marts/etl). O schema public é do app dgbcomex."""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine

from src.config import get_settings
from src.db.postgres import create_pg_engine


@lru_cache
def engine() -> Engine:
    return create_pg_engine(get_settings().neon_database_url, pool_size=3, max_overflow=2)


def dispose() -> None:
    engine().dispose()