"""Rotas de operacao (Fase B): health e estado do warehouse/Neon."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Response
from sqlalchemy import text

from src.config import get_settings
from src.db import erp, neon, warehouse
from src.db.postgres import ping

router = APIRouter(tags=["infra"])


def _safely(checagem: Any) -> dict[str, Any]:
    try:
        return checagem()
    except Exception as exc:
        return {"ok": False, "erro": f"{type(exc).__name__}: {exc}"}


@router.get("/health")
def health(response: Response) -> dict[str, Any]:
    settings = get_settings()
    local = _safely(lambda: ping(warehouse.engine()))
    remoto = _safely(lambda: ping(neon.engine()))
    erp_status = _safely(
        lambda: {
            "ok": True,
            "servidor": settings.db_server,
            "banco": settings.db_database,
            "versao": str(erp.scalar("select @@version")).splitlines()[0],
        }
    )
    etl = _safely(lambda: _ultima_execucao())
    publicacao = _safely(lambda: _status_publicacao())
    ok = bool(local.get("ok")) and bool(remoto.get("ok"))
    if not ok:
        response.status_code = 503
    return {
        "ok": ok,
        "servico": "api-microdata",
        "hora": datetime.now(UTC).isoformat(),
        "warehouse_local": local,
        "neon": remoto,
        "erp": erp_status,
        "etl": etl,
        "neon_publicacao": publicacao,
    }


def _status_publicacao() -> dict[str, Any]:
    """Se os agregados do Neon estao defasados em relacao ao warehouse local (D4)."""
    from src.etl import publicar

    settings = get_settings()
    versao = publicar.versao_local()
    defasados, _ = publicar.defasados(publicar.MARTS_DO_DASHBOARD)
    return {
        "ok": True,
        "automatico": settings.neon_publicar_automatico,
        "versao_warehouse": versao,
        "defasados": defasados,
        "publicados": {
            nome: {
                "linhas": registro.get("linhas"),
                "publicado_em": registro.get("publicado_em"),
            }
            for nome, registro in publicar.publicados().items()
        },
    }


def _ultima_execucao() -> dict[str, Any]:
    with warehouse.engine().connect() as conn:
        row = conn.execute(
            text(
                """
                select tabela, tipo, status, inicio, fim, linhas
                  from etl.execucoes
                 order by inicio desc
                 limit 1
                """
            )
        ).mappings().first()
    if row is None:
        return {"ok": True, "execucoes": 0}
    return {"ok": True, "ultima": dict(row)}