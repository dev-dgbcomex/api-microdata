"""Configuração da aplicação (ERP + warehouse local + Neon)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parents[1]
TZ = "America/Sao_Paulo"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=APP_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    db_server: str
    db_database: str = "DBMicrodata_DGB"
    db_username: str
    db_password: str
    db_driver: str = "ODBC Driver 18 for SQL Server"
    db_isolation: str = "READ UNCOMMITTED"
    db_timeout_s: int = 30

    database_url_local: str
    database_url: str

    api_host: str = "127.0.0.1"
    api_port: int = 58244
    cors_origins: list[str] = ["http://localhost:3000"]

    jwt_secret: str = "trocar-em-producao"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 30
    api_autenticacao_exigida: bool = True

    etl_batch_size: int = 5000
    etl_timezone: str = TZ

    neon_publicar_automatico: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _segredo_usable(self) -> Settings:
        """HS256 com menos de 32 bytes e recusado pelo PyJWT: falha aqui, nao no primeiro login."""
        if self.api_autenticacao_exigida and len(self.jwt_secret.encode()) < 32:
            raise ValueError(
                "JWT_SECRET precisa de ao menos 32 bytes quando a autenticacao esta exigida "
                "(gere com `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`)"
            )
        return self

    @property
    def neon_database_url(self) -> str:
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()