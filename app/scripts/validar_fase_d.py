"""Validacao da Fase D: core/marts locais x DBProDash legado (somente leitura).

Compara, numero a numero, os KPIs derivados de `core`/`marts` com as views/procedures
criptografadas do `DBProDash`. As views legadas nao podem ser lidas (WITH ENCRYPTION), mas
podem ser consultadas - e a comparacao e feita justamente sobre a saida delas.

Uso:
    python -m scripts.validar_fase_d            # todas as validacoes
    python -m scripts.validar_fase_d faturamento
    python -m scripts.validar_fase_d --meses 13

Sai com codigo 1 se alguma comparacao divergir.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text

from src.db import erp, warehouse

TOLERANCIA = Decimal("0.01")


class Relatorio:
    def __init__(self) -> None:
        self.falhas = 0
        self.total = 0

    def conferir(
        self,
        nome: str,
        meu: object,
        legado: object,
        tolerancia: Decimal = TOLERANCIA,
    ) -> None:
        self.total += 1
        if isinstance(meu, Decimal) and isinstance(legado, Decimal):
            ok = abs(meu - legado) <= tolerancia
        else:
            ok = meu == legado
        if ok:
            print(f"  OK   {nome}: {meu}")
        else:
            self.falhas += 1
            diferenca = (
                (meu - legado) if isinstance(meu, Decimal) and isinstance(legado, Decimal) else "?"
            )
            print(f"  FALHA {nome}: meu={meu} legado={legado} (dif={diferenca})")


def _dec(valor: object) -> Decimal:
    return Decimal(str(valor if valor is not None else 0))


def meses_recentes(quantidade: int, referencia: date | None = None) -> list[tuple[date, date]]:
    """Janelas [inicio, fim) dos ultimos `quantidade` meses, da mais antiga para a atual."""
    primeiro = (referencia or date.today()).replace(day=1)
    janelas: list[tuple[date, date]] = []
    for deslocamento in reversed(range(quantidade)):
        anos, mes_zerado = divmod(primeiro.month - 1 - deslocamento, 12)
        inicio = date(primeiro.year + anos, mes_zerado + 1, 1)
        fim = (
            date(inicio.year + 1, 1, 1)
            if inicio.month == 12
            else date(inicio.year, inicio.month + 1, 1)
        )
        janelas.append((inicio, fim))
    return janelas


def validar_faturamento(meses: int) -> Relatorio:
    """core.faturamento_itens e marts.faturamento_diario/mensal x vwFaturamento."""
    print("\n== faturamento (vwFaturamento) ==")
    rel = Relatorio()

    legado = erp.query(
        "select count(*) as qtd, sum(Vr_Total) as vr_total, sum(Acres_Desc) as acres "
        "from DBProDash.dbo.vwFaturamento"
    )[0]

    with warehouse.engine().connect() as conn:
        meu = conn.execute(
            text(
                "select count(*) as qtd, sum(vr_total) as vr_total, "
                "sum(acres_desc) as acres from core.faturamento_itens"
            )
        ).mappings().one()

    rel.conferir("itens faturados", int(meu["qtd"]), int(legado["qtd"]))
    rel.conferir("sum(vr_total)", _dec(meu["vr_total"]), _dec(legado["vr_total"]))
    rel.conferir("sum(acres_desc)", _dec(meu["acres"]), _dec(legado["acres"]))

    for inicio, fim in meses_recentes(meses):
        legado_mes = erp.query(
            "select count(*) as qtd, sum(Vr_Total) as vr, sum(Acres_Desc) as acres "
            "from DBProDash.dbo.vwFaturamento "
            "where Data_Nota >= ? and Data_Nota < ?",
            (inicio, fim),
        )[0]

        with warehouse.engine().connect() as conn:
            meu_mes = conn.execute(
                text(
                    "select itens, vr_total, desconto, faturamento "
                    "from marts.faturamento_mensal where mes = :inicio"
                ),
                {"inicio": inicio},
            ).mappings().one_or_none()

        if meu_mes is None:
            rel.conferir(f"{inicio:%Y-%m} sem linhas", 0, int(legado_mes["qtd"]))
            continue

        rotulo = f"{inicio:%Y-%m}"
        rel.conferir(f"{rotulo} itens", int(meu_mes["itens"]), int(legado_mes["qtd"]))
        rel.conferir(
            f"{rotulo} faturamento",
            _dec(meu_mes["faturamento"]),
            _dec(legado_mes["vr"]) + _dec(legado_mes["acres"]),
        )
        rel.conferir(f"{rotulo} desconto", _dec(meu_mes["desconto"]), _dec(legado_mes["acres"]))

    return rel


def validar_faturamento_diario(dias: int) -> Relatorio:
    """Dia a dia (uspFaturamentoDia) nos ultimos `dias` dias com movimento."""
    print("\n== faturamento diario (uspFaturamentoDia) ==")
    rel = Relatorio()
    inicio = date.today() - timedelta(days=dias)

    legado = erp.query(
        "select cast(Data_Nota as date) as dia, sum(Vr_Total) as vr, sum(Acres_Desc) as acres "
        "from DBProDash.dbo.vwFaturamento where Data_Nota >= ? group by cast(Data_Nota as date) "
        "order by 1",
        (inicio,),
    )
    esperado = {
        str(row["dia"]): _dec(row["vr"]) + _dec(row["acres"]) for row in legado
    }

    with warehouse.engine().connect() as conn:
        meu = {
            str(row["data"]): _dec(row["faturamento"])
            for row in conn.execute(
                text(
                    "select data, faturamento from marts.faturamento_diario "
                    "where data >= :inicio"
                ),
                {"inicio": inicio},
            ).mappings()
        }

    for dia in sorted(set(esperado) | set(meu)):
        rel.conferir(dia, meu.get(dia, Decimal(0)), esperado.get(dia, Decimal(0)))

    return rel


VALIDADORES = {
    "faturamento": validar_faturamento,
    "faturamento_diario": validar_faturamento_diario,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("alvos", nargs="*", default=None)
    parser.add_argument("--meses", type=int, default=13)
    parser.add_argument("--dias", type=int, default=45)
    args = parser.parse_args(argv)

    alvos = args.alvos or list(VALIDADORES)
    desconhecidos = [alvo for alvo in alvos if alvo not in VALIDADORES]
    if desconhecidos:
        print(f"validacao desconhecida: {desconhecidos}", file=sys.stderr)
        return 2

    comparacoes = falhas = 0
    for alvo in alvos:
        if alvo == "faturamento_diario":
            relatorio = validar_faturamento_diario(args.dias)
        else:
            relatorio = validar_faturamento(args.meses)
        comparacoes += relatorio.total
        falhas += relatorio.falhas

    print(f"\n{comparacoes - falhas}/{comparacoes} comparacoes iguais ao DBProDash")
    if falhas:
        print(f"{falhas} divergencia(s) — revisar a regra portada")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())