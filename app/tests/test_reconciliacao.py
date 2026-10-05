"""Testes da reconciliacao de linhas apagadas no ERP (`upsert.apagar_ausentes`)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from src.db.warehouse import engine
from src.etl.load.upsert import _sql_apagar_ausentes, apagar_ausentes

TABELA = "_teste_apagar_ausentes"
CHAVE = ("empresa", "pedido")
TIPOS = {"empresa": "character(2)", "pedido": "character(8)"}


class TestSql:
    def test_sem_nulo_usa_igual_para_habilitar_hash_anti_join(self):
        _, _, _, apagar = _sql_apagar_ausentes(TIPOS, CHAVE, nulos=False)
        assert "v.c0 = t.empresa" in apagar
        assert "is not distinct from" not in apagar

    def test_com_nulo_cai_no_comparador_seguro(self):
        _, _, _, apagar = _sql_apagar_ausentes(TIPOS, CHAVE, nulos=True)
        assert "v.c0 is not distinct from t.empresa" in apagar

    def test_temporaria_tem_tipo_e_indice(self):
        criar, inserir, indexar, _ = _sql_apagar_ausentes(TIPOS, CHAVE, nulos=False)
        assert "c0 character(2)" in criar
        assert "c1 character(8)" in criar
        assert "cast(%(empresa)s as character(2)[])" in inserir
        assert "create index ix_chaves_vistas" in indexar


@pytest.fixture
def tabela_limpa():
    """Cria uma tabela `raw` de teste; pula se o Postgres local nao estiver de pe."""
    try:
        with engine().begin() as conn:
            conn.exec_driver_sql(f"drop table if exists raw.{TABELA}")
            conn.exec_driver_sql(
                f"create table raw.{TABELA} "
                "(empresa char(2) not null, pedido char(8) not null, valor numeric, "
                "primary key (empresa, pedido))"
            )
    except Exception as erro:  # pragma: no cover - depende do ambiente
        pytest.skip(f"warehouse local indisponivel: {erro}")
    yield TABELA
    with engine().begin() as conn:
        conn.exec_driver_sql(f"drop table if exists raw.{TABELA}")


def _linhas(*pares: tuple[str, str, int]) -> None:
    with engine().begin() as conn:
        for empresa, pedido, valor in pares:
            conn.execute(
                text(
                    f"insert into raw.{TABELA} (empresa, pedido, valor) "
                    "values (:empresa, :pedido, :valor)"
                ),
                {"empresa": empresa, "pedido": pedido, "valor": valor},
            )


def _pedidos() -> list[str]:
    with engine().connect() as conn:
        return [
            str(linha[0]).strip()
            for linha in conn.execute(text(f"select pedido from raw.{TABELA} order by pedido"))
        ]


class TestApagarAusentes:
    def test_apaga_documento_que_sumiu_do_erp(self, tabela_limpa):
        _linhas(("13", "00000001", 10), ("13", "00000002", 20), ("13", "00000003", 30))
        apagadas = apagar_ausentes(
            engine(), TABELA, list(CHAVE), [("13", "00000001"), ("13", "00000003")]
        )
        assert apagadas == 1
        assert _pedidos() == ["00000001", "00000003"]

    def test_nao_apaga_nada_quando_tudo_esta_no_erp(self, tabela_limpa):
        _linhas(("13", "00000001", 10), ("13", "00000002", 20))
        apagadas = apagar_ausentes(
            engine(), TABELA, list(CHAVE), [("13", "00000001"), ("13", "00000002")]
        )
        assert apagadas == 0
        assert _pedidos() == ["00000001", "00000002"]

    def test_lista_vazia_apaga_tudo(self, tabela_limpa):
        """Carga parcial e bloqueada antes; se chegar aqui, o comportamento e o do ERP vazio."""
        _linhas(("13", "00000001", 10))
        assert apagar_ausentes(engine(), TABELA, list(CHAVE), []) == 1
        assert _pedidos() == []

    def test_chave_nula_e_preservada(self, tabela_limpa):
        _linhas(("13", "00000001", 10))
        apagadas = apagar_ausentes(engine(), TABELA, list(CHAVE), [(None, "00000001")])
        assert apagadas == 1
        assert _pedidos() == []

    def test_coluna_desconhecida_nao_apaga_nada(self, tabela_limpa):
        """Rede de seguranca: sem os tipos nao se monta o SQL."""
        _linhas(("13", "00000001", 10))
        assert apagar_ausentes(engine(), TABELA, ["empresa", "inexistente"], []) == 0
        assert _pedidos() == ["00000001"]