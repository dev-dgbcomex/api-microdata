"""Rotas de negócio: peças em aberto e sugestão de rolos (contrato do legado, Doc 44 #1 e #2).

O legado fazia `fetchall` de ~300 mil peças por request e chamava a procedure
`uspEnderecamentoParaAtenderPedidoGeral` (que abre cursor e monta XML). Aqui as duas saem
de views do warehouse local — `/dados` usa cursor com filtros e paginação, e a sugestão
reproduz a mesma regra número a número (validada em `scripts.validar_fase_d sugestao_rolos`).
"""

from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import text

from src.api.auth.dependencias import exigir_autenticado
from src.db import warehouse

router = APIRouter(tags=["estoque"], dependencies=[Depends(exigir_autenticado)])

COLUNAS_PECA = """
    empresa,
    nro_rolo,
    nro_peca,
    produto,
    prod_desc,
    situacao,
    situacao_desc,
    cor,
    cor_desc,
    desenho,
    desenho_desc,
    categoria,
    categoria_desc,
    variante,
    variante_desc,
    largura,
    metros,
    peso,
    data_entrada,
    gaveta
"""

MAPA_PECA = {
    "empresa": "Empresa",
    "nro_rolo": "Nro_Rolo",
    "nro_peca": "Nro_Peca",
    "produto": "Produto",
    "prod_desc": "Produto_Descricao",
    "situacao": "Situacao",
    "situacao_desc": "Situacao_Descricao",
    "cor": "Cor",
    "cor_desc": "Cor_Descricao",
    "desenho": "Desenho",
    "desenho_desc": "Desenho_Descricao",
    "categoria": "Categoria",
    "categoria_desc": "Categoria_Descricao",
    "variante": "Variante",
    "variante_desc": "Variante_Descricao",
    "largura": "Largura",
    "metros": "Metros",
    "peso": "Peso",
    "data_entrada": "Data_Entrada",
    "gaveta": "Gaveta",
}

MAPA_SUGESTAO = {
    "produto": "Produto",
    "cor": "Cor",
    "qtde_item": "Qtde_Item",
    "qtde_saldo": "Qtde_Saldo",
    "sublote": "Sublote",
    "gavetas": "Gavetas",
    "rolos": "Rolos",
    "qtde_pecas": "Qtde_Pecas",
    "total_metros": "Total_Metros",
}

NUMERICOS_SUGESTAO = ("Qtde_Item", "Qtde_Saldo", "Total_Metros")
NUMERICOS_PECA = ("Largura", "Metros", "Peso")


def _normalizar(peca: dict[str, Any]) -> dict[str, Any]:
    for campo in NUMERICOS_PECA:
        if peca.get(campo) is not None:
            peca[campo] = float(peca[campo])
    entrada = peca.get("Data_Entrada")
    if entrada is not None and hasattr(entrada, "date"):
        peca["Data_Entrada"] = entrada.date().isoformat()
    return peca


