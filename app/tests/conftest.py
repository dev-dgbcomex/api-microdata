"""Configuracao dos testes: a suite roda sem token; a auth e testada ligando o flag por teste."""

from __future__ import annotations

import os

os.environ.setdefault("API_AUTENTICACAO_EXIGIDA", "false")