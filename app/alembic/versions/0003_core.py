"""schema core: regras portadas do DBProDash (preenchido na Fase D)

Revision ID: 0003_core
Revises: 0002_raw
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_core"
down_revision: str | None = "0002_raw"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OBJETOS_PREVISTOS = (
    "core.estoque_pecas_em_aberto",
    "core.sugestao_rolos",
    "core.financeiro_receber_programado",
    "core.financeiro_pagar_programado",
    "core.faturamento_diario",
    "core.contas_pagas_diario",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.execute(
        "COMMENT ON SCHEMA core IS 'Regras de negocio portadas do DBProDash (Fase D)'"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS core CASCADE")