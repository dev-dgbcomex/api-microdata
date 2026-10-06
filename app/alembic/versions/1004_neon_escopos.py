"""Neon dgbcomex: escopos por rota em `auth.usuario` (D6).

O papel (`admin`/`leitura`) sozinho nao separa as telas: com dois papeis, "leitura" podia ler
tudo e `admin` nada de diferente na leitura. Os escopos dao o corte fino que o ERP fazia com
`Usuario_Acessos` (Sistema x Topico, Estudo 41) e que o produto precisa — um usuario comercial
enxerga faturamento/descontos e nao o programmed financeiro.

A coluna entra com `'{}'` (nenhum escopo) de proposito: e falha fechada. Quem nao tem escopo
recebe 403 nas rotas de negocio, e o `admin` passa em tudo. Como o warehouse tem dados so da
empresa 13, nenhum usuario de producao pode ser criado sem escolher a empresa explicitamente.

Revision ID: 1004_neon_escopos
Revises: 1003_neon_marts_tabelas
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

revision: str = "1004_neon_escopos"
down_revision: str | None = "1003_neon_marts_tabelas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUNA = "escopos"


def _engine():
    from src.db import neon

    return neon.engine()


def upgrade() -> None:
    with _engine().begin() as conn:
        conn.execute(
            sa.text(
                f"ALTER TABLE auth.usuario ADD COLUMN IF NOT EXISTS {COLUNA} "
                "text[] NOT NULL DEFAULT ARRAY[]::text[]"
            )
        )
        conn.execute(
            sa.text(
                "COMMENT ON COLUMN auth.usuario.escopos IS "
                "'Escopos das rotas de negocio (ex.: estoque:leitura, faturamento:leitura, "
                "financeiro:leitura); vazio = nenhum acesso, admin passa em tudo'"
            )
        )


def downgrade() -> None:
    with _engine().begin() as conn:
        conn.execute(sa.text(f"ALTER TABLE auth.usuario DROP COLUMN IF EXISTS {COLUNA}"))