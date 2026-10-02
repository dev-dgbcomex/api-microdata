"""Fase D: estornos e devolucoes (KPIs do dashboard).

Porta das regras de DBProDash (estudo 12):
  - `dbo.vwListagemDeEstornos`               -> core.estornos_itens
                                                 marts.estornos_{diario,mensal}
  - `dbo.vwListagemDeEntradasSaidasPorCFOP`  -> core.devolucoes_documentos
                                                 marts.devolucoes_{diario,mensal}

Estornos (conferido no ERP: 44 linhas, 24 pedidos, 18 meses iguais):
    Fat_Pedido (Flag_Emitido='1', Tipo_Pedido='4', Empresa='13')
    * Fat_Itens_Pedido (Base_Calc<>'-')
    * Fat_Nat_Pedido com LTrim(RTrim(NatOp))+Seq IN ('1.1021','2.1021')

Devolucoes (`uspDevolucao`): Soma(Vr_CONtabil) das naturalezas de devolucao
    Nova_CFOP IN ('1.201-1','1.201-2','1.202-1','2.202-1'), por mes de Data_Entrada (E)
    e Data_Saida (S). A chave natural de Liv_Entradas/Liv_Saidas inclui
    Tipo_Fornec + Fornecedor (o mesmo numero de documento e reaproveitado por
    fornecedores diferentes).

Revision ID: 0008_marts_estornos
Revises: 0007_core_financeiro
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_marts_estornos"
down_revision: str | None = "0007_core_financeiro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_ESTORNOS_ITENS = """
CREATE OR REPLACE VIEW core.estornos_itens AS
SELECT
    tp.empresa,
    tp.pedido,
    i.item,
    tp.data_emissao                                       AS emissao,
    tp.nr_nota,
    tp.serie,
    tp.vr_nota,
    i.vr_total,
    i.acres_desc,
    i.cod_produto,
    i.qtde,
    i.metros,
    tp.cliente
FROM raw.fat_pedido tp
JOIN raw.fat_itens_pedido i
    ON i.empresa = tp.empresa
   AND i.pedido = tp.pedido
JOIN raw.fat_nat_pedido np
    ON np.empresa = tp.empresa
   AND np.pedido = tp.pedido
WHERE tp.flag_emitido = '1'
  AND tp.tipo_pedido = '4'
  AND tp.empresa = '13'
  AND i.base_calc <> '-'
  AND btrim(np.nat_op) || np.seq IN ('1.1021', '2.1021')
"""

VIEW_DEVOLUCOES = """
CREATE OR REPLACE VIEW core.devolucoes_documentos AS
SELECT * FROM (
    SELECT
        'E'::text                                             AS tipo,
        n.empresa,
        n.documento,
        n.serie,
        n.tipo_fornec,
        n.fornecedor,
        n.nat_op || '-' || n.seq                               AS nova_cfop,
        e.data_entrada                                         AS data,
        coalesce(n.vr_contabil, 0)                             AS vr_contabil
    FROM raw.liv_ent_nat_op n
    JOIN raw.liv_entradas e
        ON e.empresa = n.empresa
       AND e.documento = n.documento
       AND e.serie = n.serie
       AND e.tipo_fornec = n.tipo_fornec
       AND e.fornecedor = n.fornecedor
    UNION ALL
    SELECT
        'S'::text,
        n.empresa,
        n.documento,
        n.serie,
        NULL::text,
        NULL::text,
        n.nat_op || '-' || n.seq,
        s.data_saida,
        coalesce(n.vr_contabil, 0)
    FROM raw.liv_sai_nat_op n
    JOIN raw.liv_saidas s
        ON s.empresa = n.empresa
       AND s.documento = n.documento
       AND s.serie = n.serie
) d
WHERE d.nova_cfop IN ('1.201-1', '1.201-2', '1.202-1', '2.202-1')
"""

VIEW_ESTORNOS_MENSAL = """
CREATE OR REPLACE VIEW marts.estornos_mensal AS
SELECT
    date_trunc('month', emissao)::date      AS mes,
    count(*)                                 AS itens,
    count(DISTINCT pedido)                   AS pedidos,
    count(DISTINCT nr_nota)                  AS notas,
    sum(vr_nota)                            AS valor_nota,
    sum(vr_total)                           AS valor_itens,
    sum(coalesce(acres_desc, 0))             AS acres_desc
