"""Leitura paginada do ERP por watermark (SELECT somente leitura)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import date, datetime
from typing import Any

from src.config import get_settings
from src.db import erp
from src.etl.transform.naming import qualificar_sql_server


def _base(
    tabela_erp: str,
    colunas: Sequence[str],
    coluna_watermark: str | None,
    desde: Any,
    limite: int | None,
) -> tuple[str, list[Any]]:
    lista = ", ".join(f"[{coluna}]" for coluna in colunas)
    condicoes = ""
    params: list[Any] = []
    if coluna_watermark and desde is not None:
        condicoes = f"where [{coluna_watermark}] > ?"
        params.append(desde if isinstance(desde, (date, datetime)) else desde)
    destino = qualificar_sql_server(tabela_erp)
    if limite is not None:
        return f"select top {int(limite)} {lista} from {destino} {condicoes}", params
    return f"select {lista} from {destino} {condicoes}", params


def extrair(
    tabela_erp: str,
    colunas: Sequence[str],
    *,
    coluna_watermark: str | None = None,
    desde: Any = None,
    limite: int | None = None,
    tamanho: int | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Le a fonte em lotes; sem `limite`, pagina com OFFSET/FETCH (exige ORDER BY)."""
    if not colunas:
        raise ValueError("nenhuma coluna informada")
    lote = tamanho or max(get_settings().etl_batch_size, 1)
    sql, params = _base(tabela_erp, colunas, coluna_watermark, desde, limite)

    if limite is not None:
        pagina = erp.query(sql, tuple(params))
        if pagina:
            yield pagina
        return

    deslocamento = 0
    while True:
        pagina = erp.query(
            f"{sql} order by (select null) offset {deslocamento} rows fetch next {lote} rows only",
            tuple(params),
        )
        if not pagina:
            return
        yield pagina
        deslocamento += lote