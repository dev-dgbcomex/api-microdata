"""Dependencias de autenticacao/autorizacao das rotas."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.auth import tokens, usuarios
from src.api.auth.usuarios import PAPEL_ADMIN, Usuario
from src.config import get_settings

esquema = HTTPBearer(auto_error=False)


def exigir_autenticado(
    credencial: Annotated[HTTPAuthorizationCredentials | None, Depends(esquema)],
) -> Usuario | None:
    """Dependencia de router: `None` quando `API_AUTENTICACAO_EXIGIDA=false` (validacao local)."""
    if not get_settings().api_autenticacao_exigida:
        return None
    if credencial is None or not credencial.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="informe o token em `Authorization: Bearer <token>`",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = tokens.ler(credencial.credentials)
    except tokens.TokenInvalido as erro:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"token invalido: {erro}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from erro

    usuario = usuarios.por_id(int(payload["sub"]))
    if usuario is None or not usuario.ativo:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="usuario inexistente ou inativo",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return usuario


# Alias de modulo, declarado **depois** de `exigir_autenticado`. As fabricas (`exigir_papeis`,
# `escopos.exigir_escopo`) o usam na assinatura e nao podem usar um alias local: com
# `from __future__ import annotations` ele viraria um ForwardRef que o FastAPI nao resolve ao
# montar o schema da rota (o erro so aparece quando a fabrica entra em um router de verdade).
UsuarioAutenticado = Annotated[Usuario | None, Depends(exigir_autenticado)]


def exigir_papeis(*papeis: str):
    """Fabrica de dependencia: exige **todos** os papeis informados (admin passa em tudo)."""

    def dependencia(usuario: UsuarioAutenticado) -> Usuario | None:
        if usuario is None:
            return None
        if PAPEL_ADMIN in usuario.papeis or set(papeis) <= set(usuario.papeis):
            return usuario
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"precisa de um destes papeis: {', '.join(papeis)}",
        )

    return dependencia


__all__ = [
    "UsuarioAutenticado",
    "esquema",
    "exigir_autenticado",
    "exigir_papeis",
]