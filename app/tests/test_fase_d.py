"""Testes das regras da Fase D (core/marts portados do DBProDash)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

from scripts.validar_fase_d import meses_recentes

VERSOES = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def carregar_migration(nome: str):
    """`alembic` e um pacote instalado: importa a migracao pelo caminho do arquivo."""
    caminho = VERSOES / f"{nome}.py"
    spec = importlib.util.spec_from_file_location(f"migracao_{nome}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


migration_core = carregar_migration("0005_core_faturamento")
migration_marts = carregar_migration("0006_marts_faturamento")
migration_financeiro = carregar_migration("0007_core_financeiro")
migration_estornos = carregar_migration("0008_marts_estornos")
migration_estoque = carregar_migration("0009_core_estoque")


class TestWhitelistCFOP:
    def test_whitelist_tem_104_pares_sem_duplicata(self):
        pares = migration_core.CFOP_FATURAMENTO
        assert len(pares) == 104
        assert len(set(pares)) == len(pares)

    def test_cfops_de_entrada_e_saida_do_dashboard(self):
        pares = set(migration_core.CFOP_FATURAMENTO)
        for esperado in ("1.101", "2.201", "5.101", "6.102"):
            assert any(nat_op == esperado for nat_op, _ in pares), esperado

    def test_nao_entra_o_que_a_view_irma_traz_e_o_legado_nao_usa(self):
        """5.911/1 e 6.901/1 existem em Vw_Fat_Saida_Qlik mas nao em vwFaturamento."""
        pares = set(migration_core.CFOP_FATURAMENTO)
        assert ("5.911", "1") not in pares
        assert ("6.901", "1") not in pares


class TestViewFaturamento:
    def test_filtros_da_regra_do_dashboard(self):
        ddl = migration_core.VIEW_FATURAMENTO_ITENS
        assert "tp.flag_emitido = '1'" in ddl
        assert "tp.tipo_pedido = '1'" in ddl
        assert "ti.base_calc <> '-'" in ddl
        assert "core.cfop_faturamento" in ddl
        assert "core.empresa_faturamento" in ddl

    def test_metros_segue_a_base_de_calculo(self):
        ddl = migration_core.VIEW_FATURAMENTO_ITENS
        assert "CASE WHEN ti.base_calc IN ('P', 'M') THEN ti.metros ELSE ti.qtde END" in ddl

    def test_marts_somam_valor_e_desconto(self):
        diario = migration_marts.VIEW_FATURAMENTO_DIARIO
        assert "sum(vr_total) + sum(acres_desc)" in diario
        assert "AS faturamento" in diario
        assert "date_trunc('month', data_nota)::date" in (
            migration_marts.VIEW_FATURAMENTO_MENSAL
        )


class TestContasPagasETitulosEmAberto:
    def test_mart_de_baixas_usa_pag_baixas(self):
        ddl = migration_financeiro.VIEW_CONTAS_PAGAS_DIARIO
        assert "FROM raw.pag_baixas" in ddl
        assert "sum(coalesce(valor_pago, 0))           AS valor_pago" in ddl
        assert "date_trunc('month', data_baixa)::date" in (
            migration_financeiro.VIEW_CONTAS_PAGAS_MENSAL
        )

    def test_titulo_em_aberto_exige_saldo_positivo(self):
        ddl = migration_financeiro.VIEW_PAG_TITULO_ABERTO
        assert "coalesce(b.valor_baixas, 0) < coalesce(p.valor, 0)" in ddl
        assert "FROM raw.nfe_parcelas p" in ddl
        assert "JOIN raw.nf_entradas nf" in ddl

    def test_duplicata_em_aberto_agrupa_as_baixas_por_parcela(self):
        ddl = migration_financeiro.VIEW_REC_DUPLICATAS_ABERTO
        assert "FROM raw.rec_baixas" in ddl
        assert "GROUP BY empresa, documento, serie, parcela" in ddl
        assert "coalesce(np.valor_parcelas, 0) - coalesce(rb.valor_liquido, 0) > 0" in ddl

    def test_programados_ficam_prescritos_as_empresas_do_faturamento(self):
        """A janela de vencimento (1o dia do mes seguinte ate 2050-12-31) e da API."""
        for ddl in (
            migration_financeiro.VIEW_PAGAR_PROGRAMADO,
            migration_financeiro.VIEW_RECEBER_PROGRAMADO,
        ):
            assert "core.empresa_faturamento" in ddl


class TestEstornosEDevolucoes:
    def test_estorno_filtra_tipo_4_natureza_de_estorno_e_empresa_13(self):
        ddl = migration_estornos.VIEW_ESTORNOS_ITENS
        assert "tp.flag_emitido = '1'" in ddl
        assert "tp.tipo_pedido = '4'" in ddl
        assert "tp.empresa = '13'" in ddl
        assert "i.base_calc <> '-'" in ddl
        assert "btrim(np.nat_op) || np.seq IN ('1.1021', '2.1021')" in ddl

    def test_devolucoes_usa_a_chave_com_fornecedor(self):
        """O mesmo numero de documento e reaproveitado por fornecedores diferentes."""
        ddl = migration_estornos.VIEW_DEVOLUCOES
        assert "e.tipo_fornec = n.tipo_fornec" in ddl
        assert "e.fornecedor = n.fornecedor" in ddl
        assert (
            "IN ('1.201-1', '1.201-2', '1.202-1', '2.202-1')" in ddl
        ), "CFOPs de devolucao do uspDevolucao"

    def test_marts_agrupam_por_mes_e_por_dia(self):
        assert "date_trunc('month', emissao)::date" in migration_estornos.VIEW_ESTORNOS_MENSAL
        assert "date_trunc('month', data)::date" in migration_estornos.VIEW_DEVOLUCOES_MENSAL
        assert "emissao::date                           AS data" in (
            migration_estornos.VIEW_ESTORNOS_DIARIO
        )


class TestEstoqueEmAberto:
    def test_regra_e_o_antijoin_com_as_baixas(self):
        ddl = migration_estoque.VIEW_ESTOQUE_EM_ABERTO
        assert "FROM raw.cte_baixa b" in ddl
        assert "WHERE NOT EXISTS" in ddl
        assert "b.empresa = a.empresa" in ddl
        assert "b.nro_rolo = a.nro_rolo" in ddl
        assert "b.nro_peca = a.nro_peca" in ddl

    def test_joins_de_cadastro_preservam_o_interno_e_o_left(self):
        ddl = migration_estoque.VIEW_ESTOQUE_EM_ABERTO
        for tabela in ("produtos", "car_situacoes", "car_cores", "car_desenhos", "car_categorias"):
            assert f"JOIN raw.{tabela}" in ddl, tabela
        assert "LEFT JOIN raw.car_variante cv" in ddl

    def test_nao_filtra_por_rolo_de_origem(self):
        """A view canonica nao usa Nro_Rolo_Origem: a regra e so o antijoin."""
        assert "nro_rolo_origem IS NULL" not in migration_estoque.VIEW_ESTOQUE_EM_ABERTO

    def test_saldo_agrega_por_combinacao_de_cadastros(self):
        ddl = migration_estoque.VIEW_ESTOQUE_SALDO
        assert "GROUP BY produto, situacao, cor, desenho, categoria, variante" in ddl
        assert "sum(coalesce(metros, 0))             AS metros" in ddl


class TestJanelasDeMes:
    def test_treze_meses_comecam_no_mes_anterior(self):
        janelas = meses_recentes(13, referencia=date(2026, 10, 2))
        assert janelas[0] == (date(2025, 10, 1), date(2025, 11, 1))
        assert janelas[-1] == (date(2026, 10, 1), date(2026, 11, 1))
        assert len(janelas) == 13

    def test_vira_o_ano_e_em_dezembro(self):
        janelas = meses_recentes(3, referencia=date(2026, 2, 10))
        assert janelas == [
            (date(2025, 12, 1), date(2026, 1, 1)),
            (date(2026, 1, 1), date(2026, 2, 1)),
            (date(2026, 2, 1), date(2026, 3, 1)),
        ]

    def test_janelas_sao_contiguas(self):
        janelas = meses_recentes(6, referencia=date(2026, 10, 2))
        for (_, fim), (proximo, _) in zip(janelas, janelas[1:], strict=False):
            assert fim == proximo


@pytest.mark.parametrize("quantidade", [1, 12, 13, 24])
def test_quantidade_de_meses_e_respeitada(quantidade):
    assert len(meses_recentes(quantidade, referencia=date(2026, 10, 2))) == quantidade