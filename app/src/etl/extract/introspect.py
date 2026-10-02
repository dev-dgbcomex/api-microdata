"""Introspecção do ERP: gera/aplica o DDL do schema raw (espelho das fontes).

O ERP é a fonte da verdade do espelho: as colunas são lidas do catálogo do SQL Server e
traduzidas para PostgreSQL (identificadores snake_case sem acento, trim na transformação).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import Engine, text

from src.db import erp
from src.etl import sources
from src.etl.sources import Fonte
from src.etl.transform.naming import identificar, snake_case

COLUNA_CARGA = "_carga_em"

SQL_COLUNAS = """
select
    c.name,
    c.is_nullable,
    c.max_length,
    c.precision,
    c.scale,
    t.name as type_name,
    case when t.is_user_defined = 1 then coalesce(bt.name, t.name)
        else t.name end as base_type_name,
    case when t.is_user_defined = 1 then t.max_length else c.max_length end as base_max_length,
    case when t.is_user_defined = 1 then t.precision else c.precision end as base_precision,
    case when t.is_user_defined = 1 then t.scale else c.scale end as base_scale
from sys.columns c
join sys.types t on t.user_type_id = c.user_type_id
left join sys.types bt on t.is_user_defined = 1 and bt.user_type_id = t.system_type_id
where c.object_id = object_id(?)
order by c.column_id
"""

SQL_TABELA_EXISTE = """
select o.name, o.type_desc
from sys.objects o
where o.name = ? and o.type in ('U', 'V')
"""

SQL_LINHAS = """
select sum(p.rows) as linhas
from sys.partitions p
where p.object_id = object_id(?) and p.index_id in (0, 1)
"""


@dataclass(frozen=True, slots=True)
class ColunaERP:
    nome: str
    tipo: str
    base: str
    tamanho: int | None
    precisao: int | None
    escala: int | None
    nullable: bool


@dataclass(frozen=True, slots=True)
class ColunaPG:
    nome: str
    tipo_sql: str
    origem: str


nome_pg = snake_case

def mapear_tipo(coluna: ColunaERP) -> str:
    base = coluna.base.lower()
    tamanho = coluna.tamanho
    precisao = coluna.precisao
    escala = coluna.escala
    if base in {"char", "varchar", "nchar", "nvarchar", "sysname"}:
        return f"varchar({tamanho})" if tamanho and 0 < tamanho <= 300 else "text"
    if base in {"text", "ntext", "xml", "sql_variant"}:
        return "text"
    if base in {"tinyint", "smallint"}:
        return "smallint"
    if base == "int":
        return "integer"
    if base == "bigint":
        return "bigint"
    if base == "bit":
        return "boolean"
    if base in {"decimal", "numeric"}:
        return f"numeric({precisao},{escala})" if precisao else "numeric"
    if base in {"money", "smallmoney"}:
        return "numeric(19,4)"
    if base == "float":
        return "double precision"
    if base == "real":
        return "real"
    if base == "date":
        return "date"
    if base == "time":
        return "time"
    if base in {"smalldatetime", "datetime", "datetime2"}:
        return "timestamp"
    if base == "datetimeoffset":
        return "timestamptz"
    if base == "uniqueidentifier":
        return "uuid"
    if base in {"binary", "varbinary", "image"}:
        return "bytea"
    return "text"


def normalizar(coluna_erp: ColunaERP) -> ColunaPG:
    return ColunaPG(
        nome=nome_pg(coluna_erp.nome),
        tipo_sql=mapear_tipo(coluna_erp),
        origem=coluna_erp.nome,
    )


TEP2 = {"nchar", "nvarchar"}


def _tamanho_em_caracteres(tipo: str, tamanho: int | None) -> int | None:
    """nvarchar/nchar guardam 2 bytes por caractere; sys.columns devolve bytes."""
    if tamanho is None or tamanho < 0:
        return None
    return tamanho // 2 if tipo.lower() in TEP2 else tamanho


def colunas_erp(tabela_erp: str) -> list[ColunaERP]:
    if not erp.scalar(SQL_TABELA_EXISTE, (tabela_erp,)):
        raise LookupError(f"tabela/view ausente no ERP: {tabela_erp}")
    linhas = erp.query(SQL_COLUNAS, (tabela_erp,))
    colunas: list[ColunaERP] = []
    for row in linhas:
        tipo = str(row["type_name"])
        colunas.append(
            ColunaERP(
                nome=str(row["name"]),
                tipo=tipo,
                base=str(row["base_type_name"]),
                tamanho=_tamanho_em_caracteres(tipo, row["base_max_length"]),
                precisao=int(row["base_precision"]) if row["base_precision"] else None,
                escala=int(row["base_scale"]) if row["base_scale"] else 0,
                nullable=str(row["is_nullable"]) == "YES",
            )
        )
    return colunas


def linhas_erp(tabela_erp: str) -> int | None:
    valor = erp.scalar(SQL_LINHAS, (tabela_erp,))
    return int(valor) if valor is not None else None


def deduplicar(colunas: list[ColunaPG]) -> list[ColunaPG]:
    vistos: dict[str, int] = {}
    saida: list[ColunaPG] = []
    for coluna in colunas:
        total = vistos.get(coluna.nome, 0) + 1
        vistos[coluna.nome] = total
        nome = coluna.nome if total == 1 else f"{coluna.nome}_{total}"
        saida.append(ColunaPG(nome=nome, tipo_sql=coluna.tipo_sql, origem=coluna.origem))
    return saida


def planejar(fonte: Fonte) -> tuple[list[ColunaPG], list[str]]:
    colunas = deduplicar([normalizar(c) for c in colunas_erp(fonte.tabela_erp)])
    nomes = {c.nome for c in colunas}
    faltando = [k for k in fonte.chave_natural if nome_pg(k) not in nomes]
    if faltando:
        raise ValueError(
            f"{fonte.tabela_erp}: chave natural {fonte.chave_natural} nao existe "
            f"(normalizada {faltando})"
        )
    return colunas, [nome_pg(k) for k in fonte.chave_natural]


def _texto(valor: str) -> str:
    return valor.replace("'", "''")


def criar_tabela_sql(fonte: Fonte, colunas: list[ColunaPG], chave: list[str]) -> list[str]:
    destino = identificar(fonte.destino)
    corpo = [f"    {identificar(coluna.nome)} {coluna.tipo_sql}" for coluna in colunas]
    corpo.append(f"    {COLUNA_CARGA} timestamptz DEFAULT now()")
    corpo.append(f"    PRIMARY KEY ({', '.join(identificar(k) for k in chave)})")
    ddl = [f"CREATE TABLE IF NOT EXISTS raw.{destino} (\n" + ",\n".join(corpo) + "\n)"]
    ddl.extend(
        f"COMMENT ON COLUMN raw.{destino}.{identificar(coluna.nome)} "
        f"IS 'ERP: {_texto(coluna.origem)}'"
        for coluna in colunas
    )
    ddl.append(
        f"COMMENT ON TABLE raw.{destino} IS "
        f"'Espelho de dbo.{_texto(fonte.tabela_erp)} ({_texto(fonte.doc)})'"
    )
    return ddl


def _colunas_existentes(engine: Engine, tabela_raw: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'raw' and table_name = :t"
                ),
                {"t": tabela_raw},
            )
        }


def _tabela_existe(engine: Engine, tabela_raw: str) -> bool:
    with engine.connect() as conn:
        return bool(
            conn.execute(
                text("select to_regclass(:t) is not null"),
                {"t": f"raw.{tabela_raw}"},
            ).scalar()
        )


def sincronizar(engine: Engine, fonte: Fonte) -> dict[str, list[str]]:
    """Cria a tabela do espelho e adiciona colunas novas (sempre aditivo, nunca DROP)."""
    colunas, chave = planejar(fonte)
    ddl: list[str] = []
    if not _tabela_existe(engine, fonte.destino):
        ddl.extend(criar_tabela_sql(fonte, colunas, chave))
        return {"criada": ddl, "colunas": [c.nome for c in colunas]}

    existentes = _colunas_existentes(engine, fonte.destino)
    destino = identificar(fonte.destino)
    novas = [c for c in colunas if c.nome not in existentes]
    for coluna in novas:
        ddl.append(
            f"ALTER TABLE raw.{destino} ADD COLUMN {identificar(coluna.nome)} {coluna.tipo_sql}"
        )
        ddl.append(
            f"COMMENT ON COLUMN raw.{destino}.{identificar(coluna.nome)} "
            f"IS 'ERP: {_texto(coluna.origem)}'"
        )
    return {"criada": ddl, "colunas": [c.nome for c in novas]}


def registrar_fontes(engine: Engine, processadas: list[Fonte]) -> None:
    with engine.begin() as conn:
        for fonte in processadas:
            conn.execute(
                text(
                    """
                    insert into etl.fontes
                        (dominio, tabela_erp, tabela_raw, chave_natural, coluna_watermark,
                         estrategia, pai, ativo, doc, atualizado_em)
                    values
                        (:dominio, :tabela_erp, :tabela_raw, :chave_natural, :coluna_watermark,
                         :estrategia, :pai, :ativo, :doc, now())
                    on conflict (tabela_erp) do update set
                        dominio = excluded.dominio,
                        tabela_raw = excluded.tabela_raw,
                        chave_natural = excluded.chave_natural,
                        coluna_watermark = excluded.coluna_watermark,
                        estrategia = excluded.estrategia,
                        pai = excluded.pai,
                        ativo = excluded.ativo,
                        doc = excluded.doc,
                        atualizado_em = now()
                    """
                ),
                {
                    "dominio": fonte.dominio,
                    "tabela_erp": fonte.tabela_erp,
                    "tabela_raw": fonte.destino,
                    "chave_natural": list(fonte.chave_natural),
                    "coluna_watermark": fonte.coluna_watermark,
                    "estrategia": fonte.estrategia,
                    "pai": fonte.pai,
                    "ativo": fonte.ativo,
                    "doc": fonte.doc,
                },
            )


def registrar_ddl(engine: Engine, fonte: Fonte, ddl: list[str], colunas: list[str]) -> None:
    digest = hashlib.sha256("\n".join(ddl).encode()).hexdigest()[:16]
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                insert into etl.raw_ddl (tabela_erp, tabela_raw, hash_ddl, colunas, aplicado_em)
                values (:tabela_erp, :tabela_raw, :hash_ddl, :colunas, now())
                on conflict (tabela_erp) do update set
                    hash_ddl = excluded.hash_ddl,
                    colunas = excluded.colunas,
                    aplicado_em = now()
                """
            ),
            {
                "tabela_erp": fonte.tabela_erp,
                "tabela_raw": fonte.destino,
                "hash_ddl": digest,
                "colunas": len(colunas),
            },
        )


def aplicar(engine: Engine, fontes: list[Fonte]) -> list[tuple[Fonte, list[str]]]:
    resultados: list[tuple[Fonte, list[str]]] = []
    for fonte in fontes:
        plano = sincronizar(engine, fonte)
        with engine.begin() as conn:
            for comando in plano["criada"]:
                conn.execute(text(comando))
        registrar_ddl(engine, fonte, plano["criada"], plano["colunas"])
        resultados.append((fonte, plano["criada"]))
    registrar_fontes(engine, fontes)
    return resultados


def selecionar(dominio: str | None = None, tabela: str | None = None) -> list[Fonte]:
    if tabela:
        return [sources.por_tabela(tabela)]
    return list(sources.por_dominio(dominio))