"""schema marts: views de consumo da API local (preenchido na Fase D)

Revision ID: 0004_marts
Revises: 0003_core
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_marts"
down_revision: str | None = "0003_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OBJETOS_PREVISTOS = (
    "marts.faturamento_diario",
    "marts.contas_pagas_diario",
    "marts.descontos_diario",
    "marts.devolucoes_diario",
    "marts.estornos_diario",
    "marts.custos_por_departamento_mensal",
    "marts.financeiro_receber_programado",
    "marts.financeiro_pagar_programado",
    "marts.dashboard_diario",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS marts")
    op.execute(
        "COMMENT ON SCHEMA marts IS 'Views/materialized views de consumo da API (Fase D)'"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS marts CASCADE")