"""Valida a Fase E: cada endpoint da API local x a procedure/vista equivalente do legado.

Roda as rotas via `TestClient` (nenhum HTTP de rede) e compara com `EXEC` das `usp` do
`DBProDash` — as únicas executadas são as da whitelist de `src.db.erp.PROCEDURES_SOMENTE_LEITURA`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.db import erp, warehouse

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOLERANCIA = Decimal("0.01")

MESES_DE_TESTE = (date(2026, 3, 1), date(2025, 7, 15), date(2025, 1, 1))


class Relatorio:
    def __init__(self) -> None:
        self.falhas = 0
        self.total = 0

    def conferir(self, nome: str, meu: object, legado: object, *, json: bool = True) -> None:
        self.total += 1
        if json:
            meu, legado = _dec(meu), _dec(legado)
        igual = abs(meu - legado) <= TOLERANCIA if json else meu == legado
        if igual:
            print(f"  OK   {nome}: {meu}")
        else:
            self.falhas += 1
            print(f"  FALHA {nome}: meu={meu} legado={legado}")


def _dec(valor: object) -> Decimal:
    if isinstance(valor, str):
        valor = valor.strip().replace(",", ".")
    return Decimal(str(valor if valor is not None else 0))


def _primeira(proc: str, params: tuple[object, ...] = ()) -> dict[str, object]:
    linhas = _linhas(proc, params)
    return linhas[0] if linhas else {}


def _linhas(proc: str, params: tuple[object, ...] = ()) -> list[dict[str, object]]:
    consulta = f"exec DBProDash.dbo.{proc} ?" if params else f"exec DBProDash.dbo.{proc}"
    return erp.query(consulta, params)


def _linha_legada(cursor: dict[str, object], nome: str) -> object:
    for chave in cursor:
        if chave.lower() == nome.lower():
            return cursor[chave]
    raise KeyError(f"coluna {nome!r} ausente no resultado: {list(cursor)}")


def _ddmmyyyy(valor: date) -> str:
    """A legado `formatar_data_para_sql` entregava `dd/mm/aaaa` ao ODBC."""
    return valor.strftime("%d/%m/%Y")


def validar_simples(cliente: TestClient) -> Relatorio:
    """Rotas de um único valor mensal/diario x usp de mesmo nome."""
    print("\n== KPIs de valor (uspFaturamento/Dia/Desconto/Devolucao/Estorno/BaixasPagar) ==")
    rel = Relatorio()
    alvos = (
        ("/faturamento/{data}", "uspFaturamento", "Faturamento"),
        ("/faturamento-dia/{data}", "uspFaturamentoDia", "Faturamento"),
        ("/descontos/{data}", "uspDesconto", "Desconto"),
        ("/devolucoes/{data}", "uspDevolucao", "Devolucao"),
        ("/estornos/{data}", "uspEstorno", "Estorno"),
        ("/contas-pagas/{data}", "uspListagemBaixasPagar", "ContasPagas"),
    )
    for dia in MESES_DE_TESTE:
        for rota, proc, coluna in alvos:
            esperado = _linha_legada(_primeira(proc, (_ddmmyyyy(dia),)), coluna)
            obtido = cliente.get(rota.format(data=dia.isoformat()))
            obtido.raise_for_status()
            rel.conferir(f"{proc} {dia:%Y-%m}", obtido.json().get(coluna), esperado)
    return rel


def validar_programados(cliente: TestClient) -> Relatorio:
    """Programados: a janela (1º/mês+1 a 2050-12-31) e o COUNT(DISTINCT) do pagar."""
    print("\n== programmed (uspDashFinanceiroContas*Programado) ==")
    rel = Relatorio()
    for rota, proc, distinto in (
        ("/contas-receber-programado", "uspDashFinanceiroContasReceberProgramado", False),
        ("/contas-pagar-programado", "uspDashFinanceiroContasPagarProgramado", True),
    ):
        legado = _primeira(proc)
        obtido = cliente.get(rota)
        obtido.raise_for_status()
        rel.conferir(f"{proc}.QtdeDoc", obtido.json()["QtdeDoc"], _linha_legada(legado, "QtdeDoc"))
        rel.conferir(
            f"{proc}.ValorTotal",
            obtido.json()["ValorTotal"],
            _linha_legada(legado, "ValorTotal"),
        )
        if distinto:
            brutas = erp.query(
                "select count(*) as linhas, count(distinct cast(QtdeDoc as varchar(60))) as docs "
                "from DBProDash.dbo.vwFinanceiroContasPagar"
            )[0]
            print(
                f"  (contas a pagar: {brutas['linhas']} linhas, "
                f"{brutas['docs']} documentos distintos)"
            )
    return rel


def validar_custos(cliente: TestClient) -> Relatorio:
    """Custos administrativos: uspRel_CCusto_Niveis* + uspCustoAdmArmFat*."""
    print("\n== custos administrativos (uspCustoAdmArmFat) ==")
    rel = Relatorio()
    for rota, proc in (
        ("/custos-administrativos-mensal", "uspCustoAdmArmFatMensal"),
        ("/custos-administrativos-anual", "uspCustoAdmArmFat"),
    ):
        legado = _primeira(proc)
        obtido = cliente.get(rota)
        obtido.raise_for_status()
        for coluna in ("Faturamento", "Administrativo", "Armazenagem"):
            rel.conferir(f"{proc}.{coluna}", obtido.json()[coluna], _linha_legada(legado, coluna))
    return rel


CAMPOS_TEXTO_SUGESTAO = ("Sublote", "Gavetas", "Rolos")


def _tupla_sugestao(linha: dict[str, object]) -> tuple:
    """Mesma tupla para os dois lados: texto sem padding, numérico como Decimal."""
    return (
        str(linha["Produto"]).strip(),
        str(linha["Cor"]).strip(),
        _dec(linha["Qtde_Item"]),
        _dec(linha["Qtde_Saldo"]),
        _dec(linha["Qtde_Pecas"]),
        _dec(linha["Total_Metros"]),
        *(
            str(linha[campo]).strip() if linha[campo] is not None else None
            for campo in CAMPOS_TEXTO_SUGESTAO
        ),
    )


def validar_sugestao_rolos(cliente: TestClient, pedidos: int = 5) -> Relatorio:
    """Sugestão de rolos: /sugestao-rolos/{pedido} x uspEnderecamentoParaAtenderPedidoGeral.

    A comparação é posicional depois de ordenar os dois lados: o mesmo (Produto, Cor) pode
    repetir no pedido e o legado mantém uma linha por item, então (Produto, Cor) sozinho não
    identifica a linha. O legado também preenche `Sublote` até 20 colunas — a comparação de
    texto ignora o padding.
    """
    print("\n== sugestão de rolos (uspEnderecamentoParaAtenderPedidoGeral) ==")
    rel = Relatorio()
    with warehouse.engine().connect() as conn:
        maiores = [
            dict(linha)
            for linha in conn.execute(
                text(
                    """
                    select pedido, count(*) as itens from core.pedido_sugestao_rolos
                     group by pedido order by count(*) desc, pedido limit :limite
                    """
                ),
                {"limite": pedidos},
            ).mappings()
        ]
    nomes_num = ("Qtde_Item", "Qtde_Saldo", "Qtde_Pecas", "Total_Metros")
    for linha in maiores:
        pedido = str(linha["pedido"])
        legado = _linhas("uspEnderecamentoParaAtenderPedidoGeral", (pedido,))
        obtido = cliente.get(f"/sugestao-rolos/{pedido}")
        obtido.raise_for_status()
        itens = obtido.json()
        rel.conferir(f"pedido {pedido}: QtdeItens", len(itens), len(legado), json=False)
        esperado = sorted(_tupla_sugestao(item) for item in legado)
        obtido_tuplas = sorted(_tupla_sugestao(item) for item in itens)
        for indice, (meu, ref) in enumerate(
            zip(obtido_tuplas, esperado, strict=False), start=1
        ):
            for nome, meu_valor, ref_valor in zip(
                ("Produto", "Cor"), meu[:2], ref[:2], strict=True
            ):
                rel.conferir(
                    f"pedido {pedido} linha {indice}.{nome}",
                    meu_valor,
                    ref_valor,
                    json=False,
                )
            for nome, meu_valor, ref_valor in zip(nomes_num, meu[2:6], ref[2:6], strict=True):
                rel.conferir(f"pedido {pedido} linha {indice}.{nome}", meu_valor, ref_valor)
            for nome, meu_valor, ref_valor in zip(
                CAMPOS_TEXTO_SUGESTAO, meu[6:], ref[6:], strict=True
            ):
                rel.conferir(
                    f"pedido {pedido} linha {indice}.{nome}",
                    meu_valor,
                    ref_valor,
                    json=False,
                )
    return rel


def validar_dados(cliente: TestClient, produto: str = "000015") -> Relatorio:
    """`/dados`: mesma population do SELECT legado (CTE_PECA + VW_CTE_PECA_EM_ABERTO)."""
    print("\n== /dados (VW_CTE_PECA_EM_ABERTO) ==")
    rel = Relatorio()
    legado = erp.query(
        """
        select count(*) as pecas, count(distinct empresa) as empresas,
               sum(metros) as metros, sum(peso) as peso
          from DBMicrodata_DGB.dbo.VW_CTE_PECA_EM_ABERTO
         where produto = ?
        """,
        (produto,),
    )[0]
    obtido = cliente.get("/dados", params={"produto": produto, "limite": 10_000})
    obtido.raise_for_status()
    pecas = len(obtido.json())
    rel.conferir(f"/dados produto={produto}: pecas", pecas, legado["pecas"], json=False)
    if pecas == legado["pecas"]:
        rel.conferir(
            f"/dados produto={produto}: soma metros",
            sum(p["Metros"] for p in obtido.json()),
            legado["metros"],
        )
        rel.conferir(
            f"/dados produto={produto}: soma peso",
            sum(p["Peso"] for p in obtido.json()),
            legado["peso"],
        )
        rel.conferir(
            f"/dados produto={produto}: empresas",
            len({p["Empresa"] for p in obtido.json()}),
            legado["empresas"],
            json=False,
        )

    pagina1 = cliente.get("/dados", params={"produto": produto, "limite": 5, "offset": 0}).json()
    pagina2 = cliente.get("/dados", params={"produto": produto, "limite": 5, "offset": 5}).json()
    rel.conferir(
        "paginação sem repetição",
        [p["Chave"] + p["Nro_Peca"] for p in pagina1 + pagina2],
        [p["Chave"] + p["Nro_Peca"] for p in obtido.json()[:10]],
        json=False,
    )
    return rel


def validar_dashboard(cliente: TestClient, dia: date = date(2026, 3, 1)) -> Relatorio:
    """`/dashboard-completo` precisa bater com as rotas individuais."""
    print("\n== /dashboard-completo (agrega as rotas) ==")
    rel = Relatorio()
    completo = cliente.get(f"/dashboard-completo/{dia.isoformat()}").json()
    for chave, rota, *formato in (
        ("faturamento", "/faturamento/{data}", "Faturamento"),
        ("faturamento_dia", "/faturamento-dia/{data}", "Faturamento"),
        ("contas_pagas", "/contas-pagas/{data}", "ContasPagas"),
        ("descontos", "/descontos/{data}", "Desconto"),
        ("devolucoes", "/devolucoes/{data}", "Devolucao"),
        ("estornos", "/estornos/{data}", "Estorno"),
        ("contas_receber_programado", "/contas-receber-programado", "QtdeDoc"),
        ("contas_pagar_programado", "/contas-pagar-programado", "QtdeDoc"),
        ("custos_administrativos_anual", "/custos-administrativos-anual", "Faturamento"),
        ("custos_administrativos_mensal", "/custos-administrativos-mensal", "Administrativo"),
    ):
        url = rota.format(data=dia.isoformat()) if "{data}" in rota else rota
        rel.conferir(
            f"dashboard.{chave}",
            completo[chave][formato[0]],
            cliente.get(url).json()[formato[0]],
        )
    return rel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Valida a Fase E (API x procedures legadas).")
    parser.add_argument(
        "--so",
        choices=("simples", "programados", "custos", "sugestao", "dados", "dashboard"),
    )
    parser.add_argument("--produto", default="000015", help="produto usado no /dados")
    args = parser.parse_args(argv)

    cliente = TestClient(app)
    relatorios: list[tuple[str, Relatorio]] = []
    if args.so in (None, "dados"):
        relatorios.append(("/dados", validar_dados(cliente, args.produto)))
    if args.so in (None, "simples"):
        relatorios.append(("kpis de valor", validar_simples(cliente)))
    if args.so in (None, "programados"):
        relatorios.append(("programados", validar_programados(cliente)))
    if args.so in (None, "custos"):
        relatorios.append(("custos", validar_custos(cliente)))
    if args.so in (None, "sugestao"):
        relatorios.append(("sugestao de rolos", validar_sugestao_rolos(cliente)))
    if args.so in (None, "dashboard"):
        relatorios.append(("dashboard", validar_dashboard(cliente)))

    print("\n== resumo ==")
    falhas = 0
    total = 0
    for nome, rel in relatorios:
        falhas += rel.falhas
        total += rel.total
        print(f"  {nome}: {rel.total - rel.falhas}/{rel.total}")
    print(f"\nFase E: {total - falhas}/{total} conferências iguais ao legado")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())