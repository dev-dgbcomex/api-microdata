"""Escopos por rota e corte por empresa (D6): a matriz das 14 telas de negocio.

Cada tela da API (rota) tem um escopo declarado no proprio router. Aqui testamos as tres
dimensao: **escopo** (falha fechada sem escopo, admin passa em tudo), **empresa** (o `where` ja
entra com a empresa do token) e **paridade** (o numero do legado nao muda por causa do corte,
porque a empresa do warehouse e a mesma do token).

O warehouse real so tem empresa 13, entao o isolamento e testado pelo outro lado: um token de
empresa `99` nao enxerga nada, e o admin com `?empresa=` consegue checar a outra.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.auth import tokens
from src.api.auth.escopos import (
    ESCOPO_ESTOQUE,
    ESCOPO_FATURAMENTO,
    ESCOPO_FINANCEIRO,
    empresa_efetiva,
    tem_escopo,
)
from src.api.auth.usuarios import PAPEL_ADMIN, Usuario
from src.api.main import app
from src.config import get_settings

client = TestClient(app)

EMPRESA_DO_WAREHOUSE = "13"

# As 14 telas de negocio com o escopo que cada uma exige. `/dashboard-completo` junta dois.
ROTAS = [
    ("/dados", {"estoque": ESCOPO_ESTOQUE}),
    ("/sugestao-rolos/00005470", {"estoque": ESCOPO_ESTOQUE}),
    ("/pdf/sugestao-rolos/00005470", {"estoque": ESCOPO_ESTOQUE}),
    ("/faturamento/2026-03-15", {"faturamento": ESCOPO_FATURAMENTO}),
    ("/faturamento-dia/2026-03-15", {"faturamento": ESCOPO_FATURAMENTO}),
    ("/descontos/2026-03-15", {"faturamento": ESCOPO_FATURAMENTO}),
    ("/devolucoes/2026-03-15", {"faturamento": ESCOPO_FATURAMENTO}),
    ("/estornos/2026-03-15", {"faturamento": ESCOPO_FATURAMENTO}),
    ("/contas-pagas/2026-03-15", {"financeiro": ESCOPO_FINANCEIRO}),
    ("/contas-receber-programado", {"financeiro": ESCOPO_FINANCEIRO}),
    ("/contas-pagar-programado", {"financeiro": ESCOPO_FINANCEIRO}),
    ("/custos-administrativos-anual", {"financeiro": ESCOPO_FINANCEIRO}),
("/custos-administrativos-mensal", {"financeiro": ESCOPO_FINANCEIRO}),
    # O dashboard junta os dois grupos, entao exige os dois escopos de uma vez.
    (
        "/dashboard-completo/2026-03-15",
        {"faturamento": ESCOPO_FATURAMENTO, "financeiro": ESCOPO_FINANCEIRO},
    ),
]


EMAIL = "teste@exemplo.com.br"


def _usuario(
    *,
    escopos: list[str] | None = None,
    papeis: list[str] | None = None,
    empresa: str | None = EMPRESA_DO_WAREHOUSE,
    ativo: bool = True,
) -> Usuario:
    return Usuario(
        id=1,
        email=EMAIL,
        nome="Teste",
        papeis=papeis if papeis is not None else ["leitura"],
        empresa=empresa,
        ativo=ativo,
        escopos=escopos or [],
    )


@pytest.fixture
def ligado(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(get_settings(), "api_autenticacao_exigida", True)


@pytest.fixture
def como_usuario(monkeypatch: pytest.MonkeyPatch):
    """Instala um usuario no lugar do banco e devolve a fabrica de header."""

    def instalar(usuario: Usuario):
        monkeypatch.setattr("src.api.auth.dependencias.usuarios.por_id", lambda _: usuario)
        token = tokens.emitir(
            usuario_id=usuario.id,
            email=usuario.email,
            papeis=usuario.papeis,
            empresa=usuario.empresa,
        )
        return {"Authorization": f"Bearer {token}"}

    return instalar


class TestTemEscopo:
    def test_sem_escopo_nao_passa(self):
        assert not tem_escopo(_usuario(), ESCOPO_ESTOQUE)

    def test_com_o_escopo_passa(self):
        assert tem_escopo(_usuario(escopos=[ESCOPO_ESTOQUE]), ESCOPO_ESTOQUE)

    def test_escopo_de_outro_grupo_nao_passa(self):
        usuario = _usuario(escopos=[ESCOPO_FINANCEIRO])
        assert not tem_escopo(usuario, ESCOPO_ESTOQUE)

    def test_admin_passa_em_tudo(self):
        usuario = _usuario(papeis=[PAPEL_ADMIN], empresa=None)
        assert tem_escopo(usuario, ESCOPO_ESTOQUE, ESCOPO_FINANCEIRO)

    def test_curinga_passa_em_tudo(self):
        assert tem_escopo(_usuario(escopos=["*"]), ESCOPO_ESTOQUE)

    def test_dashboard_exige_os_dois_escopos(self):
        assert not tem_escopo(
            _usuario(escopos=[ESCOPO_FATURAMENTO]), ESCOPO_FATURAMENTO, ESCOPO_FINANCEIRO
        )
        assert tem_escopo(
            _usuario(escopos=[ESCOPO_FATURAMENTO, ESCOPO_FINANCEIRO]),
            ESCOPO_FATURAMENTO,
            ESCOPO_FINANCEIRO,
        )

    def test_sem_auth_ativa_passa(self):
        assert tem_escopo(None, ESCOPO_ESTOQUE)


class TestEmpresaEfetiva:
    def test_usa_a_empresa_do_token(self):
        assert empresa_efetiva(_usuario(empresa="14")) == "14"

    def test_admin_sem_empresa_ve_todas(self):
        assert empresa_efetiva(_usuario(papeis=[PAPEL_ADMIN], empresa=None)) is None

    def test_pedir_a_propria_e_permitido(self):
        assert empresa_efetiva(_usuario(empresa="13"), "13") == "13"

    def test_usuario_comum_nao_troca_de_empresa(self):
        with pytest.raises(Exception) as erro:
            empresa_efetiva(_usuario(empresa="13"), "14")
        assert getattr(erro.value, "status_code", None) == 403

    def test_admin_troca_de_empresa(self):
        admin = _usuario(papeis=[PAPEL_ADMIN], empresa=None)
        assert empresa_efetiva(admin, "14") == "14"

    def test_sem_auth_ativa_usa_a_pedida(self):
        assert empresa_efetiva(None, "13") == "13"
        assert empresa_efetiva(None) is None


class TestMatrizDeRotas:
    """Cada tela da API: sem escopo e 403; com o escopo certo, responde."""

    @pytest.mark.parametrize("rota,escopo", ROTAS)
    def test_sem_escopo_da_403(self, ligado, como_usuario, rota, escopo):
        cabecalho = como_usuario(_usuario(escopos=[]))
        resposta = client.get(rota, headers=cabecalho)
        assert resposta.status_code == 403, rota
        assert "escopo" in resposta.json()["detail"]

    @pytest.mark.parametrize("rota,escopo", ROTAS)
    def test_com_o_escopo_da_403_apenas_por_outras_telas(self, ligado, como_usuario, rota, escopo):
        """Com o escopo certo a tela responde; com um escopo alheio, 403."""
        certo = como_usuario(_usuario(escopos=list(escopo.values())))
        assert client.get(rota, headers=certo).status_code == 200, rota

        alheio = [
            e
            for e in (ESCOPO_ESTOQUE, ESCOPO_FATURAMENTO, ESCOPO_FINANCEIRO)
            if e not in escopo.values()
        ]
        if alheio:
            errado = como_usuario(_usuario(escopos=[alheio[0]]))
            assert client.get(rota, headers=errado).status_code == 403, rota

    @pytest.mark.parametrize("rota,escopo", ROTAS)
    def test_admin_acessa_todas_sem_escopo(self, ligado, como_usuario, rota, escopo):
        cabecalho = como_usuario(_usuario(papeis=[PAPEL_ADMIN], empresa=None, escopos=[]))
        assert client.get(rota, headers=cabecalho).status_code == 200, rota


class TestCortePorEmpresa:
    def test_dados_devolve_somente_a_empresa_do_token(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE], empresa="99"))
        resposta = client.get("/dados", headers=cabecalho)
        assert resposta.status_code == 200
        assert resposta.json() == []

    def test_dados_filtra_pela_empresa_do_token(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE]))
        resposta = client.get("/dados?limite=50", headers=cabecalho)
        assert resposta.status_code == 200
        assert resposta.json(), "o warehouse tem empresa 13 em aberto"
        assert {peca["Empresa"] for peca in resposta.json()} == {EMPRESA_DO_WAREHOUSE}

    def test_admin_ve_as_duas_empresas(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(papeis=[PAPEL_ADMIN], empresa=None))
        todas = client.get("/dados?limite=10", headers=cabecalho).json()
        da_13 = client.get("/dados?limite=10&empresa=13", headers=cabecalho).json()
        da_99 = client.get("/dados?limite=10&empresa=99", headers=cabecalho).json()
        assert len(todas) == 10
        assert da_99 == []
        assert {peca["Empresa"] for peca in da_13} == {EMPRESA_DO_WAREHOUSE}

    def test_usuario_comum_nao_pede_outra_empresa(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE], empresa="13"))
        resposta = client.get("/dados?empresa=99", headers=cabecalho)
        assert resposta.status_code == 403
        assert "13" in resposta.json()["detail"]

    def test_sugestao_de_pedido_de_outra_empresa_vazia(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE], empresa="99"))
        assert client.get("/sugestao-rolos/00005470", headers=cabecalho).json() == []

    def test_pdf_de_pedido_de_outra_empresa_da_404(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE], empresa="99"))
        resposta = client.get("/pdf/sugestao-rolos/00005470", headers=cabecalho)
        assert resposta.status_code == 404

    def test_sugestao_do_pedido_da_empresa_responde(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE], empresa="13"))
        resposta = client.get("/sugestao-rolos/00005470", headers=cabecalho)
        assert resposta.status_code == 200
        assert resposta.json(), "o pedido 00005470 tem itens em aberto"


class TestParidadeDoLegado:
    """O corte nao muda o numero: a empresa do token e a do warehouse.

    A referencia "sem token" e lida com `API_AUTENTICACAO_EXIGIDA=false`, que e como os
    validadores (`scripts.validar_api`) leem a API — assim a comparacao e com o numero que
    ja foi batizado como igual ao legado.
    """

    def _referencia(self, monkeypatch: pytest.MonkeyPatch, rota: str):
        monkeypatch.setattr(get_settings(), "api_autenticacao_exigida", False)
        resposta = client.get(rota)
        monkeypatch.setattr(get_settings(), "api_autenticacao_exigida", True)
        assert resposta.status_code == 200, rota
        return resposta.json()

    def test_faturamento_iguinal_com_e_sem_corte(self, ligado, como_usuario, monkeypatch):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_FATURAMENTO], empresa="13"))
        com_token = client.get("/faturamento/2026-03-15", headers=cabecalho).json()
        assert com_token == self._referencia(monkeypatch, "/faturamento/2026-03-15")

    def test_programado_iguinal_com_e_sem_corte(self, ligado, como_usuario, monkeypatch):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_FINANCEIRO], empresa="13"))
        com_token = client.get("/contas-receber-programado", headers=cabecalho).json()
        assert com_token == self._referencia(monkeypatch, "/contas-receber-programado")

    def test_dados_com_e_sem_corte_batem(self, ligado, como_usuario, monkeypatch):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE], empresa="13"))
        com_token = client.get("/dados?limite=20", headers=cabecalho).json()
        assert com_token == self._referencia(monkeypatch, "/dados?limite=20")


class TestEscopoDoPerfil:
    def test_perfil_devolve_os_escopos(self, ligado, como_usuario):
        cabecalho = como_usuario(_usuario(escopos=[ESCOPO_ESTOQUE, ESCOPO_FINANCEIRO]))
        resposta = client.get("/auth/eu", headers=cabecalho)
        assert resposta.status_code == 200
        assert resposta.json()["escopos"] == [ESCOPO_ESTOQUE, ESCOPO_FINANCEIRO]

    def test_login_devolve_os_escopos(self, monkeypatch: pytest.MonkeyPatch):
        usuario = _usuario(escopos=[ESCOPO_FATURAMENTO])
        monkeypatch.setattr("src.api.routers.auth.usuarios.autenticar", lambda *_: usuario)
        resposta = client.post(
            "/auth/login", json={"email": usuario.email, "senha": "qualquer-123"}
        )
        assert resposta.status_code == 200
        assert resposta.json()["escopos"] == [ESCOPO_FATURAMENTO]