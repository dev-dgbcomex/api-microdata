"""Carga de uma fonte do ERP no `raw` local (upsert por chave natural / recarga por pai).

Uma `Fonte` do registro vira: leitura paginada no ERP (read-only) -> trim de `char` ->
upsert por chave natural em `raw`, com auditoria em `etl.execucoes` e `etl.watermark`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine

from src.etl import audit, sources
from src.etl.extract import introspect, reader
from src.etl.load import upsert
from src.etl.sources import Fonte
from src.etl.transform import values
from src.etl.transform.naming import snake_case


@dataclass(slots=True)
class Resultado:
    fonte: Fonte
    linhas: int
    tipo: str
    substituidas: int = 0
    documentos: int = 0
    watermark: Any = None
    avisos: list[str] = field(default_factory=list)


def _plano(fonte: Fonte) -> tuple[list[str], list[str], list[str], list[str]]:
    """(colunas_erp, colunas_raw, chave_raw, meta_erp)."""
    colunas_pg, chave_pg = introspect.planejar(fonte)
    meta_erp = introspect.colunas_erp(fonte.tabela_erp)
    mapa = {coluna.origem: coluna.nome for coluna in colunas_pg}
    colunas_erp = list(mapa)
    return colunas_erp, [mapa[coluna] for coluna in colunas_erp], chave_pg, meta_erp


def carregar(
    engine: Engine,
    fonte: Fonte,
    *,
    limite: int | None = None,
    incremental: bool = False,
    recarrega_pai: bool = True,
    gravar_watermark: bool = True,
    progresso=None,
) -> Resultado:
    colunas_erp, colunas_raw, chave_pg, meta_erp = _plano(fonte)

    desde = None
    if incremental and fonte.coluna_watermark:
        desde = audit.get_watermark(engine, fonte.tabela_erp)
        avisos = [f"watermark de origem: {desde}"] if desde else []
    else:
        avisos = []

    tipo = "incremental" if desde is not None else (
        "reconciliacao" if fonte.estrategia in {"full", "reconciliacao"} else "bootstrap"
    )
    por_pai = fonte.estrategia == "recarrega_pai" and bool(fonte.pai) and recarrega_pai
    chave_pai = [snake_case(coluna) for coluna in fonte.colunas_chave_pai()] if por_pai else []
    pais_apagados: set[tuple[Any, ...]] = set()

    total = 0
    substituidas = 0
    documentos = 0
    with audit.execucao(
        engine,
        dominio=fonte.dominio,
        tabela=fonte.tabela_erp,
        tipo=tipo,
        parcial=limite is not None,
    ) as registro:
        for pagina in reader.extrair(
            fonte.tabela_erp,
            colunas_erp,
            coluna_watermark=fonte.coluna_watermark if desde is not None else None,
            desde=desde,
            limite=limite,
        ):
            linhas = []
            for linha in pagina:
                limpa = values.linha_limpa(meta_erp, linha)
                linhas.append(
                    {
                        colunas_raw[i]: limpa.get(coluna)
                        for i, coluna in enumerate(colunas_erp)
                    }
                )
            if por_pai:
                grupo: dict[tuple[Any, ...], dict[str, Any]] = {}
                for linha in linhas:
                    grupo.setdefault(tuple(linha[c] for c in chave_pai), linha)
                novos = [v for k, v in grupo.items() if k not in pais_apagados]
                pais_apagados.update(grupo)
                if novos:
                    substituidas += upsert.apagar_documentos(
                    engine, fonte.destino, chave_pai, novos
                )
                    documentos += len(novos)
            total += upsert.upsert(engine, fonte.destino, chave_pg, colunas_raw, linhas)
            if progresso:
                progresso(total)
        registro.linhas = total
        registro.mensagem = f"colunas={len(colunas_raw)}"

    resultado = Resultado(
        fonte=fonte,
        linhas=total,
        tipo=tipo,
        substituidas=substituidas,
        documentos=documentos,
        avisos=avisos,
    )

    if fonte.coluna_watermark and gravar_watermark:
        if limite is not None:
            resultado.avisos.append(
                "carga parcial (--limite); watermark nao gravado para nao pular linhas"
            )
        else:
            maximo = _maximo_watermark(fonte)
            if maximo is None:
                resultado.avisos.append(
                    f"max({fonte.coluna_watermark}) e nulo; watermark nao gravado"
                )
            else:
                audit.set_watermark(
                    engine, fonte.tabela_erp, fonte.coluna_watermark, maximo, linhas=total
                )
                resultado.watermark = maximo
    return resultado


def _maximo_watermark(fonte: Fonte) -> Any:
    from src.db import erp

    return erp.scalar(f"select max([{fonte.coluna_watermark}]) from [{fonte.tabela_erp}]")


def conferer(engine: Engine, fonte: Fonte) -> tuple[int | None, int | None]:
    """(linhas no ERP, linhas em raw) para reconciliação pós-carga."""
    from sqlalchemy import text

    from src.etl.extract.introspect import linhas_erp

    with engine.connect() as conn:
        no_raw = conn.execute(text(f"select count(*) from raw.{fonte.destino}")).scalar_one()
    return linhas_erp(fonte.tabela_erp), int(no_raw)


__all__ = ["Resultado", "carregar", "conferer", "sources"]