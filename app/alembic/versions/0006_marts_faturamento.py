"""Fase D: marts de faturamento (diario/mensal) derivados de core.faturamento_itens

Reproduz a regra das procs legadas `uspFaturamento` / `uspFaturamentoDia` / `uspDesconto`
(DBProDash): no mes, `SUM(Vr_Total) + SUM(Acres_Desc)`; no dia, o mesmo por `Data_Nota`.

Revision ID: 0006_marts_faturamento
Revises: 0005_core_faturamento
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_marts_faturamento"
down_revision: str | None = "0005_core_faturamento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_FATURAMENTO_DIARIO = """
CREATE OR REPLACE VIEW marts.faturamento_diario AS
SELECT
    data_nota::date                                       AS data,
    count(*)                                              AS itens,
    count(DISTINCT pedido)                                AS pedidos,
    count(DISTINCT nr_nota)                               AS notas,
    count(DISTINCT cliente)                               AS clientes,
    sum(metros)                                           AS metros,
    sum(vr_total)                                         AS vr_total,
    sum(acres_desc)                                       AS desconto,
    sum(vr_total) + sum(acres_desc)                       AS faturamento,
    sum(vr_nota)                                          AS vr_nota,
    sum(peso)                                             AS peso
FROM core.faturamento_itens
WHERE data_nota IS NOT NULL
GROUP BY data_nota::date
"""

VIEW_FATURAMENTO_MENSAL = """
CREATE OR REPLACE VIEW marts.faturamento_mensal AS
SELECT
    date_trunc('month', data_nota)::date                   AS mes,
    count(*)                                              AS itens,
    count(DISTINCT pedido)                                AS pedidos,
    count(DISTINCT nr_nota)                               AS notas,
    sum(metros)                                           AS metros,
    sum(vr_total)                                         AS vr_total,
    sum(acres_desc)                                       AS desconto,
    sum(vr_total) + sum(acres_desc)                       AS faturamento,
    round((sum(vr_total) + sum(acres_desc)) / 12.0, 2)    AS faturamento_mensal_medio
FROM core.faturamento_itens
WHERE data_nota IS NOT NULL
GROUP BY date_trunc('month', data_nota)::date
"""


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS marts")

    op.execute(VIEW_FATURAMENTO_DIARIO)
    op.execute(
        "COMMENT ON VIEW marts.faturamento_diario IS "
        "'Faturamento por dia de nota (porta de uspFaturamentoDia/vwFaturamento)'"
    )

    op.execute(VIEW_FATURAMENTO_MENSAL)
    op.execute(
        "COMMENT ON VIEW marts.faturamento_mensal IS "
        "'Faturamento por mes (porta de uspFaturamento); a media de 12 meses e usada "
        "pelo custo administrativo anual (uspCustoAdmArmFat)'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS marts.faturamento_mensal")
    op.execute("DROP VIEW IF EXISTS marts.faturamento_diario")