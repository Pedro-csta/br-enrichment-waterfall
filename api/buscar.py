"""GET /api/buscar — passo 1: lista empresas do ICP no Minha Receita.

GET /api/buscar?municipios=SC devolve as cidades da UF (código IBGE) para o formulário.
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _comum  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = _comum.parametros(self)

        if p.get("municipios"):
            cidades = _comum.cidades_da_uf(p["municipios"])
            if not cidades:
                return _comum.responder(self, 502, {"erro": "Não consegui carregar as cidades agora."})
            return _comum.responder(self, 200, {"cidades": cidades})

        try:
            filtros = _comum.ler_filtros(p)
            quero = max(1, min(int(p.get("quero") or 50), 200))
            resultado = _comum.buscar(filtros, cursor=p.get("cursor") or None, quero=quero)
        except ValueError as erro:
            return _comum.responder(self, 400, {"erro": str(erro)})
        except RuntimeError as erro:
            return _comum.responder(self, 502, {"erro": str(erro)})
        return _comum.responder(self, 200, resultado)
