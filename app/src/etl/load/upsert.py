"""Carga no warehouse local: upsert por chave natural e recarga por documento-pai."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, text

from src.config import get_settings


def colunas_de(
    engine: Engine,
    tabela_raw: str,
    excluir: Sequence[str] = ("_carga_em",),
) -> list[str]:
    with engine.connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'raw' and table_name = :t "
                    "order by ordinal_position"
                ),
                {"t": tabela_raw},
            )
            if row[0] not in excluir
        ]


def _upsert_sql(tabela_raw: str, colunas: Sequence[str], chave: Sequence[str]) -> str:
    colunas = list(colunas)
    destino = ", ".join(colunas)
    marcas = ", ".join(f":{coluna}" for coluna in colunas)
    conflito = ", ".join(chave)
    atribuicoes = ", ".join(
        f"{coluna} = excluded.{coluna}" for coluna in colunas if coluna not in chave
    )
    if atribuicoes:
        atribuicoes += ", "
    return (
        f"insert into raw.{tabela_raw} ({destino}, _carga_em) values ({marcas}, now()) "
        f"on conflict ({conflito}) do update set {atribuicoes}_carga_em = now()"
    )


def upsert(
    engine: Engine,
    tabela_raw: str,
    chave: list[str],
    colunas: list[str],
    linhas: list[dict[str, Any]],
) -> int:
    if not linhas:
        return 0
    lote = max(get_settings().etl_batch_size, 1)
    comando = text(_upsert_sql(tabela_raw, colunas, chave))
    gravadas = 0
    with engine.begin() as conn:
        for inicio in range(0, len(linhas), lote):
            for linha in linhas[inicio : inicio + lote]:
                gravadas += conn.execute(comando, linha).rowcount
    return gravadas


def tipos_de(engine: Engine, tabela_raw: str) -> dict[str, str]:
    with engine.connect() as conn:
        return {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "select column_name, udt_name from information_schema.columns "
                    "where table_schema = 'raw' and table_name = :t"
                ),
                {"t": tabela_raw},
            )
        }


_SEGURO = re.compile(r"^[a-z_][a-z0-9_]*$")


def apagar_documentos(
    engine: Engine,
    tabela_raw: str,
    chave_pai: Sequence[str],
    valores_pai: list[dict[str, Any]],
) -> int:
    """Apaga os filhos de varios documentos de uma vez (unnest)."""
    if not valores_pai:
        return 0
    tipos = tipos_de(engine, tabela_raw)
    lista = ", ".join(
        f"cast(%({coluna})s as {tipos.get(coluna, 'text')}[])"
        for coluna in chave_pai
        if _SEGURO.match(tipos.get(coluna, "text"))
    )
    assinatura = ", ".join(f"v{i}" for i in range(len(chave_pai)))
    do_pai = ", ".join(f"c.{coluna}" for coluna in chave_pai)
    do_vetor = ", ".join(f"v.v{i}" for i in range(len(chave_pai)))
    with engine.begin() as conn:
        return conn.exec_driver_sql(
            f"delete from raw.{tabela_raw} c using unnest({lista}) as v({assinatura}) "
            f"where ({do_pai}) = ({do_vetor})",
            {coluna: [linha[coluna] for linha in valores_pai] for coluna in chave_pai},
        ).rowcount