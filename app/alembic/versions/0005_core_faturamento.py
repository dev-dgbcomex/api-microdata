"""Fase D: core.faturamento_itens (regra de faturamento do DBProDash portada)

A view `DBProDash.dbo.vwFaturamento` esta com WITH ENCRYPTION (OBJECT_DEFINITION = NULL).
A regra foi reconstituida pelos estudos 11/12/29 e pela view irma legivel
`DBMicrodata.dbo.Vw_Fat_Saida_Qlik` (mesma whitelist de NatOp/Seq) e validada numero a numero
contra a view legada por `scripts/validar_fase_d.py`.

Revision ID: 0005_core_faturamento
Revises: 1001_neon_marts
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005_core_faturamento"
down_revision: str | None = "1001_neon_marts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 106 pares (NatOp, Seq) extraidos da definicao de dbo.Vw_Fat_Saida_Qlik. A view irma
# `vwFaturamento` usa 104 deles: `5.911/1` e `6.901/1` NAO entram no faturamento do dashboard
# (174 itens / R$ 64.554,73 conferidos item a item contra a view legada).
CFOP_FATURAMENTO: tuple[tuple[str, str], ...] = (
    ("1.101", "1"), ("1.124", "1"), ("1.201", "1"), ("1.201", "2"),
    ("1.202", "1"), ("1.252", "1"), ("1.302", "1"), ("1.352", "1"),
    ("1.407", "1"), ("1.551", "1"), ("1.556", "1"), ("1.604", "1"),
    ("1.653", "1"), ("1.901", "1"), ("1.902", "1"), ("1.903", "1"),
    ("1.906", "1"), ("1.907", "1"), ("1.911", "1"), ("1.924", "1"),
    ("1.949", "1"), ("2.101", "1"), ("2.102", "1"), ("2.124", "1"),
    ("2.201", "1"), ("2.201", "2"), ("2.201", "3"), ("2.202", "1"),
    ("2.352", "1"), ("2.503", "1"), ("2.556", "1"), ("2.901", "1"),
    ("2.902", "1"), ("2.903", "1"), ("2.911", "1"), ("2.949", "1"),
    ("2.999", "1"), ("3.101", "1"), ("3.102", "1"), ("3.551", "1"),
    ("3.556", "1"), ("5.101", "1"), ("5.101", "2"), ("5.101", "3"),
    ("5.101", "4"), ("5.101", "5"), ("5.102", "1"), ("5.105", "1"),
    ("5.107", "1"), ("5.116", "1"), ("5.122", "1"), ("5.123", "1"),
    ("5.124", "1"), ("5.201", "1"), ("5.912", "1"),
    ("5.913", "1"), ("5.915", "2"), ("5.920", "1"), ("5.921", "1"),
    ("5.924", "2"), ("5.949", "1"), ("5.949", "2"), ("6.101", "1"),
    ("6.101", "2"), ("6.101", "4"), ("6.101", "5"), ("6.102", "1"),
    ("6.102", "2"), ("6.102", "3"), ("6.107", "1"), ("6.107", "3"),
    ("6.108", "1"), ("6.110", "1"), ("6.116", "1"), ("6.122", "1"),
    ("6.122", "2"), ("6.122", "3"), ("6.123", "1"), ("6.124", "1"),
    ("6.201", "1"), ("6.201", "2"), ("6.202", "1"), ("6.413", "1"),
    ("6.501", "1"), ("6.551", "1"), ("6.553", "1"), ("6.556", "1"),
    ("6.902", "1"), ("6.903", "1"), ("6.905", "1"),
    ("6.906", "1"), ("6.909", "1"), ("6.911", "2"), ("6.911", "3"),
    ("6.912", "1"), ("6.913", "1"), ("6.914", "1"), ("6.915", "1"),
    ("6.920", "1"), ("6.921", "1"), ("6.924", "2"), ("6.949", "1"),
    ("7.102", "1"), ("7.201", "1"),
)  # fmt: skip

# Empresas do faturamento (Estudo 12 §2.1).
EMPRESAS_FATURAMENTO = ("13", "14")

VIEW_FATURAMENTO_ITENS = """
CREATE OR REPLACE VIEW core.faturamento_itens AS
SELECT
    tp.empresa,
    tp.pedido,
    tp.cliente,
    tp.nosso_pedido,
    ti.cod_produto,
    tp.nr_nota,
    tp.data_nota,
    tp.vr_nota,
    CASE WHEN ti.base_calc IN ('P', 'M') THEN ti.metros ELSE ti.qtde END AS metros,
    ti.vr_unitario,
    ti.vr_total,
    tp.empresa_auxiliar,
    ti.peso,
    ti.acres_desc,
    cp.razao_nome_cliente AS nome_cliente,
    ti.item
FROM raw.fat_pedido tp
JOIN raw.fat_itens_pedido ti
    ON ti.empresa = tp.empresa AND ti.pedido = tp.pedido
JOIN raw.clientes_principal cp
    ON cp.codigo_cliente = tp.cliente
JOIN raw.fat_nat_pedido np
    ON np.empresa = tp.empresa AND np.pedido = tp.pedido
WHERE tp.flag_emitido = '1'
  AND tp.tipo_pedido = '1'
  AND tp.empresa = ANY (SELECT empresa FROM core.empresa_faturamento)
  AND ti.base_calc <> '-'
  AND (np.nat_op, np.seq) IN (SELECT nat_op, seq FROM core.cfop_faturamento)
"""


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS core")

    op.execute(
        """
        CREATE TABLE core.empresa_faturamento (
            empresa text PRIMARY KEY,
            descricao text NOT NULL
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE core.empresa_faturamento IS "
        "'Empresas do faturamento (DBProDash.dbo.vwFaturamento)'"
    )
    op.execute(
        "INSERT INTO core.empresa_faturamento (empresa, descricao) VALUES "
        + ", ".join(f"('{empresa}', 'Ativa')" for empresa in EMPRESAS_FATURAMENTO)
    )

    op.execute(
        """
        CREATE TABLE core.cfop_faturamento (
            nat_op text NOT NULL,
            seq text NOT NULL,
            origem text NOT NULL,
            PRIMARY KEY (nat_op, seq)
        )
        """
    )
    op.execute(
        "COMMENT ON TABLE core.cfop_faturamento IS "
        "'Whitelist de Natureza de Operacao que compoe o faturamento "
        "(104 pares; base = DBMicrodata.dbo.Vw_Fat_Saida_Qlik sem 5.911/1 e 6.901/1, "
        "conferido item a item contra DBProDash.dbo.vwFaturamento)'"
    )
    valores = ", ".join(
        f"('{nat_op}', '{seq}', 'Vw_Fat_Saida_Qlik')" for nat_op, seq in CFOP_FATURAMENTO
    )
    op.execute(
        "INSERT INTO core.cfop_faturamento (nat_op, seq, origem) VALUES " + valores
    )

    op.execute(VIEW_FATURAMENTO_ITENS)
    op.execute(
        "COMMENT ON VIEW core.faturamento_itens IS "
        "'Porta de DBProDash.dbo.vwFaturamento (Fase D): item faturado, "
        "Flag_Emitido=1, Tipo_Pedido=1, Base_Calc<>-, whitelist de CFOP'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS core.faturamento_itens")
    op.execute("DROP TABLE IF EXISTS core.cfop_faturamento")
    op.execute("DROP TABLE IF EXISTS core.empresa_faturamento")