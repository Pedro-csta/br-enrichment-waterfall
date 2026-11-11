"""Função Vercel: GET /api/info descreve a API (útil como health check)."""

import json
from http.server import BaseHTTPRequestHandler

INFO = {
    "nome": "br-enrichment-waterfall",
    "descricao": "Enriquecimento de empresas brasileiras por CNPJ, em cascata entre APIs públicas.",
    "rotas": {
        "GET /api/enrich?cnpj=...": "Enriquece um CNPJ",
        "POST /api/enrich": "Enriquece até 10 CNPJs: {\"cnpjs\": [...], \"obrigatorios\": [...], \"provedores\": [...]}",
    },
    "provedores": ["brasilapi", "cnpjws", "cnpja", "receitaws"],
    "repositorio": "https://github.com/Pedro-csta/br-enrichment-waterfall",
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        dados = json.dumps(INFO, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)
