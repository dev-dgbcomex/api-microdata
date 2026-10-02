"""Transformacao ERP -> Postgres: trim de char, preservacao de data/hora local."""

from __future__ import annotations

from typing import Any

from src.etl.extract.introspect import ColunaERP

TEXTO = {"char", "nchar", "varchar", "nvarchar", "text", "ntext", "sysname", "xml"}


def eh_texto(coluna: ColunaERP) -> bool:
    return coluna.base.lower() in TEXTO


def linha_limpa(colunas: list[ColunaERP], linha: dict[str, Any]) -> dict[str, Any]:
    saida: dict[str, Any] = {}
    for coluna in colunas:
        valor = linha.get(coluna.nome)
        saida[coluna.nome] = valor.strip() if eh_texto(coluna) and isinstance(valor, str) else valor
    return saida