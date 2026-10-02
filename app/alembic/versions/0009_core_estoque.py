"""Fase D: estoque em aberto (pecas sem baixa) e saldo agregado.

Porta da `DBMicrodata.dbo.VW_CTE_PECA_EM_ABERTO` (legivel, 18.408 pecas):

    CTE_Peca A
    INNER JOIN Produtos C        ON (C.Empresa = A.EmpProd AND C.Codigo = A.Produto)
    INNER JOIN Car_Situacoes G   ON (G.Codigo = A.Situacao)
    INNER JOIN Car_Cores D       ON (D.Codigo = A.Cor)
    INNER JOIN Car_Desenhos E    ON (E.Codigo = A.Desenho)
    INNER JOIN Car_Categorias F  ON (F.Codigo = A.Categoria)
    LEFT  JOIN Car_Variante V    ON (V.Codigo = A.Variante)
    WHERE NOT EXISTS (SELECT 1 FROM CTE_Baixa B
                      WHERE B.Empresa = A.Empresa AND B.Nro_Rolo = A.Nro_Rolo
                        AND B.Nro_Peca = A.Nro_Peca)

Nao existe filtro por `Nro_Rolo_Origem`: a regra e apenas o antijoin com as baixas.

Revision ID: 0009_core_estoque
Revises: 0008_marts_estornos
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_core_estoque"
down_revision: str | None = "0008_marts_estornos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_ESTOQUE_EM_ABERTO = """
CREATE OR REPLACE VIEW core.estoque_pecas_em_aberto AS
SELECT
    a.empresa,
    a.nro_rolo,
    a.nro_peca,
    a.metros,
    a.peso,
    a.peso_bruto,
    a.produto,
    p.descricao                                       AS prod_desc,
    a.situacao,
    s.descricao                                       AS situacao_desc,
    a.cor,
    cc.descricao                                      AS cor_desc,
    a.desenho,
    cd.descricao                                      AS desenho_desc,
    a.categoria,
    cat.descricao                                     AS categoria_desc,
    a.variante,
    cv.descricao                                      AS variante_desc,
    cd.largura,
    a.data_entrada,
    a.gaveta,
    a.nro_rolo_origem,
    a.bloqueado,
    a.terceiros,
    a.devolucao,
    a.ordem_producao,
    a.ficha_tecnica,
    a.nro_fardo,
    a.cod_acond,
    a.custo_unitario
FROM raw.cte_peca a
JOIN raw.produtos p
    ON p.empresa = a.emp_prod
   AND p.codigo = a.produto
JOIN raw.car_situacoes s
    ON s.codigo = a.situacao
JOIN raw.car_cores cc
    ON cc.codigo = a.cor
JOIN raw.car_desenhos cd
    ON cd.codigo = a.desenho
JOIN raw.car_categorias cat
    ON cat.codigo = a.categoria
LEFT JOIN raw.car_variante cv
    ON cv.codigo = a.variante
WHERE NOT EXISTS (
    SELECT 1
    FROM raw.cte_baixa b
    WHERE b.empresa = a.empresa
      AND b.nro_rolo = a.nro_rolo
      AND b.nro_peca = a.nro_peca
)
"""

VIEW_ESTOQUE_SALDO = """
CREATE OR REPLACE VIEW marts.estoque_saldo AS
SELECT
    produto,
    max(prod_desc)                       AS prod_desc,
    situacao,
    max(situacao_desc)                   AS situacao_desc,
    cor,
    max(cor_desc)                        AS cor_desc,
    desenho,
    max(desenho_desc)                    AS desenho_desc,
    categoria,
    max(categoria_desc)                  AS categoria_desc,
    variante,
    max(variante_desc)                   AS variante_desc,
    count(*)                             AS pecas,
    count(DISTINCT nro_rolo)             AS rolos,
    sum(coalesce(metros, 0))             AS metros,
    sum(coalesce(peso, 0))               AS peso,
    sum(coalesce(peso_bruto, 0))         AS peso_bruto,
    min(data_entrada)                    AS primeira_entrada,
    max(data_entrada)                    AS ultima_entrada
FROM core.estoque_pecas_em_aberto
GROUP BY produto, situacao, cor, desenho, categoria, variante
"""


def upgrade() -> None:
    op.execute(VIEW_ESTOQUE_EM_ABERTO)
    op.execute(
        "COMMENT ON VIEW core.estoque_pecas_em_aberto IS "
        "'Porta de DBMicrodata.dbo.VW_CTE_PECA_EM_ABERTO: CTE_Peca sem baixa em CTE_Baixa'"
    )

    op.execute(VIEW_ESTOQUE_SALDO)
    op.execute(
        "COMMENT ON VIEW marts.estoque_saldo IS "
        "'Saldo agregado do estoque em aberto, por produto/situacao/cor/desenho/categoria/variante'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS marts.estoque_saldo")
    op.execute("DROP VIEW IF EXISTS core.estoque_pecas_em_aberto")