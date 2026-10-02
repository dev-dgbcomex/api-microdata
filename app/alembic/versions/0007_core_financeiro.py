"""Fase D: financeiro (contas pagas) e titulos em aberto (receber/pagar)

Porta as regras legiveis do ERP:
  - `dbo.VW_Pag_Titulo_Aberto`       -> core.pag_titulo_aberto
  - `dbo.VW_Rec_DuplicatasEmAberto`  -> core.rec_duplicatas_em_aberto
  - `DBProDash.dbo.vwContasPagas`    -> marts.contas_pagas_diario/mensal
  - `DBProDash.dbo.vwFinanceiro*`    -> marts.financeiro_*_programado (espelhos 13/14)

Os espelhos `vwFinanceiroContasPagar/Receber` do dashboard foram conferidos: 122+7 = 129 titulos
e 1017+2 = 1019 duplicatas, ou seja, empresas 13 e 14 sem nenhum outro filtro.

Revision ID: 0007_core_financeiro
Revises: 0006_marts_faturamento
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_core_financeiro"
down_revision: str | None = "0006_marts_faturamento"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_PAG_TITULO_ABERTO = """
CREATE OR REPLACE VIEW core.pag_titulo_aberto AS
SELECT
    p.empresa,
    p.documento,
    p.parcela,
    p.serie,
    nf.data_emissao                                      AS emissao,
    p.vencimento,
    nf.fornecedor,
    nf.tipo_fornec,
    coalesce(nf.vr_total_parcelas, 0)                    AS total_doc,
    coalesce(p.valor, 0)                                 AS valor_parcela,
    coalesce(b.valor_baixas, 0)                          AS valor_baixas,
    coalesce(p.valor, 0) - coalesce(b.valor_baixas, 0)   AS valor_saldo,
    (p.vencimento - current_date)                        AS dias,
    p.codigo_barra,
    p.id_sis_pag,
    nf.referente
FROM raw.nfe_parcelas p
LEFT JOIN (
    SELECT
        empresa,
        documento,
        serie,
        tipo_fornec,
        fornecedor,
        parcela,
        sum(coalesce(valor_liquido, 0)) AS valor_baixas
    FROM raw.pag_baixas
    GROUP BY empresa, documento, serie, tipo_fornec, fornecedor, parcela
) b
    ON b.empresa = p.empresa
   AND b.documento = p.documento
   AND b.serie = p.serie
   AND b.tipo_fornec = p.tipo_fornec
   AND b.fornecedor = p.fornecedor
   AND b.parcela = p.parcela
JOIN raw.nf_entradas nf
    ON nf.empresa = p.empresa
   AND nf.documento = p.documento
   AND nf.serie = p.serie
   AND nf.tipo_fornec = p.tipo_fornec
   AND nf.fornecedor = p.fornecedor
WHERE coalesce(b.valor_baixas, 0) < coalesce(p.valor, 0)
"""

VIEW_REC_DUPLICATAS_ABERTO = """
CREATE OR REPLACE VIEW core.rec_duplicatas_em_aberto AS
SELECT
    np.empresa_parcelas                    AS empresa,
    nr.id_documento,
    nr.cliente_nf                         AS cnpj_cpf,
    cp.razao_nome_cliente                  AS cliente,
    np.documento_parcelas                  AS nota_fiscal,
    np.serie,
    np.parcela_parcelas                    AS desdobro,
    nr.emissao_nf                         AS emissao,
    np.vencimento_parcelas                 AS vencimento,
    np.banco_parcelas                     AS banco,
    np.operacao_parcelas                  AS operacao,
    coalesce(np.valor_parcelas, 0) - coalesce(rb.valor_liquido, 0) AS valor,
    np.valor_parcelas                      AS valor_parcela
FROM raw.notas_fiscais_parcelas np
JOIN raw.notas_fiscais_rec nr
    ON nr.nr_empresa_nf = np.empresa_parcelas
   AND nr.nr_documento_nf = np.documento_parcelas
   AND nr.serie = np.serie
LEFT JOIN raw.clientes_principal cp
    ON cp.codigo_cliente = nr.cliente_nf
LEFT JOIN (
    SELECT
        empresa,
        documento,
        serie,
        parcela,
        sum(coalesce(valor_liquido, 0)) AS valor_liquido
    FROM raw.rec_baixas
    GROUP BY empresa, documento, serie, parcela
) rb
    ON rb.empresa = np.empresa_parcelas
   AND rb.documento = np.documento_parcelas
   AND rb.parcela = np.parcela_parcelas
   AND rb.serie = np.serie
