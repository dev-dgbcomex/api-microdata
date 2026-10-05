"""Testes da autenticacao (Fase E): bcrypt, JWT, dependencias e as rotas /auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.auth import senhas, tokens
from src.api.auth.dependencias import exigir_papeis
from src.api.auth.usuarios import PAPEL_ADMIN, Usuario
from src.api.main import app
from src.config import get_settings

client = TestClient(app)


class TestSenhas:
    def test_hash_nao_guarda_a_senha(self):
        gerado = senhas.gerar("senha-de-teste-123")
        assert gerado.startswith("$2")
        assert "senha-de-teste-123" not in gerado

    def test_conferencia_aceita_a_propria_e_recusa_a_outra(self):
        gerado = senhas.gerar("senha-de-teste-123")
        assert senhas.conferir("senha-de-teste-123", gerado)
        assert not senhas.conferir("outra-senha-123", gerado)

    def test_senha_curta_e_recusada(self):
        with pytest.raises(ValueError, match="ao menos"):
            senhas.gerar("curta")

    def test_hash_invalido_nao_levanta(self):
        assert not senhas.conferir("qualquer", "nao-e-bcrypt")
        assert not senhas.conferir("", "$2b$12$abc")


class TestToken:
    def test_ida_e_volta(self):
        token = tokens.emitir(usuario_id=7, email="a@b.com", papeis=["leitura"], empresa="13")
        payload = tokens.ler(token)
        assert payload["sub"] == "7"
        assert payload["email"] == "a@b.com"
        assert payload["papeis"] == ["leitura"]
        assert payload["empresa"] == "13"
        assert payload["iss"] == tokens.EMISSOR

    def test_token_adulterado_e_recusado(self):
        token = tokens.emitir(usuario_id=7, email="a@b.com", papeis=["leitura"])
        with pytest.raises(tokens.TokenInvalido):
            tokens.ler(token[:-3] + "abc")

    def test_token_de_outro_emissor_e_recusado(self):
        import jwt

        settings = get_settings()
        forjado = jwt.encode(
            {"iss": "outro", "sub": "1", "exp": 4_102_444_800},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(tokens.TokenInvalido):
            tokens.ler(forjado)

    def test_token_sem_sub_e_recusado(self):
        import jwt

        settings = get_settings()
        sem_sub = jwt.encode(
            {"iss": tokens.EMISSOR, "exp": 4_102_444_800},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(tokens.TokenInvalido):
            tokens.ler(sem_sub)


class TestRotasPublicas:
    def test_login_e_publico(self):
        assert "/auth/login" in app.openapi()["paths"]
        assert "post" in app.openapi()["paths"]["/auth/login"]

    def test_login_valida_o_formato_do_corpo(self):
        ruim = client.post("/auth/login", json={"email": "nao-e-email", "senha": "x"})
        assert ruim.status_code == 422
        assert client.post("/auth/login", json={"email": "a@b.com"}).status_code == 422


class TestExigirAutenticado:
    def _ligar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(get_settings(), "api_autenticacao_exigida", True)

    @pytest.fixture
    def ligado(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._ligar(monkeypatch)

    def test_rota_de_negocio_exige_token(self, ligado):
        assert client.get("/faturamento/2026-03-01").status_code == 401

    def test_token_invalido_da_401(self, ligado):
        resposta = client.get(
            "/faturamento/2026-03-01", headers={"Authorization": "Bearer nao-e-jwt"}
        )
        assert resposta.status_code == 401
        assert "invalido" in resposta.json()["detail"]

    def test_token_valido_de_usuario_inexistente_da_401(self, ligado, monkeypatch):
        monkeypatch.setattr("src.api.auth.dependencias.usuarios.por_id", lambda _: None)
        token = tokens.emitir(usuario_id=999, email="ninguem@b.com", papeis=["leitura"])
        resposta = client.get(
            "/faturamento/2026-03-01", headers={"Authorization": f"Bearer {token}"}
        )
        assert resposta.status_code == 401
        assert "inexistente" in resposta.json()["detail"]

    def test_usuario_ativo_passa(self, ligado, monkeypatch):
        usuario = Usuario(
            id=1, email="a@b.com", nome="A", papeis=["leitura"], empresa="13", ativo=True
        )
        monkeypatch.setattr(
            "src.api.auth.dependencias.usuarios.por_id", lambda _: usuario
        )
        token = tokens.emitir(usuario_id=1, email=usuario.email, papeis=usuario.papeis)
        resposta = client.get("/auth/eu", headers={"Authorization": f"Bearer {token}"})
        assert resposta.status_code == 200
        assert resposta.json()["email"] == "a@b.com"
        assert resposta.json()["empresa"] == "13"

    def test_inativo_nao_passa(self, ligado, monkeypatch):
        usuario = Usuario(
            id=1, email="a@b.com", nome="A", papeis=["leitura"], empresa=None, ativo=False
        )
        monkeypatch.setattr("src.api.auth.dependencias.usuarios.por_id", lambda _: usuario)
        token = tokens.emitir(usuario_id=1, email=usuario.email, papeis=usuario.papeis)
        resposta = client.get(
            "/faturamento/2026-03-01", headers={"Authorization": f"Bearer {token}"}
        )
        assert resposta.status_code == 401

    def test_health_continua_publico(self, ligado):
        assert client.get("/health").status_code == 200


class TestPapeis:
    def test_admin_passa_em_tudo(self):
        admin = Usuario(
            id=1, email="a@b.com", nome="", papeis=[PAPEL_ADMIN], empresa=None, ativo=True
        )
        dependencia = exigir_papeis("escrita")
        assert dependencia(admin) is admin

    def test_sem_o_papel_levanta_403(self):
        leitor = Usuario(
            id=1, email="a@b.com", nome="", papeis=["leitura"], empresa=None, ativo=True
        )
        dependencia = exigir_papeis("escrita")
        with pytest.raises(Exception) as erro:
            dependencia(leitor)
        assert getattr(erro.value, "status_code", None) == 403

    def test_sem_auth_ativa_nao_checa_papel(self):
        dependencia = exigir_papeis("escrita")
        assert dependencia(None) is None