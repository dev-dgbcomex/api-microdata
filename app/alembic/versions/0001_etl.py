"""schema etl: controle de carga (watermark, execucoes, erros, fontes)

Revision ID: 0001_etl
Revises:
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_etl"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ESTRATEGIAS = ("full", "watermark", "recarrega_pai", "reconciliacao")
STATUS = ("ok", "erro", "parcial")
TIPOS = ("bootstrap", "incremental", "reconciliacao", "sync_neon")


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS etl")
    op.execute("COMMENT ON SCHEMA etl IS 'Controle do ETL (somente o runner le/escreve)'")

    op.create_table(
        "watermark",
        sa.Column("tabela", sa.Text(), primary_key=True),
        sa.Column("coluna", sa.Text()),
        sa.Column("ultimo_valor", sa.Text()),
        sa.Column("ultima_exec", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.Text()),
        sa.Column("linhas", sa.BigInteger()),
        sa.Column("levou_s", sa.Numeric(12, 3)),
        sa.Column("atualizado_em", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        schema="etl",
    )

    op.create_table(
        "execucoes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("dominio", sa.Text(), nullable=False),
        sa.Column("tabela", sa.Text(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("inicio", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("fim", sa.TIMESTAMP(timezone=True)),
        sa.Column("linhas", sa.BigInteger()),
        sa.Column("duracao_s", sa.Numeric(12, 3)),
        sa.Column("mensagem", sa.Text()),
        sa.Column("host", sa.Text()),
        sa.CheckConstraint(f"tipo in ({', '.join(repr(t) for t in TIPOS)})", name="ck_execucoes_tipo"),
        sa.CheckConstraint(
            f"status in ({', '.join(repr(s) for s in STATUS)})", name="ck_execucoes_status"
        ),
        schema="etl",
    )
    op.create_index("ix_execucoes_tabela", "execucoes", ["tabela", "inicio"], schema="etl")

    op.create_table(
        "erros",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "execucao_id",
            sa.BigInteger(),
            sa.ForeignKey("etl.execucoes.id", ondelete="CASCADE"),
        ),
        sa.Column("nivel", sa.Text(), nullable=False, server_default="erro"),
        sa.Column("tabela", sa.Text()),
        sa.Column("mensagem", sa.Text(), nullable=False),
        sa.Column("detalhe", sa.Text()),
        sa.Column("criado_em", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="etl",
    )

    op.create_table(
        "fontes",
        sa.Column("tabela_erp", sa.Text(), primary_key=True),
        sa.Column("dominio", sa.Text(), nullable=False),
        sa.Column("tabela_raw", sa.Text(), nullable=False),
        sa.Column("chave_natural", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("coluna_watermark", sa.Text()),
        sa.Column("estrategia", sa.Text(), nullable=False),
        sa.Column("pai", sa.Text()),
        sa.Column("ativo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("doc", sa.Text()),
        sa.Column("atualizado_em", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            f"estrategia in ({', '.join(repr(e) for e in ESTRATEGIAS)})", name="ck_fontes_estrategia"
        ),
        schema="etl",
    )

    op.create_table(
        "raw_ddl",
        sa.Column("tabela_erp", sa.Text(), primary_key=True),
        sa.Column("tabela_raw", sa.Text(), nullable=False),
        sa.Column("hash_ddl", sa.Text()),
        sa.Column("colunas", sa.Integer()),
        sa.Column("aplicado_em", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="etl",
    )


def downgrade() -> None:
    op.drop_table("raw_ddl", schema="etl")
    op.drop_table("fontes", schema="etl")
    op.drop_table("erros", schema="etl")
    op.drop_index("ix_execucoes_tabela", table_name="execucoes", schema="etl")
    op.drop_table("execucoes", schema="etl")
    op.drop_table("watermark", schema="etl")
    op.execute("DROP SCHEMA IF EXISTS etl CASCADE")