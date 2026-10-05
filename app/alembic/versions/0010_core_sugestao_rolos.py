"""Fase D: sugestao de rolos (enderecamento de estoque para atender o pedido).

Porta de `DBMicrodata.dbo.uspEnderecamentoParaAtenderPedidoGeral` (irma **legivel** da
procedure criptografada `DBProDash.dbo.uspEnderecamentoParaAtenderPedidoGeral`):

    1. Itens do pedido: `Vw_Car_Itens_Pedido` -> Produto, Cor, Qtde_Saldo
       (Qtde_Saldo = Qtde - Qtde_Romaneio - Qtde_Acerto)
    2. Rolos disponiveis: `Cte_Peca` LEFT JOIN `CTE_Baixa` por
       (Empresa, Situacao, Nro_Rolo, Nro_Peca), com `Nro_Rolo_Origem IS NULL`
       e `CB.Empresa IS NULL`, filtrando Produto e Cor do item.
    3. `ROW_NUMBER() OVER (ORDER BY Gaveta, Tear DESC, Nro_Rolo DESC)`
    4. `SUM(Metros) OVER (ORDER BY RowNum)` e seleciona
       `Soma_Metros <= Qtde_Saldo OR ABS(Soma_Metros - Qtde_Saldo) < 0.01`
    5. Resultado por item: Sublote = MIN(SubLote), Gavetas distintas e Rolos
       (`RIGHT('0000000000'+Nro_Rolo,10)+RIGHT('000'+Nro_Peca,3)`, ordenados por
       Nro_Rolo/Nro_Peca) concatenados por ', ', Qtde_Pecas e Total_Metros.

Diferencas mecanicas em relacao ao legado:
  - `Tear DESC`: NULL e o menor valor no SQL Server, entao `DESC` joga os rolos sem tear para
    o fim. No Postgres o padrao de `DESC` e NULLS FIRST (o oposto), e por isso a ordem precisa
    ser explicita: `tear DESC NULLS LAST`. Sem isso a sugestao comeca pelo rolo errado.
  - A procedure emite uma linha por **item** do pedido (cursor), nao por
    (Produto, Cor); a view tambem. `Qtde_Item` e `TOP 1 Qtde` de todos os itens do mesmo
    (Pedido, Produto, Cor): sem `ORDER BY`, na pratica a procedure devolve o primeiro item
    (menor `Item`), e e isso que a view reproduz com `first_value`.
  - Item com saldo e **sem rolo disponivel** continua saindo, com `Sublote`/`Gavetas`/`Rolos`/
    `Total_Metros` nulos e `Qtde_Pecas = 0`: e o que o cursor da procedure devolve, e o front
    usa essa linha para avisar que faltam rolos.

Revision ID: 0010_core_sugestao_rolos
Revises: 0009_core_estoque
Create Date: 2026-10-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_core_sugestao_rolos"
down_revision: str | None = "0009_core_estoque"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VIEW_ROLOS_DISPONIVEIS = """
CREATE OR REPLACE VIEW core.estoque_rolos_disponiveis AS
SELECT
    cp.empresa,
    cp.situacao,
    cp.nro_rolo,
    cp.nro_peca,
    cp.produto,
    cp.cor,
    cp.metros,
    cp.gaveta,
    cp.tear,
    cp.sub_lote,
    cp.data_entrada
FROM raw.cte_peca cp
WHERE cp.nro_rolo_origem IS NULL
  AND NOT EXISTS (
    SELECT 1
    FROM raw.cte_baixa cb
    WHERE cb.empresa = cp.empresa
      AND cb.situacao = cp.situacao
      AND cb.nro_rolo = cp.nro_rolo
      AND cb.nro_peca = cp.nro_peca
  )
