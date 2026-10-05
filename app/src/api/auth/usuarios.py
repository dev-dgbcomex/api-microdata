"""Usuarios da API: cadastro, busca e conference de senha em `auth.usuario` (Neon)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from src.api.auth import senhas
from src.db import neon

PAPEL_ADMIN = "admin"
PAPEL_LEITURA = "leitura"


@dataclass(frozen=True)
class Usuario:
    id: int
    email: str
    nome: str
    papeis: list[str]
    empresa: str | None
    ativo: bool

    @property
    def eh_admin(self) -> bool:
        return PAPEL_ADMIN in self.papeis


def _linha(registro: dict[str, object]) -> Usuario:
    return Usuario(
        id=int(registro["id"]),  # type: ignore[arg-type]
        email=str(registro["email"]),
        nome=str(registro["nome"] or ""),
        papeis=list(registro["papeis"] or []),
        empresa=(str(registro["empresa"]).strip() or None) if registro["empresa"] else None,
        ativo=bool(registro["ativo"]),
    )


def por_email(email: str) -> Usuario | None:
    sql = text(
        "select id, email, nome, papeis, empresa, ativo from auth.usuario where email = :email"
    )
    with neon.engine().connect() as conn:
        registro = conn.execute(sql, {"email": email.strip().lower()}).mappings().first()
    return _linha(dict(registro)) if registro else None


def por_id(usuario_id: int) -> Usuario | None:
    sql = text(
        "select id, email, nome, papeis, empresa, ativo from auth.usuario where id = :id"
    )
    with neon.engine().connect() as conn:
        registro = conn.execute(sql, {"id": usuario_id}).mappings().first()
    return _linha(dict(registro)) if registro else None


def salvar(
    *,
    email: str,
    senha: str,
    nome: str = "",
    papeis: list[str] | None = None,
    empresa: str | None = None,
    ativo: bool = True,
) -> Usuario:
    """Cria ou atualiza o usuario pelo email (a senha e sempre reescrita em bcrypt)."""
    limpo = email.strip().lower()
    hash_armazenado = senhas.gerar(senha)
    valores = {
        "email": limpo,
        "nome": nome.strip(),
        "hash": hash_armazenado,
        "papeis": papeis or [PAPEL_LEITURA],
        "empresa": (empresa or "").strip() or None,
        "ativo": ativo,
    }
    with neon.engine().begin() as conn:
        conn.execute(
            text(
                "insert into auth.usuario (email, nome, senha_hash, papeis, empresa, ativo) "
                "values (:email, :nome, :hash, :papeis, :empresa, :ativo) "
                "on conflict (email) do update set nome = excluded.nome, "
                "senha_hash = excluded.senha_hash, papeis = excluded.papeis, "
                "empresa = excluded.empresa, ativo = excluded.ativo"
            ),
            valores,
        )
    usuario = por_email(limpo)
    assert usuario is not None  # acabou de gravar
    return usuario


def autenticar(email: str, senha: str) -> Usuario | None:
    """Confere a senha e marca o ultimo acesso; `None` quando nao bate ou esta inativo."""
    sql = text("select id, senha_hash, ativo from auth.usuario where email = :email")
    with neon.engine().connect() as conn:
        registro = conn.execute(sql, {"email": email.strip().lower()}).mappings().first()
    if not registro or not registro["ativo"]:
        return None
    if not senhas.conferir(senha, str(registro["senha_hash"])):
        return None
    with neon.engine().begin() as conn:
        conn.execute(
            text("update auth.usuario set ultimo_acesso = :agora where id = :id"),
            {"agora": datetime.now(UTC), "id": registro["id"]},
        )
    return por_id(int(registro["id"]))


__all__ = [
    "PAPEL_ADMIN",
    "PAPEL_LEITURA",
    "Usuario",
    "autenticar",
    "por_email",
    "por_id",
    "salvar",
]