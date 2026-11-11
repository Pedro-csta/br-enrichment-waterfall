"""POST /api/enriquecer — passo 2: completa uma empresa em cascata.

Corpo: {"cnpj": "...", "registro": {...do Minha Receita...}, "ultimas": {"cnpjws": 1731300000.0}}
"""

import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _comum  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        corpo = _comum.ler_corpo_json(self)
        if not corpo.get("cnpj"):
            return _comum.responder(self, 400, {"erro": "Informe o CNPJ."})
        resultado, cascata = _comum.enriquecer(
            corpo["cnpj"],
            base=corpo.get("registro"),
            ultimas=corpo.get("ultimas") or {},
            consultar_outros=corpo.get("consultar_outros", True) is not False,
        )
        resultado["chamadas"] = dict(cascata.chamadas)
        resultado["cache"] = dict(cascata.cache_hits)
        resultado["erros"] = dict(cascata.erros)
        resultado["ultimas"] = dict(cascata._ultima_chamada)
        return _comum.responder(self, 200, resultado)
