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


def validar_contas_pagas(meses: int) -> Relatorio:
    """marts.contas_pagas_diario/mensal x DBProDash.dbo.vwContasPagas."""
    print("\n== contas pagas (vwContasPagas) ==")
    rel = Relatorio()

    legado = erp.query(
        "select count(*) as qtd, sum(valorPago) as pago, "
        "sum(valorPago - valorPago) as zero from DBProDash.dbo.vwContasPagas"
    )[0]

    with warehouse.engine().connect() as conn:
        meu = conn.execute(
            text(
                "select count(*) as qtd, sum(valor_pago) as pago from raw.pag_baixas"
            )
        ).mappings().one()

    rel.conferir("baixas", int(meu["qtd"]), int(legado["qtd"]))
    rel.conferir("sum(valor_pago)", _dec(meu["pago"]), _dec(legado["pago"]))

    for inicio, fim in meses_recentes(meses):
        legado_mes = erp.query(
            "select count(*) as qtd, sum(valorPago) as pago "
            "from DBProDash.dbo.vwContasPagas where Data_Baixa >= ? and Data_Baixa < ?",
            (inicio, fim),
        )[0]
        with warehouse.engine().connect() as conn:
            meu_mes = conn.execute(
                text(
                    "select baixas, valor_pago from marts.contas_pagas_mensal where mes = :inicio"
                ),
                {"inicio": inicio},
            ).mappings().one_or_none()

        rotulo = f"{inicio:%Y-%m}"
        if meu_mes is None:
            rel.conferir(f"{rotulo} sem linhas", 0, int(legado_mes["qtd"]))
            continue
        rel.conferir(f"{rotulo} baixas", int(meu_mes["baixas"]), int(legado_mes["qtd"]))
        rel.conferir(f"{rotulo} valor pago", _dec(meu_mes["valor_pago"]), _dec(legado_mes["pago"]))

    return rel


def validar_financeiro() -> Relatorio:
    """marts.financeiro_*_programado x DBProDash.dbo.vwFinanceiro*."""
    print("\n== financeiro em aberto (vwFinanceiroContasPagar/Receber) ==")
    rel = Relatorio()

    for rotulo, view, mart, coluna in (
        (
            "pagar",
            "DBProDash.dbo.vwFinanceiroContasPagar",
            "marts.financeiro_pagar_programado",
            "valor_total",
        ),
        (
            "receber",
            "DBProDash.dbo.vwFinanceiroContasReceber",
            "marts.financeiro_receber_programado",
            "valor_total",
        ),
    ):
        legado = erp.query(
            f"select count(*) as qtd, sum(ValorTotal) as valor, min(Vencimento) as menor, "
            f"max(Vencimento) as maior from {view}"
        )[0]
        with warehouse.engine().connect() as conn:
            meu = conn.execute(
                text(f"select count(*) as qtd, sum({coluna}) as valor, "
                     f"min(vencimento) as menor, max(vencimento) as maior from {mart}")
            ).mappings().one()

        rel.conferir(f"{rotulo} titulos", int(meu["qtd"]), int(legado["qtd"]))
        rel.conferir(f"{rotulo} valor", _dec(meu["valor"]), _dec(legado["valor"]))
        rel.conferir(
            f"{rotulo} vencimento menor",
            str(meu["menor"]),
            str(legado["menor"]),
        )
        rel.conferir(
            f"{rotulo} vencimento maior",
            str(meu["maior"]),
            str(legado["maior"]),
        )

    return rel


