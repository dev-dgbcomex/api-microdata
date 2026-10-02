"""Neon dgbcomex: schemas proprios da API (marts/etl) para os agregados pequenos

Esta migration roda contra o NEON (DATABASE_URL), nao contra o Postgres local: a versao
e controlada pela tabela alembic_version do warehouse local. O schema public do Neon
pertence ao app dgbcomex (drizzle) e nunca e tocado aqui.

Revision ID: 1001_neon_marts
Revises: 0004_marts
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1001_neon_marts"
down_revision: str | None = "0004_marts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _engine():
    from src.db import neon

    return neon.engine()


def upgrade() -> None:
    with _engine().begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS marts"))
        conn.execute(
            sa.text(
                "COMMENT ON SCHEMA marts IS "
                "'Agregados pequenos de dashboard produzidos on-demand pela API (api-microdata)'"
            )
        )
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS etl"))
        conn.execute(
            sa.text("COMMENT ON SCHEMA etl IS 'Defasagem/status das publicacoes da API no Neon'")
        )
        sa.Table(
            "marts_publicados",
            sa.MetaData(),
            sa.Column("mart", sa.Text(), primary_key=True),
            sa.Column("motivo", sa.Text(), nullable=False),
            sa.Column("linhas", sa.Integer()),
            sa.Column("ultima_exec_etl", sa.TIMESTAMP(timezone=True)),
            sa.Column("publicado_em", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
            schema="etl",
        ).create(conn, checkfirst=True)


def downgrade() -> None:
    with _engine().begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS etl.marts_publicados"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS marts CASCADE"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS etl CASCADE"))