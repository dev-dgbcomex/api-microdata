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