FROM core.estornos_itens
WHERE emissao IS NOT NULL
GROUP BY date_trunc('month', emissao)::date
"""

VIEW_ESTORNOS_DIARIO = """
CREATE OR REPLACE VIEW marts.estornos_diario AS
SELECT
    emissao::date                           AS data,
    count(*)                                 AS itens,
    count(DISTINCT pedido)                   AS pedidos,
    count(DISTINCT nr_nota)                  AS notas,
    sum(vr_nota)                            AS valor_nota,
    sum(vr_total)                           AS valor_itens,
    sum(coalesce(acres_desc, 0))             AS acres_desc
FROM core.estornos_itens
WHERE emissao IS NOT NULL
GROUP BY emissao::date
"""

VIEW_DEVOLUCOES_MENSAL = """
CREATE OR REPLACE VIEW marts.devolucoes_mensal AS
SELECT
    date_trunc('month', data)::date          AS mes,
    tipo,
    count(DISTINCT documento)                AS documentos,
    sum(vr_contabil)                        AS valor
FROM core.devolucoes_documentos
WHERE data IS NOT NULL
GROUP BY date_trunc('month', data)::date, tipo
"""

VIEW_DEVOLUCOES_DIARIO = """
CREATE OR REPLACE VIEW marts.devolucoes_diario AS
SELECT
    data::date                              AS data,
    tipo,
    count(DISTINCT documento)                AS documentos,
    sum(vr_contabil)                        AS valor
FROM core.devolucoes_documentos
WHERE data IS NOT NULL
GROUP BY data::date, tipo
"""


def upgrade() -> None:
    op.execute(VIEW_ESTORNOS_ITENS)
    op.execute(
        "COMMENT ON VIEW core.estornos_itens IS "
        "'Porta de DBProDash.dbo.vwListagemDeEstornos: pedidos de estorno (Tipo_Pedido=4) "
        "da empresa 13, natureza 1.102/2.102 seq 1'"
    )

    op.execute(VIEW_DEVOLUCOES)
    op.execute(
        "COMMENT ON VIEW core.devolucoes_documentos IS "
        "'Documentos com natureza de devolucao (1.201-1, 1.201-2, 1.202-1, 2.202-1), "
        "porta de DBProDash.dbo.vwListagemDeEntradasSaidasPorCFOP'"
    )

    op.execute(VIEW_ESTORNOS_MENSAL)
    op.execute(
        "COMMENT ON VIEW marts.estornos_mensal IS "
        "'Estornos por mes (porta de uspEstorno: Soma(Vr_Nota))'"
    )

    op.execute(VIEW_ESTORNOS_DIARIO)
    op.execute(
        "COMMENT ON VIEW marts.estornos_diario IS 'Estornos por dia de emissao'"
    )

    op.execute(VIEW_DEVOLUCOES_MENSAL)
    op.execute(
        "COMMENT ON VIEW marts.devolucoes_mensal IS "
        "'Devolucoes por mes (porta de uspDevolucao: Soma(Vr_CONtabil))'"
    )

    op.execute(VIEW_DEVOLUCOES_DIARIO)
    op.execute(
        "COMMENT ON VIEW marts.devolucoes_diario IS 'Devolucoes por dia de entrada/saida'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS marts.devolucoes_diario")
    op.execute("DROP VIEW IF EXISTS marts.devolucoes_mensal")
    op.execute("DROP VIEW IF EXISTS marts.estornos_diario")
    op.execute("DROP VIEW IF EXISTS marts.estornos_mensal")
    op.execute("DROP VIEW IF EXISTS core.devolucoes_documentos")
    op.execute("DROP VIEW IF EXISTS core.estornos_itens")