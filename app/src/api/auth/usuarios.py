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
    escopos: list[str]

    @property
    def eh_admin(self) -> bool:
        return PAPEL_ADMIN in self.papeis


COLUNAS = "id, email, nome, papeis, empresa, ativo, escopos"


def _linha(registro: dict[str, object]) -> Usuario:
    return Usuario(
        id=int(registro["id"]),  # type: ignore[arg-type]
        email=str(registro["email"]),
        nome=str(registro["nome"] or ""),
        papeis=list(registro["papeis"] or []),
        empresa=(str(registro["empresa"]).strip() or None) if registro["empresa"] else None,
        ativo=bool(registro["ativo"]),
        escopos=list(registro["escopos"] or []),
    )


def por_email(email: str) -> Usuario | None:
    sql = text(f"select {COLUNAS} from auth.usuario where email = :email")
    with neon.engine().connect() as conn:
        registro = conn.execute(sql, {"email": email.strip().lower()}).mappings().first()
    return _linha(dict(registro)) if registro else None


def por_id(usuario_id: int) -> Usuario | None:
    sql = text(f"select {COLUNAS} from auth.usuario where id = :id")
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
    escopos: list[str] | None = None,
) -> Usuario:
    """Cria ou atualiza o usuario pelo email (a senha e sempre reescrita em bcrypt).

    `escopos` sao os nomes das rotas liberadas (`src.api.auth.escopos.ESCOPOS_VALIDOS`); o
    padrao e nenhum, que e falha fechada — quem so vai ler passa `--escopos` no CLI.
    """
    limpo = email.strip().lower()
    hash_armazenado = senhas.gerar(senha)
    desconhecidos = sorted(set(escopos or []) - escopos_validos())
    if desconhecidos:
        raise ValueError(f"escopo desconhecido: {', '.join(desconhecidos)}")
    valores = {
        "email": limpo,
        "nome": nome.strip(),
        "hash": hash_armazenado,
        "papeis": papeis or [PAPEL_LEITURA],
        "empresa": (empresa or "").strip() or None,
        "ativo": ativo,
        "escopos": escopos or [],
    }
    with neon.engine().begin() as conn:
        conn.execute(
            text(
                "insert into auth.usuario "
                "(email, nome, senha_hash, papeis, empresa, ativo, escopos) "
                "values (:email, :nome, :hash, :papeis, :empresa, :ativo, :escopos) "
                "on conflict (email) do update set nome = excluded.nome, "
                "senha_hash = excluded.senha_hash, papeis = excluded.papeis, "
                "empresa = excluded.empresa, ativo = excluded.ativo, "
                "escopos = excluded.escopos"
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


def escopos_validos() -> frozenset[str]:
    """Escopos aceitos no cadastro. Import local: `escopos` depende deste modulo."""
    from src.api.auth.escopos import CURINGA, ESCOPOS_VALIDOS

    return ESCOPOS_VALIDOS | {CURINGA}


__all__ = [
    "PAPEL_ADMIN",
    "PAPEL_LEITURA",
    "Usuario",
    "autenticar",
    "escopos_validos",
    "por_email",
    "por_id",
    "salvar",
]