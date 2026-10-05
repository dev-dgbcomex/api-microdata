"""Neon dgbcomex: tabelas dos agregados pequenos de dashboard (Fase E, D4).

Mesma grão e mesmos nomes de coluna dos `marts` do warehouse local, para o front (`dgbcomex`)
ler direto do Neon. O `numeric` local aparece como `(0,0)` por ser expressao de view; aqui fica
sem restricao - os valores sao os mesmos, so a declaracao e mais fiel.

O que **nao** sobe (volume pesado, continua no Postgres local): `core.estoque_pecas_em_aberto`,
`core.pedido_sugestao_rolos` e qualquer coisa com grao de peca/linha de pedido.

Revision ID: 1003_neon_marts_tabelas
Revises: 1002_neon_auth
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

revision: str = "1003_neon_marts_tabelas"
down_revision: str | None = "1002_neon_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABELAS: tuple[tuple[str, str, str], ...] = (
    (
        "faturamento_diario",
        """
        create table if not exists marts.faturamento_diario (
            data        date primary key,
            itens       bigint,
            pedidos     bigint,
            notas       bigint,
            clientes    bigint,
            metros      numeric,
            vr_total    numeric,
            desconto    numeric,
            faturamento numeric,
            vr_nota     numeric,
            peso        numeric
        )
        """,
        "dia de emissao (KPI de faturamento, incluidos em /dashboard-completo)",
    ),
    (
        "contas_pagas_diario",
        """
        create table if not exists marts.contas_pagas_diario (
            data              date primary key,
            baixas            bigint,
            documentos        bigint,
            fornecedores      bigint,
            valor_pago        numeric,
            juros_pagos       numeric,
            desconto_recebido numeric,
            valor_liquido     numeric
        )
        """,
        "dia de baixa do pagamento (KPI de contas pagas)",
    ),
    (
        "custos_administrativo_mensal",
        """
        create table if not exists marts.custos_administrativo_mensal (
            mes            date primary key,
            parcelas       bigint,
            valor_baixado  numeric
        )
        """,
        "mes x centro de custo administrativo (16 linhas)",
    ),
    (
        "devolucoes_diario",
        """
        create table if not exists marts.devolucoes_diario (
            data       date,
            tipo       text,
            documentos bigint,
            valor      numeric,
            primary key (data, tipo)
        )
        """,
        "dia x tipo de devolucao",
    ),
    (
        "estornos_diario",
        """
        create table if not exists marts.estornos_diario (
            data         date primary key,
            itens        bigint,
            pedidos      bigint,
            notas        bigint,
            valor_nota   numeric,
            valor_itens  numeric,
            acres_desc   numeric
        )
        """,
        "dia de estorno",
    ),
    (
        "financeiro_receber_programado",
        """
        create table if not exists marts.financeiro_receber_programado (
            qtde_doc    text,
            valor_total numeric,
            vencimento  timestamp
        )
        """,
        "titulo a receber em aberto (granularidade: titulo, sem PK - a mesma NF repete)",
    ),
    (
        "financeiro_pagar_programado",
        """
        create table if not exists marts.financeiro_pagar_programado (
            qtde_doc    text,
            valor_total numeric,
            vencimento  timestamp
        )
        """,
        "titulo a pagar em aberto (granularidade: titulo, sem PK)",
    ),
)


def _engine():
    from src.db import neon

    return neon.engine()


def upgrade() -> None:
    with _engine().begin() as conn:
        for nome, ddl, comentario in TABELAS:
            conn.execute(sa.text(ddl))
            conn.execute(
                sa.text(f"COMMENT ON TABLE marts.{nome} IS '{comentario}'")  # noqa: S608
            )
        conn.execute(
            sa.text(
                "COMMENT ON SCHEMA marts IS "
                "'Agregados pequenos de dashboard produzidos on-demand pela API (api-microdata)'"
            )
        )


def downgrade() -> None:
    with _engine().begin() as conn:
        for nome, _, _ in reversed(TABELAS):
            conn.execute(sa.text(f"DROP TABLE IF EXISTS marts.{nome}"))  # noqa: S608