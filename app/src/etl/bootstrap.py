"""Bootstrap do schema `raw`: carrega as fontes na ordem de dependência e reconcilia.

Ordem (plano de implementação 4.2): cadastros -> cabeçalhos -> itens/parcelas ->
baixas/logs -> estoque de peças. Dentro disso, um pai sempre carrega antes do filho
(`recarrega_pai` apaga os filhos do documento antes de gravar).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import Engine

from src.etl import pipeline, sources
from src.etl.sources import Fonte

ORDEM_DOMINIO: tuple[str, ...] = (
    "cadastros",
    "faturamento",
    "contas_pagar",
    "contas_receber",
    "fiscal",
    "estoque",
)


@dataclass(slots=True)
class Linha:
    fonte: Fonte
    linhas_erp: int | None
    linhas_raw: int | None
    aviso: str = ""


def ordenar(fontes: list[Fonte]) -> list[Fonte]:
    """Ordena por domínio e garante que o pai de `recarrega_pai` venha antes do filho."""
    rank = {dominio: i for i, dominio in enumerate(ORDEM_DOMINIO)}
    restantes = sorted(fontes, key=lambda f: (rank.get(f.dominio, len(rank)), f.tabela_erp))
    ordenadas: list[Fonte] = []
    while restantes:
        pendentes = {r.tabela_erp for r in restantes}
        prontas = [f for f in restantes if not f.pai or f.pai not in pendentes]
        if not prontas:
            circular = ", ".join(f.tabela_erp for f in restantes)
            raise ValueError(f"dependencia circular entre fontes: {circular}")
        for fonte in prontas:
            ordenadas.append(fonte)
            restantes.remove(fonte)
    return ordenadas


def executar(
    engine: Engine,
    fontes: list[Fonte],
    *,
    limite: int | None = None,
    incremental: bool = False,
    somente: set[str] | None = None,
    ao_carregar: Callable[[str, int], None] | None = None,
    conferir: bool = True,
) -> list[Linha]:
    relatorio: list[Linha] = []
    for fonte in ordenar(fontes):
        if somente and fonte.tabela_erp not in somente:
            continue
        resultado = pipeline.carregar(
            engine,
            fonte,
            limite=limite,
            incremental=incremental,
        )
        if ao_carregar:
            ao_carregar(fonte.tabela_erp, resultado.linhas)
        linha = Linha(
            fonte=fonte,
            linhas_erp=None,
            linhas_raw=None,
            aviso="; ".join(resultado.avisos),
        )
        if conferir:
            linha.linhas_erp, linha.linhas_raw = pipeline.conferer(
            engine, fonte
        )
        relatorio.append(linha)
    return relatorio


def divergentes(relatorio: list[Linha]) -> list[Linha]:
    """Linhas em que a contagem do `raw` não bate com a do ERP (ou não foi conferida)."""
    return [
        linha
        for linha in relatorio
        if linha.linhas_erp is not None
        and linha.linhas_erp != linha.linhas_raw
    ]


__all__ = ["ORDEM_DOMINIO", "Linha", "executar", "ordenar", "divergentes", "sources"]