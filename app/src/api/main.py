"""API FastAPI (Fase B: apenas infra; os contratos do legado chegam na Fase E)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import infra
from src.config import get_settings

settings = get_settings()

app = FastAPI(
    title="api-microdata",
    version="0.1.0",
    description=(
        "Substitui a API legada oraculum: le o warehouse Postgres local "
        "(raw/core/marts) e publica agregados pequenos no Neon."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(infra.router)