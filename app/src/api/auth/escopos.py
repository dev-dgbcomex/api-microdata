"""Escopos por rota e corte por empresa (D6).

O ERP controlava acesso por `Usuario_Acessos` (Sistema x Topico, Estudo 41) e o produto precisa
do mesmo corte: quem e comercial nao enxerga o programmed financeiro. Com so os papeis
`admin`/`leitura` isso nao sai — "leitura" leria tudo. Cada rota declara aqui o escopo que exige:

- **estoque** (`estoque:leitura`): `/dados`, `/sugestao-rolos/{pedido}`,
  `/pdf/sugestao-rolos/{pedido}`;
- **faturamento** (`faturamento:leitura`): `/faturamento/{data}`, `/faturamento-dia/{data}`,
  `/descontos/{data}`, `/devolucoes/{data}`, `/estornos/{data}`;
- **financeiro** (`financeiro:leitura`): `/contas-pagas/{data}`, `/contas-receber-programado`,
  `/contas-pagar-programado`, `/custos-administrativos-anual`, `/custos-administrativos-mensal`;
- **dashboard** (`/dashboard-completo/{data}`): exige faturamento **e** financeiro, porque a
  tela soma os dois grupos.

Tres regras, todas fechadas por padrao:

- `exigir_escopo` exige **todos** os escopos informados e a ausencia de escopo e `403`
  (`auth.usuario.escopos` entra com `'{}'`, entao usuario recem-criado nao ve nada);
- o papel `admin` passa em qualquer escopo, assim como o escopo curinga `'*'` no usuario;
- `empresa_efetiva` devolve o codigo de empresa a aplicar no `where`: o do token, ou o
  `?empresa=` pedido — que so o `admin` pode usar para olhar outra empresa (validacao).
"""

from __future__ import annotations

from fastapi import HTTPException, status

from src.api.auth.dependencias import UsuarioAutenticado
from src.api.auth.usuarios import PAPEL_ADMIN, Usuario

ESCOPO_ESTOQUE = "estoque:leitura"
ESCOPO_FATURAMENTO = "faturamento:leitura"
ESCOPO_FINANCEIRO = "financeiro:leitura"
CURINGA = "*"

ESCOPOS_VALIDOS = frozenset({ESCOPO_ESTOQUE, ESCOPO_FATURAMENTO, ESCOPO_FINANCEIRO})


def tem_escopo(usuario: Usuario | None, *escopos: str) -> bool:
    """`True` quando o usuario pode ler tudo que `escopos` nomeia.

    `None` e o caso de `API_AUTENTICACAO_EXIGIDA=false` (validacao local): sem token nao ha
    quem cortar, entao o acesso passa — o filtro por empresa segue valendo, porque vem do `where`.
    """
    if usuario is None:
        return True
    if PAPEL_ADMIN in usuario.papeis or CURINGA in usuario.escopos:
        return True
    return set(escopos) <= set(usuario.escopos)


def exigir_escopo(*escopos: str):
    """Fabrica de dependencia: exige todos os `escopos` (admin passa em tudo).

    A assinatura usa o alias de modulo `UsuarioAutenticado` (e nao um alias local) porque, com
    `from __future__ import annotations`, um alias criado dentro da fabrica vira ForwardRef que
    o FastAPI nao resolve ao montar o schema da rota.
    """
    nomes = ", ".join(escopos)

    def dependencia(usuario: UsuarioAutenticado) -> Usuario | None:
        if usuario is None:
            return None
        if tem_escopo(usuario, *escopos):
            return usuario
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"sem escopo para: {nomes}",
        )

    return dependencia


def empresa_efetiva(usuario: Usuario | None, pedida: str | None = None) -> str | None:
    """Codigo de empresa a filtrar, ou `None` para nao cortar.

    `pedida` vem de `?empresa=`. Um usuario comum so pode pedir a propria empresa; o `admin`
    pode pedir qualquer uma (e so uma empresa de cada vez, porque a API e multiempresa por
    empresa). `None` no token = usuario transversal (admin), que ve as empresas todas.
    """
    alvo = (pedida or "").strip() or None
    if usuario is None:
        return alvo
    do_token = usuario.empresa
    if alvo is None or alvo == do_token:
        return do_token
    if PAPEL_ADMIN in usuario.papeis or CURINGA in usuario.escopos:
        return alvo
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"voce so acessa a empresa {do_token or '(todas)'}",
    )


__all__ = [
    "CURINGA",
    "ESCOPO_ESTOQUE",
    "ESCOPO_FATURAMENTO",
    "ESCOPO_FINANCEIRO",
    "ESCOPOS_VALIDOS",
    "empresa_efetiva",
    "exigir_escopo",
    "tem_escopo",
]