"""Testes do sync on-demand dos marts pequenos no Neon (Fase E, D4)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.config import get_settings
from src.db import neon, warehouse
from src.db.postgres import create_pg_engine
from src.etl import publicar

MENOR = "custos_administrativo_mensal"


class TestRegistro:
    def test_cobre_exatamente_os_marts_do_dashboard(self):
        assert set(publicar.MARTS_DO_DASHBOARD) == set(publicar.COLUNAS)

    def test_nenhuma_coluna_inventada(self):
        """Se o mart local ganhar coluna, o mapa para de bater (falha aqui, nao no front)."""
        with warehouse.engine().connect() as conn:
            for mart, colunas in publicar.COLUNAS.items():
                do_banco = [
                    linha[0]
                    for linha in conn.execute(
                        text(
                            "select column_name from information_schema.columns "
                            "where table_schema = 'marts' and table_name = :t "
                            "order by ordinal_position"
                        ),
                        {"t": mart},
                    )
                ]
                assert list(colunas) == do_banco, mart

    def test_marts_pesados_nao_sobem_para_o_neon(self):
        """Grao de peca/linha de pedido nao vai para o Neon (Doc 44 §4)."""
        for pesada in ("estoque_pecas_em_aberto", "pedido_sugestao_rolos"):
            assert pesada not in publicar.COLUNAS

    def test_mart_desconhecido_e_recusado(self):
        with pytest.raises(ValueError, match="nao publicavel"):
            publicar.publicar("nao_existe")


class TestDefasagem:
    def test_versao_local_ultima_carga_ok(self):
        assert publicar.versao_local() is not None

    def test_forcar_publica_tudo(self):
        a_publicar, em_dia = publicar.defasados(publicar.MARTS_DO_DASHBOARD, forcar=True)
        assert a_publicar == list(publicar.MARTS_DO_DASHBOARD)
        assert em_dia == []

    def test_publicado_depois_da_carga_nao_esta_defasado(self):
        publicar.sincronizar([MENOR], forcar=True)
        a_publicar, em_dia = publicar.defasados([MENOR])
        assert a_publicar == []
        assert em_dia == [MENOR]

    def test_carga_mais_nova_e_publicada_de_novo(self, monkeypatch: pytest.MonkeyPatch):
        publicar.sincronizar([MENOR], forcar=True)
        import datetime as dt

        futuro = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)
        monkeypatch.setattr(publicar, "versao_local", lambda engine=None: futuro)
        a_publicar, em_dia = publicar.defasados([MENOR])
        assert a_publicar == [MENOR]
        assert em_dia == []

    def test_sem_execucao_local_publica_tudo(self, monkeypatch: pytest.MonkeyPatch):
        """Warehouse sem carga nenhuma = nada publicado antes: publica (fail-safe)."""
        monkeypatch.setattr(publicar, "versao_local", lambda engine=None: None)
        a_publicar, _ = publicar.defasados([MENOR])
        assert a_publicar == [MENOR]


class TestPublicacao:
    def test_copia_o_mart_e_registra(self):
        linhas = publicar.publicar(MENOR, motivo="teste")
        with warehouse.engine().connect() as local, neon.engine().connect() as destino:
            esperado = local.execute(
                text(f"select count(*) from marts.{MENOR}")
            ).scalar_one()
            obtido = destino.execute(text(f"select count(*) from marts.{MENOR}")).scalar_one()
            registro = destino.execute(
                text("select motivo, linhas from etl.marts_publicados where mart = :m"),
                {"m": MENOR},
            ).one()
        assert linhas == esperado == obtido
        assert registro.linhas == esperado
        assert registro.motivo == "teste"

    def test_conteudo_igual_ao_warehouse(self):
        publicar.publicar(MENOR)
        with warehouse.engine().connect() as local, neon.engine().connect() as destino:
            a = local.execute(
                text(f"select mes, parcelas, valor_baixado from marts.{MENOR} order by mes")
            ).fetchall()
            b = destino.execute(
                text(f"select mes, parcelas, valor_baixado from marts.{MENOR} order by mes")
            ).fetchall()
        assert [tuple(linha) for linha in a] == [tuple(linha) for linha in b]

    def test_republicar_nao_duplica(self):
        publicar.publicar(MENOR)
        antes = _linhas_no_neon(MENOR)
        publicar.publicar(MENOR)
        assert _linhas_no_neon(MENOR) == antes

    def test_sincronizar_ignora_o_que_esta_em_dia(self):
        publicar.sincronizar([MENOR], forcar=True)
        resultado = publicar.sincronizar([MENOR])
        assert resultado.publicados == ()
        assert resultado.ignorados == (MENOR,)


class TestBestEffort:
    def test_desligado_por_padrao(self):
        assert get_settings().neon_publicar_automatico is False
        assert publicar.sincronizar_antes_do_dashboard() is None

    def test_falha_do_sync_nao_derruba_a_rota(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(get_settings(), "neon_publicar_automatico", True)
        quebrado = create_pg_engine(
            "postgresql+psycopg://inexistente:inexistente@127.0.0.1:1/inexistente"
            "?connect_timeout=2",
            pool_size=1,
            max_overflow=0,
        )
        monkeypatch.setattr(publicar.neon, "engine", lambda: quebrado)
        assert publicar.sincronizar_antes_do_dashboard() is None


def _linhas_no_neon(mart: str) -> int:
    with neon.engine().connect() as conn:
        return conn.execute(text(f"select count(*) from marts.{mart}")).scalar_one()