def validar_estornos(meses: int) -> Relatorio:
    """marts.estornos_* x DBProDash.dbo.vwListagemDeEstornos (KPI de uspEstorno)."""
    print("\n== estornos (vwListagemDeEstornos) ==")
    rel = Relatorio()

    legado = erp.query(
        "select count(*) as qtd, count(distinct Pedido) as pedidos, sum(Vr_Nota) as valor "
        "from DBProDash.dbo.vwListagemDeEstornos"
    )[0]
    with warehouse.engine().connect() as conn:
        meu = conn.execute(
            text(
                "select count(*) as qtd, count(distinct pedido) as pedidos, "
                "sum(vr_nota) as valor from core.estornos_itens"
            )
        ).mappings().one()

    rel.conferir("itens", int(meu["qtd"]), int(legado["qtd"]))
    rel.conferir("pedidos", int(meu["pedidos"]), int(legado["pedidos"]))
    rel.conferir("sum(vr_nota)", _dec(meu["valor"]), _dec(legado["valor"]))

    for inicio, fim in meses_recentes(meses):
        legado_mes = erp.query(
            "select count(*) as qtd, sum(Vr_Nota) as valor "
            "from DBProDash.dbo.vwListagemDeEstornos "
            "where Data_Emissao >= ? and Data_Emissao < ?",
            (inicio, fim),
        )[0]
        with warehouse.engine().connect() as conn:
            meu_mes = conn.execute(
                text("select itens, valor_nota from marts.estornos_mensal where mes = :inicio"),
                {"inicio": inicio},
            ).mappings().one_or_none()

        rotulo = f"{inicio:%Y-%m}"
        if meu_mes is None:
            rel.conferir(f"{rotulo} sem linhas", 0, int(legado_mes["qtd"]))
            continue
        rel.conferir(f"{rotulo} itens", int(meu_mes["itens"]), int(legado_mes["qtd"]))
        rel.conferir(f"{rotulo} valor", _dec(meu_mes["valor_nota"]), _dec(legado_mes["valor"]))

    return rel


def validar_devolucoes(meses: int) -> Relatorio:
    """marts.devolucoes_* x DBProDash.dbo.vwListagemDeEntradasSaidasPorCFOP (uspDevolucao)."""
    print("\n== devolucoes (vwListagemDeEntradasSaidasPorCFOP) ==")
    rel = Relatorio()

    legado = erp.query(
        "select count(*) as qtd, sum(Vr_CONtabil) as valor "
        "from DBProDash.dbo.vwListagemDeEntradasSaidasPorCFOP "
        "where Nova_CFOP in ('1.201-1', '1.201-2', '1.202-1', '2.202-1')"
    )[0]
    with warehouse.engine().connect() as conn:
        meu = conn.execute(
            text(
                "select count(*) as qtd, sum(vr_contabil) as valor from core.devolucoes_documentos"
            )
        ).mappings().one()

    rel.conferir("documentos", int(meu["qtd"]), int(legado["qtd"]))
    rel.conferir("sum(vr_contabil)", _dec(meu["valor"]), _dec(legado["valor"]))

    for inicio, fim in meses_recentes(meses):
        legado_mes = erp.query(
            "select count(*) as qtd, sum(Vr_CONtabil) as valor "
            "from DBProDash.dbo.vwListagemDeEntradasSaidasPorCFOP "
            "where Nova_CFOP in ('1.201-1', '1.201-2', '1.202-1', '2.202-1') "
            "and Data >= ? and Data < ?",
            (inicio, fim),
        )[0]
        with warehouse.engine().connect() as conn:
            meu_mes = conn.execute(
                text(
                    "select documentos, valor from marts.devolucoes_mensal "
                    "where mes = :inicio and tipo = 'E'"
                ),
                {"inicio": inicio},
            ).mappings().one_or_none()

        rotulo = f"{inicio:%Y-%m}"
        if meu_mes is None:
            rel.conferir(f"{rotulo} sem linhas", 0, int(legado_mes["qtd"]))
            continue
        rel.conferir(
            f"{rotulo} documentos", int(meu_mes["documentos"]), int(legado_mes["qtd"])
        )
        rel.conferir(f"{rotulo} valor", _dec(meu_mes["valor"]), _dec(legado_mes["valor"]))

    return rel


