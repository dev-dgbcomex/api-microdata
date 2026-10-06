"""CLI do api-microdata: introspeccao do ERP, checagem de watermark e saude das bases."""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from time import monotonic
from typing import Any

from src.config import get_settings
from src.db import erp, neon, warehouse
from src.db.postgres import ping
from src.etl import bootstrap, pipeline, publicar, sources
from src.etl.extract import introspect, reader
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
        engine = warehouse.engine()
        if args.recriar:
            print("recriando o schema raw...")
            introspect.recriar(engine)
        aplicados = introspect.aplicar(engine, selecionadas)
        for fonte, comandos in aplicados:
            print(f"  {fonte.tabela_erp:<26} {len(comandos):>4} comando(s)")
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

    if args.dry_run:
        colunas_erp, _, _, _ = pipeline._plano(fonte)
        pagina = next(reader.extrair(fonte.tabela_erp, colunas_erp, limite=args.limite), [])
        print(json.dumps(pagina[: args.amostra], indent=2, ensure_ascii=False, default=str))
        return 0

    def progresso(total: int) -> None:
        print(f"  {total} linha(s) carregada(s)...")

    resultado = pipeline.carregar(
        engine,
        fonte,
        limite=args.limite,
        incremental=args.incremental,
        recarrega_pai=not args.sem_pai,
        gravar_watermark=not args.sem_watermark,
        progresso=progresso,
    )
    for aviso in resultado.avisos:
        print(f"aviso: {aviso}")
    if resultado.watermark is not None:
        print(f"watermark gravado: {resultado.watermark}")

    no_erp, no_raw = pipeline.conferer(engine, fonte)
    print(
        f"raw.{resultado.fonte.destino}: {no_raw} linha(s) "
        f"(ERP {no_erp}, desta execucao {resultado.linhas}, "
        f"{resultado.documentos} documento(s) relidos)"
    )
    if no_erp is not None and no_erp != no_raw:
        print(f"ATENCAO: contagem divergente (ERP {no_erp} x raw {no_raw})")
        return 1
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


def cmd_bootstrap(args: argparse.Namespace) -> int:
    engine = warehouse.engine()
    if args.pai:
        selecionadas = [
            f for f in sources.FONTES if f.pai == args.pai or f.tabela_erp == args.pai
        ]
    elif args.tudo or args.dominio:
        selecionadas = list(sources.por_dominio(args.dominio))
    else:
        print(
            f"informe --dominio, --tudo ou --pai "
            f"(evita carga acidental das {len(sources.FONTES)} fontes)"
        )
        return 2

    inicio = monotonic()
    carregadas = 0
    print(f"fontes: {len(selecionadas)}")

    def progresso(tabela: str, linhas: int) -> None:
        nonlocal carregadas
        carregadas += linhas
        print(f"  {tabela:<24} {linhas:>9,} linha(s)  (total {carregadas:,})".replace(",", "."))

    relatorio = bootstrap.executar(
        engine,
        selecionadas,
        limite=args.limite,
        incremental=args.incremental,
        conferir=not args.sem_conferencia,
    )

    print("\n-- conferencia ERP x raw --")
    divergentes = bootstrap.divergentes(relatorio)
    for linha in relatorio:
        marca = " " if linha not in divergentes else "!"
        print(
            f"{marca}{linha.fonte.tabela_erp:<24} erp={str(linha.linhas_erp):>9} "
            f"raw={str(linha.linhas_raw):>9} {linha.aviso}"
        )
    print(f"\n{round(monotonic() - inicio, 1)}s de execucao")
    if divergentes:
        print(f"{len(divergentes)} tabela(s) divergente(s)", file=sys.stderr)
        return 1
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

    p_boot = sub.add_parser(
        "bootstrap",
        help="carrega as fontes do ERP no raw na ordem de dependencia e reconcilia",
    )
    p_boot.add_argument("--dominio", help="cadastros, faturamento, contas_pagar, ...")
    p_boot.add_argument(
        "--tudo", action="store_true", help=f"carrega todas as {len(sources.FONTES)} fontes"
    )
    p_boot.add_argument("--pai", help="carrega so a fonte informada e os filhos dela")
    p_boot.add_argument("--limite", type=int, help="maximo de linhas por fonte")
    p_boot.add_argument("--incremental", action="store_true", help="usa etl.watermark")
    p_boot.add_argument(
        "--sem-conferencia",
        action="store_true",
        help="nao compara a contagem do ERP com a do raw",
    )
    p_boot.set_defaults(func=cmd_bootstrap)

    p_usuario = sub.add_parser(
        "usuario", help="cria/atualiza um usuario da API em auth.usuario (Neon)"
    )
    p_usuario.add_argument("--email", help="omitido com --listar")
    p_usuario.add_argument("--senha", help="omitida, gera uma aleatoria e mostra uma vez")
    p_usuario.add_argument("--nome", default="")
    p_usuario.add_argument(
        "--papeis", default="leitura", help="separados por virgula (ex.: leitura,admin)"
    )
    p_usuario.add_argument("--empresa", help="codigo char(2) que o token carrega")
    p_usuario.add_argument(
        "--escopos",
        default="",
        help="rotas liberadas, separadas por virgula (ex.: "
        "estoque:leitura,faturamento:leitura,financeiro:leitura); padrao e nenhuma",
    )
    p_usuario.add_argument(
        "--inativo", action="store_true", help="cadastra sem poder entrar"
    )
    p_usuario.add_argument(
        "--listar", action="store_true", help="so lista os usuarios cadastrados"
    )
    p_usuario.set_defaults(func=cmd_usuario)

    p_publicar = sub.add_parser(
        "publicar-neon",
        help="publica os agregados pequenos (marts) do warehouse no Neon",
    )
    p_publicar.add_argument(
        "--mart",
        action="append",
        choices=sorted(publicar.COLUNAS),
        help="repete para varios; padrao e todos os do dashboard",
    )
    p_publicar.add_argument(
        "--forcar",
        action="store_true",
        help="publica mesmo sem defasagem detectada",
    )
    p_publicar.add_argument(
        "--status", action="store_true", help="so mostra o que esta publicado no Neon"
    )
    p_publicar.set_defaults(func=cmd_publicar)
    return parser


