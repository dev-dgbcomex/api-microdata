"""Registro de execução e controle de watermark (schema etl)."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from sqlalchemy import Engine, text

TIPOS = ("bootstrap", "incremental", "reconciliacao", "sync_neon")
STATUS = ("ok", "erro", "parcial")


@dataclass(slots=True)
class Execucao:
    id: int
    tabela: str
    linhas: int = 0
    mensagem: str | None = None


@contextmanager
def execucao(
    engine: Engine,
    *,
    dominio: str,
    tabela: str,
    tipo: str = "incremental",
    parcial: bool = False,
) -> Iterator[Execucao]:
    if tipo not in TIPOS:
        raise ValueError(f"tipo de execucao invalido: {tipo}")
    with engine.begin() as conn:
        registro_id = conn.execute(
            text(
                """
                insert into etl.execucoes (dominio, tabela, tipo, status, inicio, host)
                values (:dominio, :tabela, :tipo, 'ok', now(), :host)
                returning id
                """
            ),
            {"dominio": dominio, "tabela": tabela, "tipo": tipo, "host": socket.gethostname()},
        ).scalar_one()
    registro = Execucao(id=registro_id, tabela=tabela)
    inicio = monotonic()
    try:
        yield registro
    except Exception as exc:
        _finalizar(engine, registro, "erro", inicio, f"{type(exc).__name__}: {exc}")
        registrar_erro(engine, registro, str(exc))
        raise
    _finalizar(engine, registro, "parcial" if parcial else "ok", inicio, registro.mensagem)


def _finalizar(
    engine: Engine,
    registro: Execucao,
    status: str,
    inicio: float,
    mensagem: str | None,
) -> None:
    if status not in STATUS:
        raise ValueError(f"status invalido: {status}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                update etl.execucoes
                   set status = :status, fim = now(), linhas = :linhas,
                       duracao_s = :duracao, mensagem = :mensagem
                 where id = :id
                """
            ),
            {
                "status": status,
                "linhas": registro.linhas,
                "duracao": round(monotonic() - inicio, 3),
                "mensagem": mensagem,
                "id": registro.id,
            },
        )


def registrar_erro(
    engine: Engine,
    registro: Execucao,
    mensagem: str,
    detalhe: str | None = None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                insert into etl.erros (execucao_id, nivel, tabela, mensagem, detalhe)
                values (:execucao_id, 'erro', :tabela, :mensagem, :detalhe)
                """
            ),
            {
                "execucao_id": registro.id,
                "tabela": registro.tabela,
                "mensagem": mensagem[:2000],
                "detalhe": detalhe,
            },
        )


def get_watermark(engine: Engine, tabela: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text("select ultimo_valor from etl.watermark where tabela = :t"),
            {"t": tabela},
        ).scalar()


def set_watermark(
    engine: Engine,
    tabela: str,
    coluna: str | None,
    ultimo_valor: str | datetime | None,
    *,
    status: str = "ok",
    linhas: int | None = None,
    duracao_s: float | None = None,
) -> None:
    if ultimo_valor is None:
        raise ValueError(f"watermark de {tabela} e nulo: nao gravar (pularia linhas)")
    valor = ultimo_valor.isoformat() if isinstance(ultimo_valor, datetime) else ultimo_valor
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                insert into etl.watermark
                    (tabela, coluna, ultimo_valor, ultima_exec, status, linhas, levou_s)
                values (:tabela, :coluna, :valor, now(), :status, :linhas, :duracao)
                on conflict (tabela) do update set
                    coluna = excluded.coluna,
                    ultimo_valor = excluded.ultimo_valor,
                    ultima_exec = now(),
                    status = excluded.status,
                    linhas = excluded.linhas,
                    levou_s = excluded.levou_s
                """
            ),
            {
                "tabela": tabela,
                "coluna": coluna,
                "valor": valor,
                "status": status,
                "linhas": linhas,
                "duracao": duracao_s,
            },
        )