def validar_estoque() -> Relatorio:
    """core.estoque_pecas_em_aberto x VW_CTE_PECA_EM_ABERTO / vwSaldoTecidosEstoqueDetalhado."""
    print("\n== estoque em aberto (VW_CTE_PECA_EM_ABERTO) ==")
    rel = Relatorio()

    legado = erp.query(
        "select count(*) as qtd, sum(Metros) as metros, sum(Peso) as peso, "
        "min(Data_Entrada) as menor, max(Data_Entrada) as maior from VW_CTE_PECA_EM_ABERTO"
    )[0]
    with warehouse.engine().connect() as conn:
        meu = conn.execute(
            text(
                "select count(*) as qtd, sum(metros) as metros, sum(peso) as peso, "
                "min(data_entrada) as menor, max(data_entrada) as maior "
                "from core.estoque_pecas_em_aberto"
            )
        ).mappings().one()

    rel.conferir("pecas em aberto", int(meu["qtd"]), int(legado["qtd"]))
    rel.conferir("sum(metros)", _dec(meu["metros"]), _dec(legado["metros"]))
    rel.conferir("sum(peso)", _dec(meu["peso"]), _dec(legado["peso"]))
    rel.conferir("entrada menor", str(meu["menor"]), str(legado["menor"]))
    rel.conferir("entrada maior", str(meu["maior"]), str(legado["maior"]))

    dash = erp.query(
        "select count(*) as qtd, sum(Metros) as metros, sum(Peso_Liquido) as peso "
        "from DBProDash.dbo.vwSaldoTecidosEstoqueDetalhado"
    )[0]
    rel.conferir("dashboard: pecas", int(meu["qtd"]), int(dash["qtd"]))
    rel.conferir("dashboard: metros", _dec(meu["metros"]), _dec(dash["metros"]))
    rel.conferir("dashboard: peso", _dec(meu["peso"]), _dec(dash["peso"]))

    return rel


SQL_SUGESTAO_ROLOS = """
with itens as (
    select Pedido, Item, Produto, Cor, Qtde,
           (Qtde - Qtde_Romaneio - Qtde_Acerto) as Qtde_Saldo
    from Vw_Car_Itens_Pedido
    where (Qtde - Qtde_Romaneio - Qtde_Acerto) > 0
),
disp as (
    select CP.Produto, CP.Cor, CP.SubLote, CP.Gaveta, CP.Nro_Rolo, CP.Nro_Peca, CP.Metros,
           row_number() over (
               partition by CP.Produto, CP.Cor
               order by CP.Gaveta, CP.Tear desc, CP.Nro_Rolo desc
           ) as rn
    from Cte_Peca CP
    left join CTE_Baixa CB
        on CP.Empresa = CB.Empresa and CP.Situacao = CB.Situacao
        and CP.Nro_Rolo = CB.Nro_Rolo and CP.Nro_Peca = CB.Nro_Peca
    where CP.Nro_Rolo_Origem is null and CB.Empresa is null
),
acum as (
    select d.*, sum(d.Metros) over (
               partition by d.Produto, d.Cor order by d.rn
               rows between unbounded preceding and current row
           ) as Soma_Metros
    from disp d
),
sel as (
    select i.Pedido, i.Item, i.Produto, i.Cor, i.Qtde, i.Qtde_Saldo,
           a.SubLote, a.Gaveta, a.Nro_Rolo, a.Nro_Peca, a.Metros
    from itens i
    join acum a on a.Produto = i.Produto and a.Cor = i.Cor
    where a.Soma_Metros <= i.Qtde_Saldo or abs(a.Soma_Metros - i.Qtde_Saldo) < 0.01
)
select Pedido, Item, Produto, Cor, min(Qtde) as Qtde_Item, Qtde_Saldo, min(SubLote) as Sublote,
       count(*) as Qtde_Pecas, sum(Metros) as Total_Metros,
       stuff((select ', ' + cast(Gaveta as varchar(20)) from (
                   select distinct Gaveta from sel s3
                    where s3.Pedido = s.Pedido and s3.Item = s.Item) g
                 order by Gaveta
                 for xml path(''), type).value('.', 'nvarchar(max)'), 1, 2, '') as Gavetas,
       stuff((select ', ' + right('0000000000' + Nro_Rolo, 10) + right('000' + Nro_Peca, 3)
                from sel s4 where s4.Pedido = s.Pedido and s4.Item = s.Item
                order by Nro_Rolo, Nro_Peca
                for xml path(''), type).value('.', 'nvarchar(max)'), 1, 2, '') as Rolos
from sel s
group by Pedido, Item, Produto, Cor, Qtde_Saldo
"""