def _consultar(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with warehouse.engine().connect() as conn:
        return [dict(linha) for linha in conn.execute(text(sql), params or {}).mappings()]


@router.get("/dados")
def dados(
    produto: str | None = Query(None, description="Código do produto (`Produto`)"),
    cor: str | None = Query(None, description="Código da cor"),
    situacao: str | None = Query(None, description="Código da situação"),
    limite: int = Query(500, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Peças em aberto (`core.estoque_pecas_em_aberto`), com filtros e paginação.

    O legado não paginava e devolvia `Chave` montada com um separador ambíguo (Doc 44 #1);
    aqui a chave é reconstruída explicitamente como `Nro_Rolo|Situacao|Cor|Desenho`. Campos
    que vinham de `Produtos_Tecidos` (`Lote_Interno`, `Num_Etq_Aux`, `Linha`) não existem no mart
    e ficam de fora — precisam de um join com o cadastro de tecidos.
    """
    condicoes: list[str] = []
    params: dict[str, Any] = {"limite": limite, "offset": offset}
    if produto:
        condicoes.append("produto = :produto")
        params["produto"] = produto.strip()
    if cor:
        condicoes.append("cor = :cor")
        params["cor"] = cor.strip()
    if situacao:
        condicoes.append("situacao = :situacao")
        params["situacao"] = situacao.strip()
    onde = f"where {' and '.join(condicoes)}" if condicoes else ""

    linhas = _consultar(
        f"select {COLUNAS_PECA} from core.estoque_pecas_em_aberto {onde} "
        f"order by empresa, nro_rolo, nro_peca "
        f"limit :limite offset :offset",
        params,
    )
    saida = []
    for linha in linhas:
        peca = _normalizar({MAPA_PECA[coluna]: linha[coluna] for coluna in linha})
        peca["Chave"] = "|".join(
            str(peca[campo] or "") for campo in ("Nro_Rolo", "Situacao", "Cor", "Desenho")
        )
        saida.append(peca)
    return saida


def _texto(valor: Any) -> str:
    """O legado jogava o valor cru no cartão: `None` virava vazio, `Decimal` perdia o zero."""
    if valor is None:
        return ""
    if isinstance(valor, Decimal):
        return f"{valor:f}"
    return str(valor)


@router.get("/sugestao-rolos/{pedido}")
def sugestao_rolos(pedido: str) -> list[dict[str, Any]]:
    """Sugestão de rolos por item do pedido (`core.pedido_sugestao_rolos`).

    Uma linha por item com saldo, como no cursor da procedure: item sem rolo disponível vem com
    `Sublote`/`Gavetas`/`Rolos`/`Total_Metros` nulos e `Qtde_Pecas = 0`. Pedido inexistente ou
    sem itens com saldo devolve lista vazia.
    """
    linhas = _consultar(
        "select produto, cor, qtde_item, qtde_saldo, sublote, gavetas, rolos, qtde_pecas, "
        "total_metros from core.pedido_sugestao_rolos "
        "where pedido = :pedido order by item",
        {"pedido": pedido.strip()},
    )
    saida = []
    for linha in linhas:
        item = {MAPA_SUGESTAO[coluna]: linha[coluna] for coluna in linha}
        for campo in NUMERICOS_SUGESTAO:
            if item[campo] is not None:
                item[campo] = float(item[campo])
        item["Qtde_Pecas"] = int(item["Qtde_Pecas"] or 0)
        saida.append(item)
    return saida


CARTES_POR_LINHA = 3
CAMPOS_CARTAO = (
    ("Produto", "Produto"),
    ("Cor", "Cor"),
    ("Qtde Item", "Qtde_Item"),
    ("Qtde Saldo", "Qtde_Saldo"),
    ("SubLote", "Sublote"),
    ("Gavetas", "Gavetas"),
    ("Qtde Peças", "Qtde_Pecas"),
    ("Rolos", "Rolos"),
    ("Total Metros", "Total_Metros"),
)


@router.get("/pdf/sugestao-rolos/{pedido}")
def pdf_sugestao_rolos(pedido: str) -> Response:
    """Mesmos cartões do legado (A4 paisagem, 3 por linha), servidos da view local.

    O legado gerava um arquivo temporário por request; aqui o PDF é montado em memória. Sem
    itens para o pedido, `404` como no `oraculum`.
    """
    itens = sugestao_rolos(pedido)
    if not itens:
        raise HTTPException(status_code=404, detail="Nenhum item encontrado")

    cartoes = []
    for item in itens:
        cartao = Table(
            [[rotulo, _texto(item.get(campo))] for rotulo, campo in CAMPOS_CARTAO],
            colWidths=[80, 100],
        )
        cartao.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        cartoes.append(cartao)

    elementos: list[Any] = []
    for inicio in range(0, len(cartoes), CARTES_POR_LINHA):
        linha = cartoes[inicio : inicio + CARTES_POR_LINHA]
        elementos.append(
            Table([linha], colWidths=[250] * len(linha), hAlign="LEFT")
        )
        elementos.append(Spacer(1, 10))

    buffer = BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=20,
        leftMargin=20,
        topMargin=20,
        bottomMargin=20,
    ).build(elementos)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=sugestao_{pedido.strip()}.pdf"},
    )