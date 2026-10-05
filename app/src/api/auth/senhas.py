"""Senhas: hash e conferencia com bcrypt (nunca texto claro, ao contrario do `public.usuarios`)."""

from __future__ import annotations

import bcrypt

MINIMO = 8


def gerar(senha: str) -> str:
    if len(senha) < MINIMO:
        raise ValueError(f"senha deve ter ao menos {MINIMO} caracteres")
    return bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def conferir(senha: str, hash_armazenado: str) -> bool:
    if not senha or not hash_armazenado:
        return False
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_armazenado.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False


__all__ = ["MINIMO", "conferir", "gerar"]