"""Função Vercel: enriquece CNPJs em cascata.

GET  /api/enrich?cnpj=47.960.950/0001-21
POST /api/enrich  {"cnpjs": ["47960950000121", "..."], "obrigatorios": [...], "provedores": [...]}

Responde JSON com o registro, a origem de cada campo e o status. CORS aberto,
para ser chamada de qualquer front-end.
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from providers import CAMPOS, ORDEM_PADRAO  # noqa: E402
from waterfall import CAMPOS_OBRIGATORIOS_PADRAO, Cascata, http_get_json  # noqa: E402

MAX_LOTE = 10          # CNPJs por chamada POST
TEMPO_LIMITE = 240     # segundos; a função tem 300 s, sobra margem para responder
CACHE_DIR = "/tmp/cnpj-cache"  # único diretório gravável na Vercel; vive enquanto a instância vive


def nova_cascata(provedores=None, obrigatorios=None):
    """Ponto único de criação: os testes trocam o http_get por respostas gravadas."""
    return Cascata(
        provedores=provedores or ORDEM_PADRAO,
        campos_obrigatorios=obrigatorios or CAMPOS_OBRIGATORIOS_PADRAO,
        http_get=http_get_json,
        cache_dir=CACHE_DIR,
    )


def resultado_json(r):
    return {
        "cnpj": r["cnpj"],
        "status": r["status"],
        "dados": r["registro"],
        "fontes": r["fontes"],
        "provedores_consultados": r["provedores_consultados"],
    }


def validar_lista(valor, permitidos, nome):
    if valor is None:
        return None
    if isinstance(valor, str):
        valor = [v.strip() for v in valor.split(",") if v.strip()]
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise ValueError(f"'{nome}' deve ser uma lista de textos")
    invalidos = [v for v in valor if v not in permitidos]
    if invalidos:
        raise ValueError(f"'{nome}' inválido: {', '.join(invalidos)}. Opções: {', '.join(permitidos)}")
    return valor or None


class handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _responder(self, status, corpo):
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        cnpj = (params.get("cnpj") or [""])[0]
        if not cnpj:
            self._responder(400, {"erro": "Informe ?cnpj=..."})
            return
        try:
            provedores = validar_lista((params.get("provedores") or [None])[0], ORDEM_PADRAO, "provedores")
            obrigatorios = validar_lista((params.get("obrigatorios") or [None])[0], CAMPOS, "obrigatorios")
        except ValueError as erro:
            self._responder(400, {"erro": str(erro)})
            return
        cascata = nova_cascata(provedores, obrigatorios)
        self._responder(200, resultado_json(cascata.enriquecer(cnpj)))

    def do_POST(self):
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
            corpo = json.loads(self.rfile.read(tamanho) or b"{}")
            cnpjs = corpo.get("cnpjs")
            if not isinstance(cnpjs, list) or not cnpjs:
                raise ValueError("Envie {\"cnpjs\": [\"...\"]}")
            if len(cnpjs) > MAX_LOTE:
                raise ValueError(f"No máximo {MAX_LOTE} CNPJs por chamada; divida a lista em lotes")
            provedores = validar_lista(corpo.get("provedores"), ORDEM_PADRAO, "provedores")
            obrigatorios = validar_lista(corpo.get("obrigatorios"), CAMPOS, "obrigatorios")
        except (ValueError, json.JSONDecodeError) as erro:
            self._responder(400, {"erro": str(erro)})
            return

        cascata = nova_cascata(provedores, obrigatorios)
        inicio, resultados, pendentes = time.monotonic(), [], []
        for valor in cnpjs:
            if time.monotonic() - inicio > TEMPO_LIMITE:
                pendentes.append(valor)
                continue
            resultados.append(resultado_json(cascata.enriquecer(str(valor))))
        self._responder(200, {
            "resultados": resultados,
            "pendentes": pendentes,
            "chamadas": dict(cascata.chamadas),
        })