CAMPOS_SUGESTAO = ("qtde_saldo", "qtde_pecas", "total_metros", "sublote", "gavetas", "rolos")


def _valor(linha: object, nome: str) -> object:
    """Le a coluna ignorando caixa: o ERP devolve `Qtde_Pecas`, o Postgres `qtde_pecas`."""
    for chave, conteudo in dict(linha).items():
        if chave.lower() == nome:
            return conteudo
    raise KeyError(nome)


def _chave_sugestao(linha: object) -> tuple[str, str, str, str]:
    return (
        str(_valor(linha, "pedido")).strip(),
        str(_valor(linha, "item")),
        str(_valor(linha, "produto")).strip(),
        str(_valor(linha, "cor")).strip(),
    )


def validar_sugestao_rolos() -> Relatorio:
    """core.pedido_sugestao_rolos x logica de uspEnderecamentoParaAtenderPedidoGeral.

    A procedure do `DBProDash` e criptografada; a referencia e a mesma logica escrita como
    SELECT sobre as tabelas legiveis do ERP, que e o que a migration `0010` portada.
    """
    print("\n== sugestao de rolos (uspEnderecamentoParaAtenderPedidoGeral) ==")
    rel = Relatorio()

    legado = {_chave_sugestao(linha): linha for linha in erp.query(SQL_SUGESTAO_ROLOS)}
    with warehouse.engine().connect() as conn:
        meu = {
            _chave_sugestao(linha): linha
            for linha in conn.execute(
                text(
                    "select pedido, item, produto, cor, qtde_saldo, qtde_pecas, total_metros, "
                    "sublote, gavetas, rolos from core.pedido_sugestao_rolos"
                )
            ).mappings()
        }

    rel.conferir("itens com sugestao", len(meu), len(legado))
    for chave_item in sorted(set(legado) | set(meu)):
        esperado, obtido = legado.get(chave_item), meu.get(chave_item)
        if esperado is None or obtido is None:
            rel.conferir(f"item {chave_item} presente", obtido is not None, esperado is not None)
            continue
        for nome in CAMPOS_SUGESTAO:
            ref, meu_valor = _valor(esperado, nome), _valor(obtido, nome)
            if nome in ("qtde_pecas", "qtde_saldo"):
                rel.conferir(f"{chave_item} {nome}", int(meu_valor or 0), int(ref or 0))
            elif nome == "total_metros":
                rel.conferir(f"{chave_item} {nome}", _dec(meu_valor), _dec(ref))
            else:
                rel.conferir(
                    f"{chave_item} {nome}",
                    str(meu_valor or "").strip(),
                    str(ref or "").strip(),
                )

    return rel


SQL_CUSTOS_DEPARTAMENTO = """
select datefromparts(year(Data_Baixa), month(Data_Baixa), 1) as mes,
       Codigo_Departamento, Codigo_Despesa,
       count(*) as parcelas, sum(Valor_Baixado) as valor_baixado
from DBProDash.dbo.vwContasPagasCentroCusto
where Data_Baixa is not null
group by datefromparts(year(Data_Baixa), month(Data_Baixa), 1),
         Codigo_Departamento, Codigo_Despesa
"""

SQL_CUSTOS_ADMINISTRATIVO = """
select datefromparts(year(Data_Baixa), month(Data_Baixa), 1) as mes,
       count(*) as parcelas, sum(Valor_Baixado) as valor_baixado
from DBProDash.dbo.vwContasPagasCentroCusto
where Data_Baixa is not null and Codigo_Departamento in ('1.1.1.1', '1.1.1.2')
group by datefromparts(year(Data_Baixa), month(Data_Baixa), 1)
"""


