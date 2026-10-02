"""Normalizacao de identificadores: nomes do ERP (SQL Server) -> snake_case do Postgres."""

from __future__ import annotations

import re
import unicodedata

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NAO_ALFANUM = re.compile(r"[^a-z0-9_]+")
_UNDERSCORES = re.compile(r"_{2,}")

ACENTOS = {
    "ç": "c",
    "ã": "a",
    "á": "a",
    "à": "a",
    "é": "e",
    "ê": "e",
    "í": "i",
    "ó": "o",
    "ô": "o",
    "õ": "o",
    "ú": "u",
    "ü": "u",
    "ñ": "n",
}

RESERVADOS = {
    "all",
    "analyse",
    "analyze",
    "and",
    "any",
    "array",
    "as",
    "asc",
    "authorization",
    "between",
    "binary",
    "both",
    "case",
    "cast",
    "check",
    "collate",
    "column",
    "constraint",
    "create",
    "cross",
    "current_date",
    "current_role",
    "current_time",
    "current_timestamp",
    "current_user",
    "default",
    "deferrable",
    "desc",
    "distinct",
    "do",
    "else",
    "end",
    "except",
    "false",
    "for",
    "foreign",
    "freeze",
    "from",
    "full",
    "grant",
    "group",
    "having",
    "ilike",
    "in",
    "initially",
    "inner",
    "intersect",
    "into",
    "is",
    "isnull",
    "join",
    "leading",
    "left",
    "like",
    "limit",
    "localtime",
    "localtimestamp",
    "natural",
    "not",
    "notnull",
    "null",
    "offset",
    "on",
    "only",
    "or",
    "order",
    "outer",
    "overlaps",
    "placing",
    "primary",
    "references",
    "returning",
    "right",
    "select",
    "session_user",
    "similar",
    "some",
    "symmetric",
    "table",
    "then",
    "to",
    "trailing",
    "true",
    "union",
    "unique",
    "user",
    "using",
    "verbose",
    "when",
    "where",
    "window",
    "with",
}


def snake_case(nome_erp: str) -> str:
    texto = unicodedata.normalize("NFKD", nome_erp)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    for acento, simples in ACENTOS.items():
        texto = texto.replace(acento, simples)
    texto = _CAMEL.sub("_", texto)
    texto = _NAO_ALFANUM.sub("_", texto.lower())
    texto = _UNDERSCORES.sub("_", texto).strip("_")
    if not texto:
        raise ValueError(f"identificador sem forma utilizavel: {nome_erp!r}")
    if texto[0].isdigit():
        texto = f"c_{texto}"
    if texto in RESERVADOS:
        texto = f"{texto}_col"
    return texto


SEGURO_PG = re.compile(r"^[a-z_][a-z0-9_]*$")


def identificar(nome: str) -> str:
    """Nome pronto para o DDL: entre aspas apenas quando necessario."""
    if SEGURO_PG.match(nome) and nome not in RESERVADOS:
        return nome
    return '"' + nome.replace('"', '""') + '"'


def partes_sql_server(nome: str) -> tuple[str | None, str, str]:
    """`banco.esquema.objeto` -> (banco, esquema, objeto); sem banco, `None` e `dbo`.

    Fontes de outro banco (`DBProDash`, o BI) entram no ETL pela mesma rota das demais.
    """
    partes = nome.split(".")
    if len(partes) >= 3:
        return partes[0], partes[1], partes[2]
    if len(partes) == 2:
        return None, partes[0], partes[1]
    return None, "dbo", partes[0]


def qualificar_sql_server(nome: str) -> str:
    """`DBProDash.dbo.X` -> `[DBProDash].[dbo].[X]`; `X` -> `[X]`."""
    return ".".join(f"[{parte.replace(']', ']]')}]" for parte in nome.split("."))