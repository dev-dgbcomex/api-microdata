"""Publicacao on-demand dos marts pequenos no Neon (Fase E, D4).

O front (`dgbcomex`) le direto do Neon, mas quem produz o dado e a API, que le o warehouse
local. Publicar aqui e copiar os agregados pequenos - nao as fontes.

Duas garantias:

- **So publica se defasou.** A versao do warehouse e o `max(fim)` das execucoes `ok`
  (`etl.execucoes`); o Neon guarda `publicado_em` em `etl.marts_publicados`. Enquanto nenhuma
  fonte carregou depois da ultima publicacao, nenhuma query sai daqui. E conservador de proposito:
  qualquer carga nova republica tudo - o custo e de alguns milhares de linhas.
- **Troca atomica.** `delete` + `insert` na mesma transacao do Neon: o front nunca ve o mart
  meio publicado (le snapshot antigo ou o novo).

O que **nao** publica: `core.estoque_pecas_em_aberto` e `core.pedido_sugestao_rolos` (grao de
peca/linha, centenas de milhares de linhas) - continuam so no Postgres local.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, text

from src.db import neon, warehouse

SCHEMA = "marts"

#: mart local -> colunas publicadas na ordem. Deliberadamente explicito: se o mart local ganhar
#: coluna, a publicacao para de bater em vez de copiar silenciosamente o que nao foi mapeado.
COLUNAS: dict[str, tuple[str, ...]] = {
    "faturamento_diario": (
        "data",
        "itens",
        "pedidos",
        "notas",
        "clientes",
        "metros",
        "vr_total",
        "desconto",
        "faturamento",
        "vr_nota",
        "peso",
    ),
    "contas_pagas_diario": (
        "data",
        "baixas",
        "documentos",
        "fornecedores",
        "valor_pago",
        "juros_pagos",
        "desconto_recebido",
        "valor_liquido",
    ),
    "custos_administrativo_mensal": ("mes", "parcelas", "valor_baixado"),
    "devolucoes_diario": ("data", "tipo", "documentos", "valor"),
    "estornos_diario": (
        "data",
        "itens",
        "pedidos",
        "notas",
        "valor_nota",
        "valor_itens",
        "acres_desc",
    ),
    "financeiro_receber_programado": ("qtde_doc", "valor_total", "vencimento"),
    "financeiro_pagar_programado": ("qtde_doc", "valor_total", "vencimento"),
}

#: marts que o `/dashboard-completo` precisa para responder (D4).
MARTS_DO_DASHBOARD: tuple[str, ...] = (
    "faturamento_diario",
    "contas_pagas_diario",
    "custos_administrativo_mensal",
    "devolucoes_diario",
    "estornos_diario",
    "financeiro_receber_programado",
    "financeiro_pagar_programado",
)


@dataclass(slots=True)
class Resultado:
    """O que uma rodada de publicacao fez (para log e para o `/health`)."""

    publicados: tuple[str, ...] = ()
    ignorados: tuple[str, ...] = ()
    linhas: int = 0
    motivo: str = "defasado"


def versao_local(engine: Engine | None = None) -> datetime | None:
    """Instante da ultima carga **bem-sucedida** no warehouse local."""
    with (engine or warehouse.engine()).connect() as conn:
        return conn.execute(
            text("select max(fim) from etl.execucoes where status = 'ok'")
        ).scalar()


def publicados(engine: Engine | None = None) -> dict[str, dict[str, object]]:
    """O que ja esta no Neon, com o instante da publicacao."""
    with (engine or neon.engine()).connect() as conn:
        registros = conn.execute(
            text(
                "select mart, motivo, linhas, ultima_exec_etl, publicado_em "
                "from etl.marts_publicados"
            )
        ).mappings()
        return {str(linha["mart"]): dict(linha) for linha in registros}


def defasados(
    marts: tuple[str, ...] | list[str],
    *,
    forcar: bool = False,
    engine_local: Engine | None = None,
    engine_neon: Engine | None = None,
) -> tuple[list[str], list[str]]:
    """Separa o que precisa sair agora do que ja esta em dia no Neon."""
    if forcar:
        return list(marts), []
    versao = versao_local(engine_local)
    if versao is None:
        return list(marts), []
    atuais = publicados(engine_neon)
    defasados_, em_dia = [], []
    for mart in marts:
        registro = atuais.get(mart)
        publicado_em = registro.get("publicado_em") if registro else None
        if publicado_em is None or publicado_em < versao:
            defasados_.append(mart)
        else:
            em_dia.append(mart)
    return defasados_, em_dia


def _linhas_local(mart: str, engine: Engine) -> list[tuple[object, ...]]:
    colunas = COLUNAS[mart]
    selecao = ", ".join(colunas)  # noqa: S608 - nomes vem do COLUNAS, nao de entrada do usuario
    with engine.connect() as conn:
        return [tuple(linha) for linha in conn.execute(text(f"select {selecao} from marts.{mart}"))]  # noqa: S608


def publicar(
    mart: str,
    *,
    motivo: str = "defasado",
    engine_local: Engine | None = None,
    engine_neon: Engine | None = None,
) -> int:
    """Copia um mart do warehouse para o Neon (delete + insert na mesma transacao)."""
    if mart not in COLUNAS:
        raise ValueError(f"mart nao publicavel: {mart}")
    from src.config import get_settings

    local = engine_local or warehouse.engine()
    destino = engine_neon or neon.engine()
    colunas = COLUNAS[mart]
    linhas = _linhas_local(mart, local)
    lote = max(get_settings().etl_batch_size, 500)

    with destino.begin() as conn:
        conn.execute(text(f"delete from {SCHEMA}.{mart}"))  # noqa: S608
        if linhas:
            marcadores = ", ".join(f":{coluna}" for coluna in colunas)
            destino_colunas = ", ".join(colunas)
            payload = [dict(zip(colunas, linha, strict=True)) for linha in linhas]
            for inicio in range(0, len(payload), lote):
                bloco = payload[inicio : inicio + lote]
                conn.execute(
                    text(
                        f"insert into {SCHEMA}.{mart} ({destino_colunas}) "  # noqa: S608
                        f"values ({marcadores})"
                    ),
                    bloco,
                )
        conn.execute(
            text(
                """
                insert into etl.marts_publicados
                    (mart, motivo, linhas, ultima_exec_etl, publicado_em)
                values (:mart, :motivo, :linhas, :versao, now())
                on conflict (mart) do update set
                    motivo = excluded.motivo,
                    linhas = excluded.linhas,
                    ultima_exec_etl = excluded.ultima_exec_etl,
                    publicado_em = excluded.publicado_em
                """
            ),
            {"mart": mart, "motivo": motivo, "linhas": len(linhas), "versao": versao_local(local)},
        )
    return len(linhas)


def sincronizar(
    marts: tuple[str, ...] | list[str] = MARTS_DO_DASHBOARD,
    *,
    forcar: bool = False,
    motivo: str | None = None,
    engine_local: Engine | None = None,
    engine_neon: Engine | None = None,
) -> Resultado:
    """Publica os marts defasados e devolve o que fez."""
    local = engine_local or warehouse.engine()
    destino = engine_neon or neon.engine()
    a_publicar, em_dia = defasados(
        marts, forcar=forcar, engine_local=local, engine_neon=destino
    )
    linhas = 0
    for mart in a_publicar:
        linhas += publicar(
            mart,
            motivo=motivo or ("forcado" if forcar else "defasado"),
            engine_local=local,
            engine_neon=destino,
        )
    return Resultado(
        publicados=tuple(a_publicar),
        ignorados=tuple(em_dia),
        linhas=linhas,
        motivo=motivo or ("forcado" if forcar else "defasado"),
    )


def sincronizar_antes_do_dashboard(marts: tuple[str, ...] = MARTS_DO_DASHBOARD) -> Resultado | None:
    """Chamado pelo `/dashboard-completo`: publica so se defasou, e nunca derruba a resposta.

    Falha de sync **nao** pode derrubar o KPI - o dado continua vindo do warehouse local; o que
    fica para tras e o front ate a proxima chamada (ou ate o `publicar-neon` manual).
    """
    from src.config import get_settings

    if not get_settings().neon_publicar_automatico:
        return None
    try:
        return sincronizar(marts)
    except Exception:  # noqa: BLE001 - sync e best-effort por desenho
        return None