"""Testes da Fase B: normalizacao, DDL gerado, SQL de carga e leitor do ERP."""

from __future__ import annotations

from src.etl import sources
from src.etl.extract import introspect, reader
from src.etl.extract.introspect import ColunaERP, ColunaPG
from src.etl.load.upsert import _upsert_sql
from src.etl.transform.naming import identificar, snake_case


def coluna(nome: str, tipo: str, **extra) -> ColunaERP:
    return ColunaERP(
        nome=nome,
        tipo=tipo,
        base=extra.get("base", tipo),
        tamanho=extra.get("tamanho"),
        precisao=extra.get("precisao"),
        escala=extra.get("escala", 0),
        nullable=extra.get("nullable", True),
    )


def test_snake_case_erp_para_pg():
    assert snake_case("Codigo_Pagto") == "codigo_pagto"
    assert snake_case("DescricaoPagto") == "descricao_pagto"
    assert snake_case("NrNotaFiscal") == "nr_nota_fiscal"
    assert snake_case("BairroCobrança") == "bairro_cobranca"


def test_identificador_sem_aspas():
    assert identificar("carga_em") == "carga_em"
    assert identificar("ordem de compra") == '"ordem de compra"'
    assert identificar('tabela"injetada') == '"tabela""injetada"'


def test_colunas_duplicadas_recebem_sufixo():
    colunas = [
        ColunaPG("bairro_cobranca", "varchar(30)", "BairroCobranca"),
        ColunaPG("bairro_cobranca", "varchar(30)", "BairroCobrança"),
    ]
    saida = introspect.deduplicar(colunas)
    assert [c.nome for c in saida] == ["bairro_cobranca", "bairro_cobranca_2"]
    assert [c.origem for c in saida] == ["BairroCobranca", "BairroCobrança"]


def test_mapeamento_de_tipos():
    assert introspect.mapear_tipo(coluna("a", "char", tamanho=2)) == "varchar(2)"
    assert introspect.mapear_tipo(coluna("a", "nvarchar", tamanho=30)) == "varchar(30)"
    assert introspect.mapear_tipo(coluna("a", "varchar", tamanho=-1)) == "text"
    assert introspect.mapear_tipo(coluna("a", "text")) == "text"
    assert introspect.mapear_tipo(coluna("a", "tinyint")) == "smallint"
    assert introspect.mapear_tipo(coluna("a", "bit")) == "boolean"
    assert introspect.mapear_tipo(coluna("a", "smalldatetime")) == "timestamp"
    assert introspect.mapear_tipo(coluna("a", "datetimeoffset")) == "timestamptz"
    assert introspect.mapear_tipo(coluna("a", "uniqueidentifier")) == "uuid"
    assert introspect.mapear_tipo(coluna("a", "decimal", precisao=16, escala=4)) == "numeric(16,4)"
    assert introspect.mapear_tipo(coluna("a", "money")) == "numeric(19,4)"


def test_alias_tipo_usa_tamanho_do_alias():
    """Pagto (alias de char(2)) nao pode virar text nem varchar(8000)."""
    coluna_pagto = coluna("Codigo_Pagto", "Pagto", base="char", tamanho=2)
    assert introspect.mapear_tipo(coluna_pagto) == "varchar(2)"


def test_comentario_com_apostofo_gerado_em_sql_valido():
    fonte = sources.por_tabela("Pag_DespesasReceitas")
    colunas = [
        ColunaPG("e_s", "integer", "E_S"),
        ColunaPG("descricao", "varchar(80)", "Descricao"),
    ]
    ddl = introspect.criar_tabela_sql(fonte, colunas, ["e_s"])
    comentario = next(comando for comando in ddl if comando.startswith("COMMENT ON TABLE"))
    assert "''Quarto_Nivel''" in comentario
    assert comentario.count("'") % 2 == 0


def test_comentario_com_percent_nao_quebra_o_execucao():
    """`doc` com % roda em text() (exec_driver_sql interpretaria como placeholder)."""
    fonte = sources.por_tabela("Clientes_Principal")
    colunas = [ColunaPG("codigo_cliente", "integer", "Codigo_Cliente")]
    comentario = next(
        comando
        for comando in introspect.criar_tabela_sql(fonte, colunas, ["codigo_cliente"])
        if comando.startswith("COMMENT ON TABLE")
    )
    assert "100% NULL" in comentario


def test_largura_usa_o_maior_entre_declarado_e_observado():
    declarado = coluna("Inscricao_Municipal", "char", tamanho=7)
    assert introspect.mapear_tipo(declarado) == "varchar(7)"
    assert introspect.mapear_tipo(declarado, observado=8) == "varchar(8)"
    assert introspect.mapear_tipo(declarado, observado=5000) == "text"


def test_upsert_atualiza_apenas_fora_da_chave():
    sql = _upsert_sql("fat_pedido", ["empresa", "pedido", "valor"], ["empresa", "pedido"])
    assert "on conflict (empresa, pedido)" in sql
    assert "valor = excluded.valor" in sql
    assert "empresa = excluded.empresa" not in sql
    assert sql.endswith("_carga_em = now()")


def test_leitor_monta_paginacao_e_limite():
    sql, params = reader._base("Fat_Pedido", ["Empresa", "Pedido"], None, None, None)
    assert "select [Empresa], [Pedido] from [Fat_Pedido]" in sql
    assert params == []

    sql, params = reader._base("Fat_Pedido", ["Pedido"], "Data_Hora", "2026-01-01", 500)
    assert sql.startswith("select top 500 ")
    assert "where [Data_Hora] > ?" in sql
    assert params == ["2026-01-01"]


def test_fontes_com_watermark_tem_cobertura_util():
    """Fontes declaradas como watermark nao podem ter coluna inutilizavel."""
    invalidas = {
        "Clientes_Principal",
        "Fat_Pedido",
        "Fat_Itens_Pedido",
        "Car_Pedido",
        "Car_Itens_Pedido",
    }
    for fonte in sources.FONTES:
        if fonte.estrategia == "watermark":
            assert fonte.coluna_watermark, fonte.tabela_erp
        if fonte.tabela_erp in invalidas:
            assert fonte.coluna_watermark is None
            assert fonte.estrategia in {"full", "recarrega_pai"}


def test_fontes_recarregam_o_pai():
    for fonte in sources.FONTES:
        if fonte.estrategia == "recarrega_pai":
            pai = sources.por_tabela(fonte.pai)
            assert pai.estrategia != "recarrega_pai", fonte.tabela_erp


def test_app_expoe_openapi():
    from src.api.main import app

    assert app.title == "api-microdata"
    assert "/health" in {rota for rota in app.openapi()["paths"]}