WHERE coalesce(np.valor_parcelas, 0) - coalesce(rb.valor_liquido, 0) > 0
"""

VIEW_CONTAS_PAGAS_DIARIO = """
CREATE OR REPLACE VIEW marts.contas_pagas_diario AS
SELECT
    data_baixa::date                       AS data,
    count(*)                               AS baixas,
    count(DISTINCT documento)              AS documentos,
    count(DISTINCT fornecedor)             AS fornecedores,
    sum(coalesce(valor_pago, 0))           AS valor_pago,
    sum(coalesce(juros_pagos, 0))          AS juros_pagos,
    sum(coalesce(desconto_recebido, 0))    AS desconto_recebido,
    sum(coalesce(valor_liquido, 0))        AS valor_liquido
FROM raw.pag_baixas
WHERE data_baixa IS NOT NULL
GROUP BY data_baixa::date
"""

VIEW_CONTAS_PAGAS_MENSAL = """
CREATE OR REPLACE VIEW marts.contas_pagas_mensal AS
SELECT
    date_trunc('month', data_baixa)::date  AS mes,
    count(*)                               AS baixas,
    count(DISTINCT documento)              AS documentos,
    count(DISTINCT fornecedor)             AS fornecedores,
    sum(coalesce(valor_pago, 0))           AS valor_pago,
    sum(coalesce(juros_pagos, 0))          AS juros_pagos,
    sum(coalesce(valor_liquido, 0))        AS valor_liquido
FROM raw.pag_baixas
WHERE data_baixa IS NOT NULL
GROUP BY date_trunc('month', data_baixa)::date
"""

# Espelho de DBProDash.dbo.vwFinanceiroContasPagar (apenas QtdeDoc, ValorTotal, Vencimento).
VIEW_PAGAR_PROGRAMADO = """
CREATE OR REPLACE VIEW marts.financeiro_pagar_programado AS
SELECT
    documento || '/' || serie              AS qtde_doc,
    valor_saldo                            AS valor_total,
    vencimento
FROM core.pag_titulo_aberto
WHERE empresa = ANY (SELECT empresa FROM core.empresa_faturamento)
"""

VIEW_RECEBER_PROGRAMADO = """
CREATE OR REPLACE VIEW marts.financeiro_receber_programado AS
SELECT
    nota_fiscal                            AS qtde_doc,
    valor                                  AS valor_total,
    vencimento
FROM core.rec_duplicatas_em_aberto
WHERE empresa = ANY (SELECT empresa FROM core.empresa_faturamento)
"""


def upgrade() -> None:
    op.execute(VIEW_PAG_TITULO_ABERTO)
    op.execute(
        "COMMENT ON VIEW core.pag_titulo_aberto IS "
        "'Porta de DBMicrodata.dbo.VW_Pag_Titulo_Aberto: parcelas de NF com saldo > 0'"
    )

    op.execute(VIEW_REC_DUPLICATAS_ABERTO)
    op.execute(
        "COMMENT ON VIEW core.rec_duplicatas_em_aberto IS "
        "'Porta de DBMicrodata.dbo.VW_Rec_DuplicatasEmAberto: duplicatas com saldo > 0'"
    )

    op.execute(VIEW_CONTAS_PAGAS_DIARIO)
    op.execute(
        "COMMENT ON VIEW marts.contas_pagas_diario IS "
        "'Baixas de Pag_Baixas por dia "
        "(porta de DBProDash.dbo.vwContasPagas/uspListagemBaixasPagar)'"
    )

    op.execute(VIEW_CONTAS_PAGAS_MENSAL)
    op.execute(
        "COMMENT ON VIEW marts.contas_pagas_mensal IS "
        "'Baixas de Pag_Baixas por mes (porta de uspListagemBaixasPagar)'"
    )

    op.execute(VIEW_PAGAR_PROGRAMADO)
    op.execute(
        "COMMENT ON VIEW marts.financeiro_pagar_programado IS "
        "'Titulos a pagar em aberto, empresas 13/14 (porta de vwFinanceiroContasPagar); "
        "a janela de vencimento e aplicada pela API (1o dia do mes seguinte ate 2050-12-31)'"
    )

    op.execute(VIEW_RECEBER_PROGRAMADO)
    op.execute(
        "COMMENT ON VIEW marts.financeiro_receber_programado IS "
        "'Duplicatas a receber em aberto, empresas 13/14 (porta de vwFinanceiroContasReceber); "
        "a janela de vencimento e aplicada pela API'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS marts.financeiro_receber_programado")
    op.execute("DROP VIEW IF EXISTS marts.financeiro_pagar_programado")
    op.execute("DROP VIEW IF EXISTS marts.contas_pagas_mensal")
    op.execute("DROP VIEW IF EXISTS marts.contas_pagas_diario")
    op.execute("DROP VIEW IF EXISTS core.rec_duplicatas_em_aberto")
    op.execute("DROP VIEW IF EXISTS core.pag_titulo_aberto")