def validar_custos() -> Relatorio:
    """marts.custos_* x DBProDash.vwContasPagasCentroCusto (espelho de Rel_CCusto_Niveis)."""
    print("\n== custos por departamento (vwContasPagasCentroCusto) ==")
    rel = Relatorio()

    legado = {_chave_custos(linha): linha for linha in erp.query(SQL_CUSTOS_DEPARTAMENTO)}
    with warehouse.engine().connect() as conn:
        meu = {
            _chave_custos(linha): linha
            for linha in conn.execute(
                text(
                    "select mes, codigo_departamento, codigo_despesa, parcelas, valor_baixado "
                    "from marts.custos_por_departamento_mensal"
                )
            ).mappings()
        }
    rel.conferir("combinacoes mes/depto/despesa", len(meu), len(legado))
    for chave in sorted(set(legado) | set(meu)):
        esperado, obtido = legado.get(chave), meu.get(chave)
        if esperado is None or obtido is None:
            rel.conferir(f"grupo {chave} presente", obtido is not None, esperado is not None)
            continue
        rel.conferir(
            f"{chave} parcelas",
            int(_valor(obtido, "parcelas")),
            int(_valor(esperado, "parcelas")),
        )
        rel.conferir(
            f"{chave} valor",
            _dec(_valor(obtido, "valor_baixado")),
            _dec(_valor(esperado, "valor_baixado")),
        )

    admin_legado = {_chave_mes(linha): linha for linha in erp.query(SQL_CUSTOS_ADMINISTRATIVO)}
    with warehouse.engine().connect() as conn:
        admin_meu = {
            _chave_mes(linha): linha
            for linha in conn.execute(
                text(
                    "select mes, parcelas, valor_baixado from marts.custos_administrativo_mensal"
                )
            ).mappings()
        }
    rel.conferir("meses administrativos", len(admin_meu), len(admin_legado))
    for chave in sorted(set(admin_legado) | set(admin_meu)):
        esperado, obtido = admin_legado.get(chave), admin_meu.get(chave)
        if esperado is None or obtido is None:
            rel.conferir(
                f"administrativo {chave} presente", obtido is not None, esperado is not None
            )
            continue
        rel.conferir(
            f"administrativo {chave} parcelas",
            int(_valor(obtido, "parcelas")),
            int(_valor(esperado, "parcelas")),
        )
        rel.conferir(
            f"administrativo {chave} valor",
            _dec(_valor(obtido, "valor_baixado")),
            _dec(_valor(esperado, "valor_baixado")),
        )

    return rel


def _chave_mes(linha: object) -> str:
    valor = _valor(linha, "mes")
    return valor.strftime("%Y-%m") if hasattr(valor, "strftime") else str(valor)[:7]


def _chave_custos(linha: object) -> str:
    """`mes/Codigo_Departamento/Codigo_Despesa`: o grupo do mart de custos."""
    return (
        f"{_chave_mes(linha)}/{_valor(linha, 'codigo_departamento')}"
        f"/{_valor(linha, 'codigo_despesa')}"
    )


VALIDADORES = {
    "faturamento": validar_faturamento,
    "faturamento_diario": validar_faturamento_diario,
    "contas_pagas": validar_contas_pagas,
    "financeiro": validar_financeiro,
    "estornos": validar_estornos,
    "devolucoes": validar_devolucoes,
    "estoque": validar_estoque,
    "sugestao_rolos": validar_sugestao_rolos,
    "custos": validar_custos,
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
        elif alvo == "financeiro":
            relatorio = validar_financeiro()
        elif alvo == "estoque":
            relatorio = validar_estoque()
        elif alvo == "sugestao_rolos":
            relatorio = validar_sugestao_rolos()
        elif alvo == "custos":
            relatorio = validar_custos()
        else:
            relatorio = VALIDADORES[alvo](args.meses)
        comparacoes += relatorio.total
        falhas += relatorio.falhas

    print(f"\n{comparacoes - falhas}/{comparacoes} comparacoes iguais ao DBProDash")
    if falhas:
        print(f"{falhas} divergencia(s) — revisar a regra portada")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())