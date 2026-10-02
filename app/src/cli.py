"""CLI do api-microdata: introspeccao do ERP, checagem de watermark e saude das bases."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from src.config import get_settings
from src.db import erp, neon, warehouse
from src.db.postgres import ping
from src.etl import audit, sources
from src.etl.extract import introspect, reader
from src.etl.load import upsert
from src.etl.transform import values
from src.etl.transform.naming import snake_case


def _ident(nome: str) -> str:
    return f"[{nome}]"


def cmd_fontes(args: argparse.Namespace) -> int:
    linhas = []
    for fonte in sources.por_dominio(args.dominio):
        linhas.append(
            {
                "dominio": fonte.dominio,
                "tabela_erp": fonte.tabela_erp,
                "tabela_raw": f"raw.{fonte.destino}",
                "chave": ",".join(fonte.chave_natural),
                "watermark": fonte.coluna_watermark or "-",
                "estrategia": fonte.estrategia,
                "pai": fonte.pai or "-",
                "linhas_erp": fonte.linhas_erp if fonte.linhas_erp is not None else "-",
            }
        )
    print(json.dumps(linhas, indent=2, ensure_ascii=False))
    return 0


def _chave_do_pai_ausente(fonte: Any) -> list[str]:
    """Colunas da chave do pai que o filho nao tem (impedem recarregar por pai)."""
    faltando = (snake_case(c) for c in fonte.colunas_chave_pai())
    disponiveis = {snake_case(c.nome) for c in introspect.colunas_erp(fonte.tabela_erp)}
    return [coluna for coluna in faltando if coluna not in disponiveis]


def cmd_check(args: argparse.Namespace) -> int:
    relatorio: list[dict[str, Any]] = []
    invalidas: list[str] = []
    minimo = args.min_cobertura
    for fonte in sources.por_dominio(args.dominio):
        item: dict[str, Any] = {"tabela": fonte.tabela_erp, "estrategia": fonte.estrategia}
        if fonte.coluna_watermark:
            coluna = _ident(fonte.coluna_watermark)
            tabela = _ident(fonte.tabela_erp)
            sql = (
                f"select count(*) as total, "
                f"sum(case when {coluna} is null then 1 else 0 end) as nulos, "
                f"max({coluna}) as maximo, min({coluna}) as minimo "
                f"from {tabela}"
            )
            row = erp.query(sql)[0]
            cobertura = (
                round(100 * (1 - (row["nulos"] or 0) / row["total"]), 2) if row["total"] else 0.0
            )
            item |= {
                "watermark": fonte.coluna_watermark,
                "total": row["total"],
                "nulos": row["nulos"],
                "min": str(row["minimo"]) if row["minimo"] is not None else None,
                "max": str(row["maximo"]) if row["maximo"] is not None else None,
                "cobertura_pct": cobertura,
            }
            if row["maximo"] is None or cobertura < minimo:
                invalidas.append(
                    f"{fonte.tabela_erp}.{fonte.coluna_watermark} "
                    f"({cobertura}% preenchido, max={row['maximo']})"
                )
                item["invalida"] = True
        else:
            item |= {"watermark": None, "pai": fonte.pai}
            if fonte.estrategia == "recarrega_pai" and not args.sem_pai:
                faltando = _chave_do_pai_ausente(fonte)
                if faltando:
                    invalidas.append(
                        f"{fonte.tabela_erp} nao tem a chave do pai "
                        f"({fonte.pai}): {', '.join(faltando)}"
                    )
                    item["invalida"] = True
        relatorio.append(item)
    print(json.dumps(relatorio, indent=2, ensure_ascii=False))
    if invalidas:
        print(
            f"\n{len(invalidas)} watermark(s) inutilizavel(is) (minimo {minimo}%): "
            f"mude para full/recarrega_pai em sources.py",
            file=sys.stderr,
        )
        for item in invalidas:
            print(f"  - {item}", file=sys.stderr)
        return 1
    return 0


def cmd_introspect(args: argparse.Namespace) -> int:
    selecionadas = introspect.selecionar(args.dominio, args.tabela)
    planos = []
    for fonte in selecionadas:
        colunas, chave = introspect.planejar(fonte)
        planos.append((fonte, colunas, chave))
        print(
            f"{fonte.tabela_erp:<26} -> raw.{fonte.destino:<26} "
            f"{len(colunas):>4} colunas  chave={','.join(chave)}"
        )

    if args.apply:
        with warehouse.engine().begin() as conn:
            if args.recriar:
                print("recriando o schema raw...")
                conn.exec_driver_sql("DROP SCHEMA IF EXISTS raw CASCADE")
                conn.exec_driver_sql("DELETE FROM etl.raw_ddl")
            conn.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS raw")
        for fonte, _, _ in planos:
            resultado = introspect.sincronizar(warehouse.engine(), fonte)
            comandos = resultado["criada"]
            with warehouse.engine().begin() as conn:
                for comando in comandos:
                    conn.exec_driver_sql(comando)
            introspect.registrar_ddl(
                warehouse.engine(), fonte, comandos, resultado["colunas"]
            )
            print(f"  DDL aplicado: {len(comandos)} comando(s)")
        introspect.registrar_fontes(warehouse.engine(), selecionadas)
        print(f"\netl.fontes: {len(selecionadas)} fonte(s) registrada(s)")
        return 0

    if args.sql:
        for fonte, _, _ in planos:
            print(f"\n-- raw.{fonte.destino}")
            print(";\n".join(introspect.criar_tabela_sql(fonte, colunas, chave)) + ";")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    fonte = sources.por_tabela(args.tabela)
    engine = warehouse.engine()
    colunas_pg, chave_pg = introspect.planejar(fonte)
    meta_erp = introspect.colunas_erp(fonte.tabela_erp)
    mapa = {coluna.origem: coluna.nome for coluna in colunas_pg}
    colunas_erp = list(mapa)
    colunas_raw = [mapa[coluna] for coluna in colunas_erp]

    desde = None
    if args.incremental and fonte.coluna_watermark:
        desde = audit.get_watermark(engine, fonte.tabela_erp)
        print(f"watermark de {fonte.tabela_erp}: {desde}")

    if args.dry_run:
        pagina = next(reader.extrair(fonte.tabela_erp, colunas_erp, limite=args.limite), [])
        print(json.dumps(pagina[: args.amostra], indent=2, ensure_ascii=False, default=str))
        return 0

    tipo = "incremental" if desde is not None else (
        "reconciliacao" if fonte.estrategia in {"full", "reconciliacao"} else "bootstrap"
    )
    recarrega_pai = fonte.estrategia == "recarrega_pai" and fonte.pai and not args.sem_pai
    chave_pai = [snake_case(coluna) for coluna in fonte.colunas_chave_pai()]
    pais_apagados: set[tuple[Any, ...]] = set()
    total = 0
    with audit.execucao(
        engine,
        dominio=fonte.dominio,
        tabela=fonte.tabela_erp,
        tipo=tipo,
        parcial=args.limite is not None,
    ) as reg:
        for pagina in reader.extrair(
            fonte.tabela_erp,
            colunas_erp,
            coluna_watermark=fonte.coluna_watermark if desde is not None else None,
            desde=desde,
            limite=args.limite,
        ):
            linhas = []
            for pagina_linha in pagina:
                limpa = values.linha_limpa(meta_erp, pagina_linha)
                linhas.append(
                    {colunas_raw[i]: limpa.get(coluna) for i, coluna in enumerate(colunas_erp)}
                )
            if recarrega_pai:
                documentos: dict[tuple[Any, ...], dict[str, Any]] = {}
                for linha in linhas:
                    documentos.setdefault(tuple(linha[c] for c in chave_pai), linha)
                novos = [v for k, v in documentos.items() if k not in pais_apagados]
                pais_apagados.update(documentos)
                if novos:
                    apagadas = upsert.apagar_documentos(engine, fonte.destino, chave_pai, novos)
                    print(f"  {apagadas} linha(s) de {len(novos)} documento(s) substituida(s)")
            total += upsert.upsert(engine, fonte.destino, chave_pg, colunas_raw, linhas)
            print(f"  {total} linha(s) carregada(s)...")
        reg.linhas = total
        reg.mensagem = f"colunas={len(colunas_raw)}"

    if fonte.coluna_watermark and not args.sem_watermark:
        maximo = erp.scalar(f"select max([{fonte.coluna_watermark}]) from [{fonte.tabela_erp}]")
        if args.limite is not None:
            print(
                f"carga parcial (--limite {args.limite}); watermark NAO gravado "
                "para nao pular linhas na proxima execucao"
            )
        elif maximo is None:
            print(f"aviso: max({fonte.coluna_watermark}) e nulo; watermark nao gravado")
        else:
            audit.set_watermark(
                engine,
                fonte.tabela_erp,
                fonte.coluna_watermark,
                maximo,
                linhas=total,
            )
            print(f"watermark gravado: {maximo}")

    with engine.connect() as conn:
        gravadas = conn.exec_driver_sql(f"select count(*) from raw.{fonte.destino}").scalar_one()
    print(f"raw.{fonte.destino}: {gravadas} linha(s) no warehouse (desta execucao: {total})")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    settings = get_settings()
    resultado: dict[str, Any] = {
        "servidor_erp": settings.db_server,
        "banco_erp": settings.db_database,
    }
    for nome, checagem in (
        ("warehouse_local", lambda: ping(warehouse.engine())),
        ("neon", lambda: ping(neon.engine())),
        ("erp", lambda: erp.query("select db_name() as banco, suser_sname() as usuario")),
    ):
        try:
            resultado[nome] = checagem()
        except Exception as exc:
            resultado[nome] = {"ok": False, "erro": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(resultado, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="api-microdata", description=__doc__)
    sub = parser.add_subparsers(dest="comando", required=True)

    p_fontes = sub.add_parser("fontes", help="lista o registro de fontes (ERP -> raw)")
    p_fontes.add_argument("--dominio")
    p_fontes.set_defaults(func=cmd_fontes)

    p_check = sub.add_parser("check", help="confere cobertura/max das colunas de watermark no ERP")
    p_check.add_argument("--dominio")
    p_check.add_argument(
        "--min-cobertura",
        type=float,
        default=95.0,
        help="cobertura minima aceitavel na coluna de watermark (%%)",
    )
    p_check.set_defaults(func=cmd_check)
    p_check.add_argument(
        "--sem-pai",
        action="store_true",
        help="nao valida se o filho tem as colunas da chave do pai",
    )

    p_introspect = sub.add_parser(
        "introspect", help="gera (e opcionalmente aplica) o DDL do schema raw"
    )
    p_introspect.add_argument("--dominio")
    p_introspect.add_argument("--tabela")
    p_introspect.add_argument("--sql", action="store_true", help="imprime o DDL sem aplicar")
    p_introspect.add_argument(
        "--apply", action="store_true", help="aplica o DDL no warehouse local"
    )
    p_introspect.add_argument(
        "--recriar",
        action="store_true",
        help="dropa o schema raw e recria tudo (use quando corrigir tipos gerados)",
    )
    p_introspect.set_defaults(func=cmd_introspect)

    p_health = sub.add_parser("health", help="checa ERP + warehouse local + Neon")
    p_health.set_defaults(func=cmd_health)

    p_extract = sub.add_parser(
        "extract", help="carrega uma fonte do ERP no raw (upsert por chave natural)"
    )
    p_extract.add_argument("--tabela", required=True, help="nome da tabela/view no ERP")
    p_extract.add_argument("--limite", type=int, help="maximo de linhas (prova de conceito)")
    p_extract.add_argument(
        "--incremental", action="store_true", help="usa etl.watermark da ultima carga"
    )
    p_extract.add_argument(
        "--dry-run", action="store_true", help="mostra as primeiras linhas do ERP"
    )
    p_extract.add_argument("--amostra", type=int, default=3, help="linhas exibidas no dry-run")
    p_extract.add_argument(
        "--sem-pai",
        action="store_true",
        help="nao apaga os filhos antes de gravar (recarrega_pai)",
    )
    p_extract.add_argument(
        "--sem-watermark",
        action="store_true",
        help="nao grava etl.watermark ao final da carga",
    )
    p_extract.set_defaults(func=cmd_extract)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