def cmd_usuario(args: argparse.Namespace) -> int:
    from src.api.auth import usuarios as auth_usuarios

    if args.listar:
        from sqlalchemy import text

        from src.db import neon

        with neon.engine().connect() as conn:
            linhas = conn.execute(
                text(
                    "select id, email, nome, papeis, empresa, ativo, escopos from auth.usuario "
                    "order by id"
                )
            ).fetchall()
        if not linhas:
            print("nenhum usuario cadastrado")
            return 0
        for linha in linhas:
            papeis = ", ".join(linha.papeis or [])
            escopos = ", ".join(linha.escopos or [])
            print(
                f"{linha.id:>4}  {linha.email:<40} {linha.nome:<20} "
                f"[{papeis}] empresa={linha.empresa or '-'} "
                f"escopos=[{escopos or '-'}] "
                f"{'ativo' if linha.ativo else 'INATIVO'}"
            )
        return 0

    papeis = [item.strip() for item in args.papeis.split(",") if item.strip()]
    escopos = [item.strip() for item in args.escopos.split(",") if item.strip()]
    senha = args.senha or secrets.token_urlsafe(12)
    usuario = auth_usuarios.salvar(
        email=args.email,
        senha=senha,
        nome=args.nome,
        papeis=papeis,
        empresa=args.empresa,
        ativo=not args.inativo,
        escopos=escopos,
    )
    print(f"usuario {usuario.id} <{usuario.email}> papeis={usuario.papeis}")
    print(
        f"empresa={usuario.empresa or '(todas)'} "
        f"escopos={', '.join(usuario.escopos) or '(nenhum: 403 em tudo)'}"
    )
    if not args.senha:
        print(f"senha gerada (mostrada uma vez): {senha}")
    return 0


def cmd_publicar(args: argparse.Namespace) -> int:
    mod = publicar

    if args.status:
        atuais = mod.publicados()
        if not atuais:
            print("nenhum mart publicado no Neon")
            return 0
        print(f"{'mart':<34} {'linhas':>7}  publicado_em")
        for nome, registro in sorted(atuais.items()):
            linhas = registro.get("linhas")
            print(
                f"{nome:<34} {linhas if linhas is not None else '-':>7}  "
                f"{registro.get('publicado_em')}"
            )
        return 0

    marts = tuple(args.mart) if args.mart else mod.MARTS_DO_DASHBOARD
    resultado = mod.sincronizar(marts, forcar=args.forcar)
    if not resultado.publicados:
        print(f"nada a publicar ({len(resultado.ignorados)} mart(s) em dia no Neon)")
        return 0
    print(f"publicados no Neon: {', '.join(resultado.publicados)} ({resultado.linhas} linhas)")
    if resultado.ignorados:
        print(f"em dia: {', '.join(resultado.ignorados)}")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
