"""Testes da Fase E: contratos da API sobre o warehouse local (sem tocar no ERP)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers import kpis
from src.db import erp

client = TestClient(app)


class TestRotasPublicadas:
    def test_publica_o_contrato_do_legado_mais_o_health(self):
        rotas = set(app.openapi()["paths"])
        esperadas = {
            "/dados",
            "/sugestao-rolos/{pedido}",
            "/faturamento/{data}",
            "/faturamento-dia/{data}",
            "/contas-pagas/{data}",
            "/custos-administrativos-anual",
            "/custos-administrativos-mensal",
            "/descontos/{data}",
            "/devolucoes/{data}",
            "/estornos/{data}",
            "/contas-receber-programado",
            "/contas-pagar-programado",
            "/dashboard-completo/{data}",
            "/health",
        }
        assert esperadas <= rotas, sorted(esperadas - rotas)

    def test_rotas_de_debug_do_legado_nao_sao_portadas(self):
        """`/procedures` e `/test-procedure/{nome}` faziam EXEC de qualquer nome no ERP."""
        rotas = set(app.openapi()["paths"])
        assert "/procedures" not in rotas
        assert "/test-procedure/{procedure_name}" not in rotas

    def test_pdf_de_sugestao_ainda_nao_existe(self):
        """Fica para o pedaco seguinte da Fase E (o legado gerava os cartoes)."""
        assert "/pdf/sugestao-rolos/{pedido}" not in app.openapi()["paths"]

    def test_data_invalida_cai_com_422(self):
        assert client.get("/faturamento/15-03-2026").status_code == 422

    def test_limite_do_dados_e_limitado(self):
        assert client.get("/dados?limite=99999").status_code == 422
        assert client.get("/dados?limite=0").status_code == 422


class TestJanelasDeData:
    def test_mes_seguinte_vira_ano(self):
        assert kpis._proximo_mes(date(2026, 12, 15)) == date(2027, 1, 1)

    def test_mes_anterior_vira_ano(self):
        assert kpis._mes_anterior(date(2026, 1, 1)) == date(2025, 12, 1)

    def test_primeiro_dia_sobra_a_data(self):
        assert kpis._primeiro_dia(date(2026, 3, 15)) == date(2026, 3, 1)

    def test_doze_mes_atras_da_janela_do_anual(self):
        assert kpis._mes_anterior_doze(date(2026, 10, 1)) == date(2025, 10, 1)


class TestGuardDeProcedureNoERP:
    """`assert_read_only` so deixa passar SELECT e as `usp` de leitura conhecidas."""

    @pytest.mark.parametrize(
        "sql",
        [
            "select 1",
            "WITH x AS (select 1) select * from x",
            "exec DBProDash.dbo.uspFaturamento ?",
            "EXEC DBProDash.dbo.uspCustoAdmArmFat",
            "exec [DBProDash].[dbo].[uspDesconto] ?",
        ],
    )
    def test_permite_leitura(self, sql):
        erp.assert_read_only(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "update x set y = 1",
            "delete from x",
            "exec DBProDash.dbo.uspAtualizaPreco",
            "exec DBProDash.dbo.sp_PagRel_CCusto_Niveis",
            "exec @proc",
            "EXEC sp_executesql 'select 1'",
            "exec DBProDash.dbo.uspFaturamento; drop table x",
            "exec DBProDash.dbo.uspFaturamento -- ; delete from x",
        ],
    )
    def test_bloqueia_escrita_e_procedure_desconhecida(self, sql):
        with pytest.raises(PermissionError):
            erp.assert_read_only(sql)

    def test_procedure_que_escreve_nao_entra(self):
        """As `uspRel_CCusto_Niveis*` e a `sp_PagRel_CCusto_Niveis` fazem TRUNCATE+INSERT."""
        for nome in (
            "usprel_ccusto_niveisanual",
            "usprel_ccusto_niveismensal",
            "sp_pagrel_ccusto_niveis",
        ):
            assert nome not in erp.PROCEDURES_SOMENTE_LEITURA, nome

    def test_nomes_da_whitelist_sao_case_insensitive(self):
        assert "uspfaturamento" in erp.PROCEDURES_SOMENTE_LEITURA
        assert "uspdesconto" in erp.PROCEDURES_SOMENTE_LEITURA