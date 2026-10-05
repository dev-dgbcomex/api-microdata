"""Token JWT (HS256) da API: emissao e leitura, com `sub`, papeis e empresa."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from src.config import get_settings

EMISSOR = "api-microdata"


class TokenInvalido(ValueError):
    """Token ausente, adulterado, expirado ou com outro emissor."""


def emitir(
    *,
    usuario_id: int,
    email: str,
    papeis: list[str],
    empresa: str | None = None,
    expiracao_minutos: int | None = None,
) -> str:
    agora = datetime.now(UTC)
    minutos = expiracao_minutos or get_settings().jwt_expire_minutes
    payload: dict[str, Any] = {
        "iss": EMISSOR,
        "sub": str(usuario_id),
        "email": email,
        "papeis": sorted(papeis),
        "iat": int(agora.timestamp()),
        "exp": int((agora + timedelta(minutes=minutos)).timestamp()),
    }
    if empresa:
        payload["empresa"] = empresa
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def ler(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=EMISSOR,
        )
    except jwt.PyJWTError as erro:
        raise TokenInvalido(str(erro)) from erro
    if not payload.get("sub"):
        raise TokenInvalido("token sem `sub`")
    return payload


__all__ = ["EMISSOR", "TokenInvalido", "emitir", "ler"]