"""schema raw: espelho das fontes do ERP (tabelas geradas pelo introspect)

As tabelas em raw sao criadas a partir do catalogo do DBMicrodata_DGB
(src.etl.extract.introspect), nao em SQL estatico: o ERP tem ~2.300 colunas nas
fontes dos contratos e as mudancas sao aditivas (nunca DROP/ALTER destrutivo).
O registro das fontes vive em etl.fontes e o DDL aplicado em etl.raw_ddl.

Revision ID: 0002_raw
Revises: 0001_etl
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_raw"
down_revision: str | None = "0001_etl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS raw")
    op.execute(
        "COMMENT ON SCHEMA raw IS "
        "'Espelho fiel das fontes do DBMicrodata_DGB (somente o ETL le/escreve)'"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS raw CASCADE")