"""

VIEW_ACUMULADO = """
CREATE OR REPLACE VIEW core.sugestao_rolos_acumulado AS
SELECT
    produto,
    cor,
    nro_rolo,
    nro_peca,
    metros,
    gaveta,
    sub_lote,
    row_number() OVER (
        PARTITION BY produto, cor
        ORDER BY gaveta, tear DESC NULLS LAST, nro_rolo DESC
    ) AS row_num,
    sum(metros) OVER (
        PARTITION BY produto, cor
        ORDER BY gaveta, tear DESC NULLS LAST, nro_rolo DESC
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) AS soma_metros
FROM core.estoque_rolos_disponiveis
"""

VIEW_SUGESTAO_ROLOS = """
CREATE OR REPLACE VIEW core.pedido_sugestao_rolos AS
WITH itens AS (
    SELECT
        empresa,
        pedido,
        item,
        produto,
        cor,
        qtde,
        (qtde - coalesce(qtde_romaneio, 0) - coalesce(qtde_acerto, 0)) AS qtde_saldo,
        -- a procedure le `TOP 1 Qtde` de todos os itens do mesmo (pedido, produto, cor)
        first_value(qtde) OVER (
            PARTITION BY empresa, pedido, produto, cor ORDER BY item
        )                                          AS qtde_item
    FROM raw.car_itens_pedido
    WHERE (qtde - coalesce(qtde_romaneio, 0) - coalesce(qtde_acerto, 0)) > 0
),
sugestoes AS (
    SELECT
        i.empresa,
        i.pedido,
        i.item,
        min(a.sub_lote)                               AS sublote,
        string_agg(DISTINCT btrim(a.gaveta), ', ' ORDER BY btrim(a.gaveta)) AS gavetas,
        string_agg(
            lpad(a.nro_rolo, 10, '0') || lpad(a.nro_peca, 3, '0'),
            ', ' ORDER BY a.nro_rolo, a.nro_peca
        )                                             AS rolos,
        count(*)                                      AS qtde_pecas,
        sum(a.metros)                                 AS total_metros
    FROM itens i
    JOIN core.sugestao_rolos_acumulado a
        ON a.produto = i.produto
       AND a.cor = i.cor
    WHERE a.soma_metros <= i.qtde_saldo
       OR abs(a.soma_metros - i.qtde_saldo) < 0.01
    GROUP BY i.empresa, i.pedido, i.item
)
SELECT
    i.empresa,
    i.pedido,
    i.item,
    i.produto,
    i.cor,
    i.qtde_item,
    i.qtde_saldo,
    s.sublote,
    s.gavetas,
    s.rolos,
    coalesce(s.qtde_pecas, 0) AS qtde_pecas,
    s.total_metros
FROM itens i
LEFT JOIN sugestoes s
    ON s.empresa = i.empresa
   AND s.pedido = i.pedido
   AND s.item = i.item
"""


def upgrade() -> None:
    op.execute(VIEW_ROLOS_DISPONIVEIS)
    op.execute(
        "COMMENT ON VIEW core.estoque_rolos_disponiveis IS "
        "'Rolos disponiveis para a sugestao (peca sem baixa e sem rolo de origem) - "
        "regra do uspEnderecamentoParaAtenderPedidoGeral'"
    )

    op.execute(VIEW_ACUMULADO)
    op.execute(
        "COMMENT ON VIEW core.sugestao_rolos_acumulado IS "
        "'Metros acumulados por produto/cor na ordem Gaveta, Tear DESC, Rolo DESC'"
    )

    op.execute(VIEW_SUGESTAO_ROLOS)
    op.execute(
        "COMMENT ON VIEW core.pedido_sugestao_rolos IS "
        "'Sugestao de rolos por item de pedido (porta de uspEnderecamentoParaAtenderPedidoGeral)'"
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS core.pedido_sugestao_rolos")
    op.execute("DROP VIEW IF EXISTS core.sugestao_rolos_acumulado")
    op.execute("DROP VIEW IF EXISTS core.estoque_rolos_disponiveis")