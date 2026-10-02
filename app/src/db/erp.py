"""Conexão com o ERP (SQL Server DBMicrodata_DGB) — somente leitura."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import pyodbc

from src.config import get_settings

_ALLOWED_PREFIXES = ("select", "with")
_WHITESPACE = re.compile(r"\s+")


def connection_string() -> str:
    s = get_settings()
    return (
        f"DRIVER={{{s.db_driver}}};"
        f"SERVER={s.db_server};"
        f"DATABASE={s.db_database};"
        f"UID={s.db_username};"
        f"PWD={s.db_password};"
        "TrustServerCertificate=yes;"
        "Encrypt=no;"
        f"Connection Timeout={s.db_timeout_s};"
        "Application Name=api-microdata;"
    )


def assert_read_only(sql: str) -> None:
    first = _WHITESPACE.split(sql.strip().lstrip("("), 1)[0].lower()
    if first not in _ALLOWED_PREFIXES:
        raise PermissionError(
            f"SQL bloqueado no ERP (somente SELECT/WITH permitido): {first!r}"
        )


@contextmanager
def connect(read_only: bool = True) -> Iterator[pyodbc.Connection]:
    settings = get_settings()
    conn = pyodbc.connect(connection_string(), autocommit=True, timeout=settings.db_timeout_s)
    try:
        if read_only:
            conn.cursor().execute(f"SET TRANSACTION ISOLATION LEVEL {settings.db_isolation}")
        yield conn
    finally:
        conn.close()


def _rows(cursor: pyodbc.Cursor) -> list[dict[str, Any]]:
    columns = [str(col[0]).strip() for col in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def query(
    sql: str,
    params: Sequence[Any] | None = None,
    read_only: bool = True,
) -> list[dict[str, Any]]:
    if read_only:
        assert_read_only(sql)
    with connect(read_only=read_only) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, tuple(params or ()))
        return _rows(cursor)


def scalar(sql: str, params: Sequence[Any] | None = None) -> Any:
    rows = query(sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))