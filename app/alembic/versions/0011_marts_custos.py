"""Fase D: custos por centro de custo / departamento (rateio das contas pagas).

Fonte: `DBProDash.dbo.Rel_CCusto_Niveis`, o **snapshot** materializado por
`sp_PagRel_CCusto_Niveis` (Estudo 12 §3/§4). As procs `uspRel_CCusto_Niveis*` que o alimentam
fazem `TRUNCATE + INSERT` no banco do dashboard — nao sao portadas: o espelho `raw` vira a
carga idempotente da regra, lida do mesmo lugar.

Colunas de referencia (`Tot*` sao totais da janela do legado: nunca somar):
    Valor_Baixado  -> valor baixado da parcela no mes da baixa
    Juros          -> juros pago
    CodDesp1..3    -> niveis de despesa   |  CodDpto1..4 -> niveis de departamento
    Data_Baixa     -> competencia do custo |  MesAno = 'MM/AAAA'

Objetos:
    core.custo_baixas                      uma linha por documento/parcela/despesa/departamento
    marts.custos_por_departamento_mensal   mes x despesa x departamento
    marts.custos_administrativo_mensal     Σ Valor_Baixado dos departamentos administrativos
                                          (1.1.1.1 e 1.1.1.2), base do Porc_Administrativo

Revision ID: 0011_marts_custos
Revises: 0010_core_sugestao_rolos
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_marts_custos"
down_revision: str | None = "0010_core_sugestao_rolos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEPARTAMENTOS_ADMINISTRATIVOS = ("1.1.1.1", "1.1.1.2")

VIEW_CUSTO_BAIXAS = """
CREATE OR REPLACE VIEW core.custo_baixas AS
SELECT
    b.empresa,
    b.documento,
    b.parcela,
    b.data_baixa,
    b.cod_fornecedor,
    b.referente,
    b.cod_desp1,
    b.cod_desp2,
    b.cod_desp3,
    b.desc_desp1,
    b.desc_desp2,
    b.desc_desp3,
    b.cod_dpto1,
    b.cod_dpto2,
    b.cod_dpto3,
    b.cod_dpto4,
    b.desc_dpto1,
    b.desc_dpto2,
    b.desc_dpto3,
    b.desc_dpto4,
    coalesce(b.valor_baixado, 0)    AS valor_baixado,
    coalesce(b.juros, 0)            AS juros,
    coalesce(b.valor_aberto, 0)     AS valor_aberto,
    coalesce(b.valor_parcela, 0)    AS valor_parcela,
    -- mesmo formato de chave que o dashboard expõe (Codigo_Despesa/Codigo_Departamento)
    concat_ws('.', b.cod_desp1, b.cod_desp2, b.cod_desp3)     AS codigo_despesa,
    concat_ws('.', b.cod_dpto1, b.cod_dpto2, b.cod_dpto3, b.cod_dpto4) AS codigo_departamento
FROM raw.rel_ccusto_niveis b
"""

VIEW_CUSTOS_DEPARTAMENTO = """
CREATE OR REPLACE VIEW marts.custos_por_departamento_mensal AS
SELECT
    date_trunc('month', c.data_baixa)::date  AS mes,
    c.codigo_departamento,
    c.cod_dpto1,
    c.cod_dpto2,
    c.cod_dpto3,
    c.cod_dpto4,
    min(c.desc_dpto4)                        AS descricao_departamento,
    c.codigo_despesa,
    c.cod_desp1,
    c.cod_desp2,
    c.cod_desp3,
    min(c.desc_desp3)                        AS descricao_despesa,
    count(*)                                 AS parcelas,
    sum(c.valor_baixado)                     AS valor_baixado,
    sum(c.juros)                             AS juros
FROM core.custo_baixas c
WHERE c.data_baixa IS NOT NULL
GROUP BY
    date_trunc('month', c.data_baixa)::date,
    c.codigo_departamento,
    c.cod_dpto1, c.cod_dpto2, c.cod_dpto3, c.cod_dpto4,
    c.codigo_despesa,
    c.cod_desp1, c.cod_desp2, c.cod_desp3
"""

VIEW_CUSTOS_ADMINISTRATIVO = """
CREATE OR REPLACE VIEW marts.custos_administrativo_mensal AS
SELECT
    date_trunc('month', c.data_baixa)::date   AS mes,
    count(*)                                 AS parcelas,
    sum(c.valor_baixado)                     AS valor_baixado
FROM core.custo_baixas c
WHERE c.data_baixa IS NOT NULL
  AND c.codigo_departamento IN ('1.1.1.1', '1.1.1.2')
GROUP BY date_trunc('month', c.data_baixa)::date
"""


def upgrade() -> None:
    op.execute(VIEW_CUSTO_BAIXAS)
    op.execute(
        "COMMENT ON VIEW core.custo_baixas IS "
        "'Rateio de custo por documento/parcela/despesa/departamento "
        "(espelho de DBProDash.dbo.Rel_CCusto_Niveis)'"
    )

    op.execute(VIEW_CUSTOS_DEPARTAMENTO)
    op.execute(
        "COMMENT ON VIEW marts.custos_por_departamento_mensal IS "
        "'Custo baixado por mes, departamento e despesa'"
    )

    op.execute(VIEW_CUSTOS_ADMINISTRATIVO)
    op.execute(
        "COMMENT ON VIEW marts.custos_administrativo_mensal IS "
        "'Valor baixado dos departamentos administrativos 1.1.1.1 e 1.1.1.2 por mes "
        "(base do Porc_Administrativo de uspCustoAdmArmFat)'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS marts.custos_administrativo_mensal")
    op.execute("DROP VIEW IF EXISTS marts.custos_por_departamento_mensal")
    op.execute("DROP VIEW IF EXISTS core.custo_baixas")