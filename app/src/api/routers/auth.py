"""Autenticacao da API: login e perfil. A senha nunca trafega nem fica em texto claro."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from src.api.auth import tokens, usuarios
from src.api.auth.dependencias import exigir_autenticado
from src.api.auth.usuarios import Usuario
from src.config import get_settings

router = APIRouter(tags=["auth"])


class EntradaLogin(BaseModel):
    email: EmailStr
    senha: str = Field(min_length=1, max_length=200)


class RespostaLogin(BaseModel):
    token: str
    tipo: str = "Bearer"
    expira_em_minutos: int
    papeis: list[str]
    empresa: str | None = None
    escopos: list[str] = []


class Perfil(BaseModel):
    id: int
    email: str
    nome: str
    papeis: list[str]
    empresa: str | None
    admin: bool
    escopos: list[str] = []


@router.post("/auth/login", summary="Troca email e senha por um JWT curto")
def login(entrada: EntradaLogin) -> RespostaLogin:
    usuario = usuarios.autenticar(entrada.email, entrada.senha)
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="email ou senha invalidos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return RespostaLogin(
        token=tokens.emitir(
            usuario_id=usuario.id,
            email=usuario.email,
            papeis=usuario.papeis,
            empresa=usuario.empresa,
        ),
        expira_em_minutos=get_settings().jwt_expire_minutes,
        papeis=usuario.papeis,
        empresa=usuario.empresa,
        escopos=usuario.escopos,
    )


@router.get("/auth/eu", summary="Perfil do usuario do token")
def eu(usuario: Annotated[Usuario | None, Depends(exigir_autenticado)]) -> Perfil:
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="autenticacao desativada neste ambiente; nao ha perfil",
        )
    return Perfil(
        id=usuario.id,
        email=usuario.email,
        nome=usuario.nome,
        papeis=usuario.papeis,
        empresa=usuario.empresa,
        admin=usuario.eh_admin,
        escopos=usuario.escopos,
    )


__all__ = ["router"]