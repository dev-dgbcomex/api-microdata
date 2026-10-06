"""Rotas de KPI do dashboard (contrato do legado, Doc 44 #4 a #14).

Cada rota lê um mart do warehouse local em vez de executar uma `usp` do `DBProDash`: o legado
fazia até 10 `EXEC` por request (`/dashboard-completo`), aqui a leitura é uma query por KPI sobre
`marts`. As chaves do JSON e as regras de janela continuam as do legado — inclusive as duas
escolhas que parecem erro e não são (ver Doc 44):

- a "janela de programmed" (`Vencimento >= 1º dia do mês corrente`, <= 2050-12-31) era aplicada
  pelas procedures, não pelas views, então ela mora aqui — e o mês de corte é o **corrente**,
  não o seguinte (medido contra `uspDashFinanceiroContas*Programado`);
- o "anual" de custos divide o faturamento por 12 (média mensal) e devolve `Armazenagem = 0`.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import text

from src.api.auth.escopos import (
    ESCOPO_FATURAMENTO,
    ESCOPO_FINANCEIRO,
    exigir_escopo,
)
from src.api.auth.usuarios import Usuario
from src.db import warehouse
from src.etl import publicar

router = APIRouter(tags=["kpis"])

_faturamento = Annotated[Usuario | None, Depends(exigir_escopo(ESCOPO_FATURAMENTO))]
_financeiro = Annotated[Usuario | None, Depends(exigir_escopo(ESCOPO_FINANCEIRO))]
# O dashboard junta os dois grupos, entao exige os dois escopos.
_dashboard = Annotated[
    Usuario | None, Depends(exigir_escopo(ESCOPO_FATURAMENTO, ESCOPO_FINANCEIRO))
]

LIMITE_VENCIMENTO = date(2050, 12, 31)


def _consultar(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with warehouse.engine().connect() as conn:
        return [dict(linha) for linha in conn.execute(text(sql), params or {}).mappings()]


def _primeiro_dia(valor: date) -> date:
    return valor.replace(day=1)


def _proximo_mes(valor: date) -> date:
    inicio = _primeiro_dia(valor)
    return date(inicio.year + (inicio.month == 12), inicio.month % 12 + 1, 1)


def _mes_anterior(valor: date) -> date:
    inicio = _primeiro_dia(valor)
    return date(inicio.year - (inicio.month == 1), inicio.month - 1 or 12, 1)


def _soma(sql: str, params: dict[str, Any] | None = None) -> float:
    linha = _consultar(sql, params)
    return float(list(linha[0].values())[0] or 0) if linha else 0.0


# ---------------------------------------------------------------- faturamento


@router.get("/faturamento/{data}")
def faturamento(data: date, usuario: _faturamento) -> dict[str, float]:
    """`uspFaturamento`: Σ(Vr_Total) + Σ(Acres_Desc) do **mês** de `data`."""
    inicio = _primeiro_dia(data)
    return {
        "Faturamento": _soma(
            "select sum(faturamento) from marts.faturamento_diario "
            "where data >= :inicio and data < :fim",
            {"inicio": inicio, "fim": _proximo_mes(inicio)},
        )
    }


@router.get("/faturamento-dia/{data}")
def faturamento_dia(data: date, usuario: _faturamento) -> dict[str, float]:
    """`uspFaturamentoDia`: mesmo cálculo, por **dia** (`ISNULL(...,0)` no legado)."""
    return {
        "Faturamento": _soma(
            "select sum(faturamento) from marts.faturamento_diario where data = :data",
            {"data": data},
        )
    }


@router.get("/descontos/{data}")
def descontos(data: date, usuario: _faturamento) -> dict[str, float]:
    """`uspDesconto`: Σ(Acres_Desc) do mês de `data`."""
    inicio = _primeiro_dia(data)
    return {
        "Desconto": _soma(
            "select sum(desconto) from marts.faturamento_diario "
            "where data >= :inicio and data < :fim",
            {"inicio": inicio, "fim": _proximo_mes(inicio)},
        )
    }


# --------------------------------------------------------------- contas pagas


@router.get("/contas-pagas/{data}")
def contas_pagas(data: date, usuario: _financeiro) -> dict[str, float]:
    """`uspListagemBaixasPagar`: Σ das baixas pagas no mês de `data`.

    No legado o `SELECT` estava comentado e a rota respondia `{}`, mas a `usp` devolve a coluna
    `ContasPagas` — é esse o contrato adotado aqui (Doc 44 #6, atualizado).
    """
    inicio = _primeiro_dia(data)
    return {
        "ContasPagas": _soma(
            "select sum(valor_pago) from marts.contas_pagas_diario "
            "where data >= :inicio and data < :fim",
            {"inicio": inicio, "fim": _proximo_mes(inicio)},
        )
    }


# ------------------------------------------------------- devoluções/estornos


@router.get("/devolucoes/{data}")
def devolucoes(data: date, usuario: _faturamento) -> dict[str, float]:
    """`uspDevolucao`: Σ(Vr_Contabil) das naturezas de devolução no mês de `data`."""
    inicio = _primeiro_dia(data)
    return {
        "Devolucao": _soma(
            "select sum(valor) from marts.devolucoes_diario "
            "where data >= :inicio and data < :fim",
            {"inicio": inicio, "fim": _proximo_mes(inicio)},
        )
    }


@router.get("/estornos/{data}")
def estornos(data: date, usuario: _faturamento) -> dict[str, float]:
    """`uspEstorno`: Σ(Vr_Nota) por `Data_Emissao` no mês de `data`."""
    inicio = _primeiro_dia(data)
    return {
        "Estorno": _soma(
            "select sum(valor_nota) from marts.estornos_diario "
            "where data >= :inicio and data < :fim",
            {"inicio": inicio, "fim": _proximo_mes(inicio)},
        )
    }


# ------------------------------------------------------------- programmed


@router.get("/contas-receber-programado")
def contas_receber_programado(usuario: _financeiro) -> dict[str, Any]:
    """`uspDashFinanceiroContasReceberProgramado`: COUNT(QtdeDoc) e Σ(ValorTotal)."""
    return _programado("marts.financeiro_receber_programado", distinct=False)


@router.get("/contas-pagar-programado")
def contas_pagar_programado(usuario: _financeiro) -> dict[str, Any]:
    """`uspDashFinanceiroContasPagarProgramado`: COUNT(DISTINCT QtdeDoc) — diferença do legado."""
    return _programado("marts.financeiro_pagar_programado", distinct=True)


def _programado(origem: str, *, distinct: bool) -> dict[str, Any]:
    """Aplica a janela que as procedures aplicavam: vencimento de 1º/mês corrente até 2050-12-31.

    A regra real (medida contra `uspDashFinanceiroContas*Programado`) é `>= 1º dia do mês
    corrente`, não `>= 1º/mês seguinte`: o "programado" inclui os vencidos do próprio mês.
    O teto de 2050-12-31 nunca limita hoje (vencimento máximo é 2029) e fica como rede de
    segurança para não vazar registros com data zerada.
    """
    contagem = "count(distinct qtde_doc)" if distinct else "count(*)"
    linha = _consultar(
        f"""
        select {contagem} as documentos, coalesce(sum(valor_total), 0) as valor
          from {origem}
         where vencimento >= :inicio and vencimento <= :limite
        """,
        {"inicio": _primeiro_dia(date.today()), "limite": LIMITE_VENCIMENTO},
    )
    return {
        "QtdeDoc": int(linha[0]["documentos"] or 0),
        "ValorTotal": float(linha[0]["valor"] or 0),
    }


# -------------------------------------------------------- custos administrativos


def _custos(referencia: date, anual: bool) -> dict[str, Any]:
    """`uspCustoAdmArmFat` / `uspCustoAdmArmFatMensal`, medidos contra as procedures.

    As duas procedures são inconsistentes entre si e o replicamos como está (Doc 44 #7/#8):

    - `Faturamento` anual = Σ dos **12 meses fechados** (mês corrente − 12 .. − 1) ÷ 12;
      o mensal é o faturamento do **último mês fechado**, sem dividir;
    - `Administrativo` = acumulado **histórico** inteiro de `1.1.1.1`/`1.1.1.2`; a anual
      ainda divide por 12, a mensal não divide — daí `Porc_Administrativo` de 1758% no mensal;
    - `Armazenagem` e `Porc_Armazenagem` são sempre 0 (não havia cálculo de armazenagem).
    """
    primeiro = _primeiro_dia(referencia)
    if anual:
        janela = {"inicio": _mes_anterior_doze(primeiro), "fim": primeiro}
        divisor = 12
    else:
        janela = {"inicio": _mes_anterior(primeiro), "fim": primeiro}
        divisor = 1
    faturamento = _soma(
        "select sum(faturamento) from marts.faturamento_diario "
        "where data >= :inicio and data < :fim",
        janela,
    ) / divisor
    administrativo = _soma(
        "select sum(valor_baixado) from marts.custos_administrativo_mensal"
    ) / divisor
    return {
        "Faturamento": faturamento,
        "Administrativo": administrativo,
        "Armazenagem": 0.0,
        "Porc_Administrativo": (administrativo / faturamento) if faturamento else 0.0,
        "Porc_Armazenagem": 0.0,
    }


def _mes_anterior_doze(valor: date) -> date:
    inicio = _primeiro_dia(valor)
    for _ in range(12):
        inicio = _mes_anterior(inicio)
    return inicio


@router.get("/custos-administrativos-anual")
def custos_administrativos_anual(usuario: _financeiro, data: date | None = None) -> dict[str, Any]:
    """`uspRel_CCusto_NiveisAnual` + `uspCustoAdmArmFat`: 12 meses fechados, tudo ÷ 12.

    `data` (opcional, padrão hoje) só ancora a janela; a procedure original não recebe data.
    """
    return _custos(data or date.today(), anual=True)


@router.get("/custos-administrativos-mensal")
def custos_administrativos_mensal(
    usuario: _financeiro, data: date | None = None
) -> dict[str, Any]:
    """`uspRel_CCusto_NiveisMensal` + `uspCustoAdmArmFatMensal`: último mês fechado.

    `data` (opcional, padrão hoje) só ancora a janela; a procedure original não recebe data.
    """
    return _custos(data or date.today(), anual=False)


# ---------------------------------------------------------------- dashboard


@router.get("/dashboard-completo/{data}")
def dashboard_completo(data: date, usuario: _dashboard) -> dict[str, Any]:
    """Orquestração do legado (#4 a #13 em 10 `EXEC`); aqui uma leitura por KPI.

    Faturamento/descontos/devoluções/estornos/contas pagas usam o mês de `data`; os custos e os
    programados sao sempre relativos a hoje, como nas procedures (que nao recebem data).

    E aqui que o **sync on-demand** acontece (D4): antes de responder, os agregados pequenos que o
    front le no Neon sao republicados **se** o warehouse carregou depois da ultima publicacao.
    Desligado por `NEON_PUBLICAR_AUTOMATICO=false` (padrao) e best-effort: se o sync falhar, a
    resposta do KPI sai igual, lendo o warehouse local.
    """
    publicar.sincronizar_antes_do_dashboard()
    return {
        "data_consulta": data.isoformat(),
        "faturamento": faturamento(data, usuario),
        "faturamento_dia": faturamento_dia(data, usuario),
        "contas_pagas": contas_pagas(data, usuario),
        "custos_administrativos_anual": custos_administrativos_anual(usuario),
        "custos_administrativos_mensal": custos_administrativos_mensal(usuario),
        "descontos": descontos(data, usuario),
        "devolucoes": devolucoes(data, usuario),
        "estornos": estornos(data, usuario),
        "contas_receber_programado": contas_receber_programado(usuario),
        "contas_pagar_programado": contas_pagar_programado(usuario),
    }