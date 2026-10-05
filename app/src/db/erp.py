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
# Nomes comparados em minúsculas: o SQL Server é case-insensitive por padrão e o nome pode
# vir como `uspFaturamento`, `DBProDash.dbo.uspFaturamento` ou `[DBProDash].[dbo].[uspFaturamento]`.
# Só entram aqui as procedures que apenas leem: as `uspRel_CCusto_Niveis*` e a
# `sp_PagRel_CCusto_Niveis` **escrevem** (`TRUNCATE + INSERT`) e por isso ficam de fora.
PROCEDURES_SOMENTE_LEITURA = frozenset(
    nome.lower()
    for nome in (
        "uspDashFinanceiroContasPagarProgramado",
        "uspDashFinanceiroContasReceberProgramado",
        "uspCustoAdmArmFat",
        "uspCustoAdmArmFatMensal",
        "uspDesconto",
        "uspDevolucao",
        "uspEnderecamentoParaAtenderPedidoGeral",
        "uspEstorno",
        "uspFaturamento",
        "uspFaturamentoDia",
        "uspListagemBaixasPagar",
    )
)


def _nome_procedure(sql: str) -> str | None:
    """Nome da procedure de um `EXEC [banco].[dbo].uspX`, ou None se não for um `EXEC` válido."""
    corpo = sql.strip().rstrip(";").lstrip("(").strip()
    if ";" in corpo:
        return None
    partes = _WHITESPACE.split(corpo, 2)
    if len(partes) < 2 or partes[0].lower() not in ("exec", "execute") or partes[1].startswith("@"):
        return None
    return partes[1].replace("[", "").replace("]", "").split(".")[-1].lower()


def _params(params: Sequence[Any] | str | None) -> tuple[Any, ...]:
    """`str` solto vira **um** parâmetro (não uma sequência de caracteres)."""
    if params is None:
        return ()
    if isinstance(params, str):
        return (params,)
    return tuple(params)


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
    primeiro = _WHITESPACE.split(sql.strip().lstrip("("), 1)[0].lower()
    if primeiro in _ALLOWED_PREFIXES:
        return
    procedure = _nome_procedure(sql)
    if procedure in PROCEDURES_SOMENTE_LEITURA:
        return
    raise PermissionError(
        f"SQL bloqueado no ERP (somente SELECT/WITH/EXEC whitelistado): {primeiro!r}"
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
    params: Sequence[Any] | str | None = None,
    read_only: bool = True,
) -> list[dict[str, Any]]:
    if read_only:
        assert_read_only(sql)
    with connect(read_only=read_only) as conn:
        cursor = conn.cursor()
        cursor.execute(sql, _params(params))
        return _rows(cursor)


def scalar(sql: str, params: Sequence[Any] | str | None = None) -> Any:
    rows = query(sql, params)
    if not rows:
        return None
    return next(iter(rows[0].values()))