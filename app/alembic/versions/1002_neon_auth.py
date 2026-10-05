"""Neon dgbcomex: usuarios da API (JWT + bcrypt) no schema proprio `auth`.

O `public.usuarios` do app dgbcomex guarda a senha em texto claro (D5 do plano), entao a API
mantem o proprio cadastro em `auth.usuario`: hash bcrypt, papeis e empresa. Schema separado para
nao encostar no schema do drizzle.

Revision ID: 1002_neon_auth
Revises: 0011_marts_custos
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

revision: str = "1002_neon_auth"
down_revision: str | None = "0011_marts_custos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _engine():
    from src.db import neon

    return neon.engine()


def upgrade() -> None:
    with _engine().begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS auth"))
        conn.execute(
            sa.text(
                "COMMENT ON SCHEMA auth IS "
                "'Cadastro de autenticacao da API (api-microdata): bcrypt + papeis'"
            )
        )
        conn.execute(
            sa.text(
                """
                CREATE TABLE IF NOT EXISTS auth.usuario (
                    id            serial PRIMARY KEY,
                    email         varchar(180) NOT NULL UNIQUE,
                    nome          varchar(180) NOT NULL DEFAULT '',
                    senha_hash    varchar(120) NOT NULL,
                    papeis        text[] NOT NULL DEFAULT ARRAY['leitura']::text[],
                    empresa       char(2),
                    ativo         boolean NOT NULL DEFAULT true,
                    criado_em     timestamptz NOT NULL DEFAULT now(),
                    ultimo_acesso timestamptz
                )
                """
            )
        )
        conn.execute(
            sa.text(
                "COMMENT ON TABLE auth.usuario IS "
                "'Usuarios da API; senha em bcrypt, papeis controlam o acesso as rotas'"
            )
        )


def downgrade() -> None:
    with _engine().begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS auth.usuario"))
        conn.execute(sa.text("DROP SCHEMA IF EXISTS auth"))