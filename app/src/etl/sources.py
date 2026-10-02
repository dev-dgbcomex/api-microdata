"""Registro das fontes do ERP (Estudo 22 §5 + conferências de 02/out/2026 no ERP).

Chaves naturais e colunas de watermark foram conferidas em information_schema/sys do
DBMicrodata_DGB; divergências em relação aos estudos estão anotadas em `doc`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.etl.transform.naming import snake_case

Estrategia = Literal["full", "watermark", "recarrega_pai", "reconciliacao"]


@dataclass(frozen=True, slots=True)
class Fonte:
    dominio: str
    tabela_erp: str
    chave_natural: tuple[str, ...]
    estrategia: Estrategia
    coluna_watermark: str | None = None
    tabela_raw: str = ""
    pai: str | None = None
    linhas_erp: int | None = None
    doc: str = ""
    ativo: bool = True
    chave_pai: tuple[str, ...] | None = None
    """Colunas do filho que carregam a chave do pai, quando os nomes nao coincidem."""

    @property
    def destino(self) -> str:
        return self.tabela_raw or snake_case(self.tabela_erp)

    def colunas_chave_pai(self) -> tuple[str, ...]:
        """Chave do pai vista nas colunas deste filho (usada na recarga por pai)."""
        if not self.pai:
            return ()
        return self.chave_pai or FONTES_POR_TABELA[self.pai.lower()].chave_natural


FONTES: tuple[Fonte, ...] = (
    Fonte(
        dominio="cadastros",
        tabela_erp="Clientes_Principal",
        chave_natural=("Codigo_Cliente",),
        estrategia="full",
        coluna_watermark=None,
        linhas_erp=1690,
        doc="Est. 22 §5; Est. 27 (Ult_Atualizacao 100% NULL; ultima_atualizacao_dt 8%)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Produtos",
        chave_natural=("Empresa", "Codigo"),
        estrategia="full",
        linhas_erp=64,
        doc="Est. 22 §5 (view, 64 linhas)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Produtos_Tecidos",
        chave_natural=("Empresa", "Produto"),
        estrategia="full",
        doc="Est. 27 (view)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Fornecedores",
        chave_natural=("Codigo",),
        estrategia="full",
        linhas_erp=659,
        doc="Est. 22 §5 (view, sem PK; Data_Cadastro ~=4)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Rec_Vendedores",
        chave_natural=("Codigo_Vendedores",),
        estrategia="full",
        linhas_erp=77,
        doc="Est. 22 §5 (view)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Condicoes_Pagto",
        chave_natural=("Codigo_Pagto",),
        estrategia="full",
        linhas_erp=362,
        doc="Est. 22 §5",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Bancos",
        chave_natural=("Nr_Bco_Bancos",),
        estrategia="full",
        linhas_erp=14,
        doc="Est. 33",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Sistemas",
        chave_natural=("Cod_Sistema",),
        estrategia="full",
        linhas_erp=67,
        doc="Est. 41 (auth: escopos)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="usuario",
        chave_natural=("usu_cd",),
        estrategia="full",
        linhas_erp=35,
        doc="Est. 41 (login/senha; senha em texto claro -> D5 auth nova)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Usuarios",
        chave_natural=("Cod_Usuario",),
        estrategia="full",
        linhas_erp=39,
        doc="Est. 41 (Senha + senha_crypt)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="Usuario_Acessos",
        chave_natural=("Cod_Usuario", "Cod_Sistema", "Cod_Topico"),
        estrategia="full",
        linhas_erp=19748,
        doc="Est. 41 (CONSULTA/INCLUI/ALTERA/EXCLUI por topico)",
    ),
    Fonte(
        dominio="cadastros",
        tabela_erp="SIS_UsuarioEmpresa",
        chave_natural=("ID",),
        estrategia="full",
        linhas_erp=68,
        doc="Est. 42 §7 (multiempresa; D6)",
    ),
    Fonte(
        dominio="faturamento",
        tabela_erp="Fat_Pedido",
        chave_natural=("Empresa", "Pedido"),
        estrategia="full",
        coluna_watermark=None,
        linhas_erp=12745,
        doc="Est. 29 §6 (Ult_Atualizacao 100% NULL; PK real = Empresa+Pedido)",
    ),
    Fonte(
        dominio="faturamento",
        tabela_erp="Fat_Itens_Pedido",
        chave_natural=("Empresa", "Pedido", "Item"),
        estrategia="recarrega_pai",
        pai="Fat_Pedido",
        coluna_watermark=None,
        linhas_erp=101110,
        doc="Est. 29 §6 (290 colunas; sem watermark utilizavel)",
    ),
    Fonte(
        dominio="faturamento",
        tabela_erp="Fat_Nat_Pedido",
        chave_natural=("Empresa", "Pedido", "NatOp", "Seq"),
        estrategia="recarrega_pai",
        pai="Fat_Pedido",
        linhas_erp=12743,
        doc="Est. 29 (view sem carimbo de alteracao)",
    ),
    Fonte(
        dominio="faturamento",
        tabela_erp="Fat_Parc_Pedido",
        chave_natural=("Empresa_Parcelas", "Documento_Parcelas", "Parcela_Parcelas"),
        estrategia="recarrega_pai",
        pai="Fat_Pedido",
        chave_pai=("Empresa_Parcelas", "Documento_Parcelas"),
        linhas_erp=30101,
        doc="Est. 29 (PK real nao tem Serie; Empresa/Documento com sufixo _Parcelas)",
    ),
    Fonte(
        dominio="contas_pagar",
        tabela_erp="NF_Entradas",
        chave_natural=("Empresa", "Documento", "Serie", "Tipo_Fornec", "Fornecedor"),
        estrategia="watermark",
        coluna_watermark="Data_Hora",
        linhas_erp=8676,
        doc="Est. 31 §8",
    ),
    Fonte(
        dominio="contas_pagar",
        tabela_erp="NFE_Parcelas",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "Tipo_Fornec",
            "Fornecedor",
            "Parcela",
        ),
        estrategia="reconciliacao",
        linhas_erp=8781,
        doc="Est. 31 §8 (CDC via NFE_Parcelas_Log.DataHora; recarrega por pai)",
    ),
    Fonte(
        dominio="contas_pagar",
        tabela_erp="Pag_Baixas",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "Tipo_Fornec",
            "Fornecedor",
            "Parcela",
            "Parcial",
        ),
        estrategia="watermark",
        coluna_watermark="Data_Hora_Baixa",
        linhas_erp=8652,
        doc="Est. 31 §8",
    ),
    Fonte(
        dominio="contas_pagar",
        tabela_erp="Pag_Historicos",
        chave_natural=("Cod_Historico_Historicos",),
        estrategia="full",
        linhas_erp=8,
        doc="Est. 31 (historicos de baixa; tipo 6)",
    ),
    Fonte(
        dominio="contas_pagar",
        tabela_erp="Pag_Operacoes",
        chave_natural=("Codigo",),
        estrategia="full",
        linhas_erp=1,
        doc="Est. 31",
    ),
    Fonte(
        dominio="custos",
        tabela_erp="NFE_CCustos",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "Tipo_Fornec",
            "Fornecedor",
            "Centro_Custo",
        ),
        estrategia="recarrega_pai",
        pai="NF_Entradas",
        linhas_erp=0,
        tabela_raw="nfe_ccustos",
        doc="Est. 36 (vazia no ERP; estrutura mantida p/ reprocesso)",
    ),
    Fonte(
        dominio="custos",
        tabela_erp="NFE_CCustos_departamento",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "Tipo_Fornec",
            "Fornecedor",
            "ItemDespRec",
            "Item",
        ),
        estrategia="recarrega_pai",
        pai="NF_Entradas",
        linhas_erp=8621,
        tabela_raw="nfe_ccustos_departamento",
        doc="Est. 36/23 (rateio por departamento)",
    ),
    Fonte(
        dominio="custos",
        tabela_erp="NFE_CCustos_DespesasReceitas",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "Tipo_Fornec",
            "Fornecedor",
            "Item",
        ),
        estrategia="recarrega_pai",
        pai="NF_Entradas",
        linhas_erp=8636,
        tabela_raw="nfe_ccustos_despesas_receitas",
        doc="Est. 36 (rateio despesa/receita)",
    ),
    Fonte(
        dominio="custos",
        tabela_erp="Pag_DespesasReceitas",
        chave_natural=("Primeiro_Nivel", "Segundo_Nivel", "Terceiro_Nivel", "E_S"),
        estrategia="full",
        linhas_erp=91,
        doc="Est. 36 (PK real: 3 niveis + E_S; a 4a coluna 'Quarto_Nivel' do "
        "Pag_CCusto_Departamento nao existe aqui)",
    ),
    Fonte(
        dominio="custos",
        tabela_erp="Pag_CCusto_Departamento",
        chave_natural=(
            "Primeiro_Nivel",
            "Segundo_Nivel",
            "Terceiro_Nivel",
            "Quarto_Nivel",
        ),
        estrategia="full",
        linhas_erp=21,
        tabela_raw="pag_ccusto_departamento",
        doc="Est. 36",
    ),
    Fonte(
        dominio="contas_receber",
        tabela_erp="Notas_Fiscais_Rec",
        chave_natural=("Nr_Empresa_NF", "Nr_Documento_NF", "Serie"),
        estrategia="reconciliacao",
        linhas_erp=11825,
        doc="Est. 30 §7 (sem carimbo confiavel; CDC em Notas_Fiscais_Parcelas_Log)",
    ),
    Fonte(
        dominio="contas_receber",
        tabela_erp="Notas_Fiscais_Parcelas",
        chave_natural=(
            "Empresa_Parcelas",
            "Documento_Parcelas",
            "Serie",
            "Parcela_Parcelas",
        ),
        estrategia="reconciliacao",
        linhas_erp=30115,
        doc="Est. 30 §7",
    ),
    Fonte(
        dominio="contas_receber",
        tabela_erp="Rec_Baixas",
        chave_natural=("Empresa", "Documento", "Serie", "Parcela", "Parcial"),
        estrategia="watermark",
        coluna_watermark="Data_Hora_Baixa",
        linhas_erp=29131,
        doc="Est. 30 §7",
    ),
    Fonte(
        dominio="fiscal",
        tabela_erp="Liv_Saidas",
        chave_natural=("Empresa", "Documento", "Serie"),
        estrategia="watermark",
        coluna_watermark="Data_Hora",
        linhas_erp=12190,
        doc="Est. 35",
    ),
    Fonte(
        dominio="fiscal",
        tabela_erp="Liv_SaiProd",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "NatOp",
            "Seq",
            "EmpProd",
            "Produto",
            "Incremento",
        ),
        estrategia="recarrega_pai",
        pai="Liv_Saidas",
        linhas_erp=34257,
        doc="Est. 22 §5 (itens sem data -> recarrega por documento)",
    ),
    Fonte(
        dominio="fiscal",
        tabela_erp="Liv_Entradas",
        chave_natural=("Empresa", "Documento", "Serie", "Fornecedor", "Tipo_Fornec"),
        estrategia="watermark",
        coluna_watermark="Data_Hora",
        linhas_erp=1960,
        doc="Est. 35",
    ),
    Fonte(
        dominio="fiscal",
        tabela_erp="Liv_EntProd",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "Tipo_Fornec",
            "Fornecedor",
            "NatOp",
            "Seq",
            "EmpProd",
            "Produto",
            "Incremento",
        ),
        estrategia="recarrega_pai",
        pai="Liv_Entradas",
        linhas_erp=22526,
        doc="Est. 22 §5",
    ),
    Fonte(
        dominio="fiscal",
        tabela_erp="Liv_SaiNatOp",
        chave_natural=("Empresa", "Documento", "Serie", "NatOp", "Seq"),
        estrategia="recarrega_pai",
        pai="Liv_Saidas",
        linhas_erp=12191,
        doc="Est. 35 (totais por natureza da saida; necessario para o KPI de devolucao)",
    ),
    Fonte(
        dominio="fiscal",
        tabela_erp="Liv_EntNatOp",
        chave_natural=(
            "Empresa",
            "Documento",
            "Serie",
            "Tipo_Fornec",
            "Fornecedor",
            "NatOp",
            "Seq",
        ),
        estrategia="recarrega_pai",
        pai="Liv_Entradas",
        linhas_erp=1960,
        doc="Est. 35 (totais por natureza da entrada; necessario para o KPI de devolucao)",
    ),
    Fonte(
        dominio="fiscal",
        tabela_erp="Liv_Natureza",
        chave_natural=("Codigo", "Sequencia"),
        estrategia="full",
        linhas_erp=132,
        doc="Est. 35 (cadastro de naturezas de operacao)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Car_Cores",
        chave_natural=("Codigo",),
        estrategia="full",
        linhas_erp=72,
        doc="Est. 34 (descricao de cor; join INNER na VW_CTE_PECA_EM_ABERTO)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Car_Situacoes",
        chave_natural=("Codigo",),
        estrategia="full",
        linhas_erp=3,
        doc="Est. 34 (descricao de situacao; join INNER na VW_CTE_PECA_EM_ABERTO)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Car_Desenhos",
        chave_natural=("Codigo",),
        estrategia="full",
        linhas_erp=2,
        doc="Est. 34 (descricao de desenho; join INNER na VW_CTE_PECA_EM_ABERTO)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Car_Categorias",
        chave_natural=("Codigo",),
        estrategia="full",
        linhas_erp=3,
        doc="Est. 34 (descricao de categoria; join INNER na VW_CTE_PECA_EM_ABERTO)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Car_Variante",
        chave_natural=("Codigo",),
        estrategia="full",
        linhas_erp=2,
        doc="Est. 34 (descricao de variante; join LEFT na VW_CTE_PECA_EM_ABERTO)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Cte_Peca",
        chave_natural=("Empresa", "Situacao", "Nro_Rolo", "Nro_Peca"),
        estrategia="watermark",
        coluna_watermark="Data_Hora",
        linhas_erp=300601,
        doc="Est. 34 (Alt_Data date + Data_Hora datetime); full fora do expediente",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="CTE_Baixa",
        chave_natural=("Empresa", "Situacao", "Nro_Rolo", "Nro_Peca"),
        estrategia="watermark",
        coluna_watermark="Data_Saida",
        linhas_erp=282113,
        doc="Est. 34 (Data_Saida date -> reconciliacao diaria)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Car_Pedido",
        chave_natural=("Empresa", "Pedido"),
        estrategia="full",
        coluna_watermark=None,
        linhas_erp=15915,
        doc="Est. 28 (carteira; DataHora_Alteracao 0,16%, max 2018)",
    ),
    Fonte(
        dominio="estoque",
        tabela_erp="Car_Itens_Pedido",
        chave_natural=("Empresa", "Pedido", "Item"),
        estrategia="recarrega_pai",
        pai="Car_Pedido",
        coluna_watermark=None,
        linhas_erp=41459,
        doc="Est. 24/28 (base da sugestao de rolos; DtEntrega 100% NULL)",
    ),
)

DERIVADAS: tuple[str, ...] = (
    "Vw_Car_Itens_Pedido",
    "VW_CTE_PECA_EM_ABERTO",
)

ORDEM_CARGA: tuple[str, ...] = (
    "cadastros",
    "faturamento",
    "contas_pagar",
    "contas_receber",
    "custos",
    "fiscal",
    "estoque",
)


@dataclass(frozen=True, slots=True)
class FonteAudit:
    dominio: str
    tabela_erp: str
    tabela_raw: str
    chave_natural: tuple[str, ...]
    coluna_watermark: str | None
    estrategia: str
    ativo: bool
    doc: str
    extras: dict[str, object] = field(default_factory=dict)


def por_dominio(dominio: str | None = None, somente_ativos: bool = True) -> tuple[Fonte, ...]:
    return tuple(
        f
        for f in FONTES
        if (dominio is None or f.dominio == dominio) and (f.ativo or not somente_ativos)
    )


def por_tabela(tabela_erp: str) -> Fonte:
    for fonte in FONTES:
        if fonte.tabela_erp.lower() == tabela_erp.lower():
            return fonte
    raise KeyError(tabela_erp)


FONTES_POR_TABELA: dict[str, Fonte] = {f.tabela_erp.lower(): f for f in FONTES}