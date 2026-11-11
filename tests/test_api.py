"""Testes das funções da Vercel (api/enrich.py e api/info.py), num servidor HTTP local
e com as APIs de CNPJ simuladas.

Rodar: python -m unittest discover -s tests
"""

import importlib.util
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

RAIZ = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, RAIZ)

from test_waterfall import BRASILAPI, CNPJWS, MAGALU, FakeHTTP, url  # noqa: E402
from waterfall import Cascata  # noqa: E402


def carregar(nome):
    spec = importlib.util.spec_from_file_location(f"api_{nome}", os.path.join(RAIZ, "api", f"{nome}.py"))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class TestAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.enrich = carregar("enrich")
        cls.info = carregar("info")
        cls.http = FakeHTTP({
            url("brasilapi", MAGALU): (200, BRASILAPI),
            url("cnpjws", MAGALU): (200, CNPJWS),
        })

        def nova_cascata(provedores=None, obrigatorios=None):
            return Cascata(provedores=provedores, campos_obrigatorios=obrigatorios,
                           http_get=cls.http, cache_dir=None, dormir=lambda s: None)

        cls.enrich.nova_cascata = nova_cascata
        cls.servidores = []
        for modulo in (cls.enrich, cls.info):
            srv = ThreadingHTTPServer(("127.0.0.1", 0), modulo.handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.servidores.append(srv)
        cls.base_enrich = f"http://127.0.0.1:{cls.servidores[0].server_port}"
        cls.base_info = f"http://127.0.0.1:{cls.servidores[1].server_port}"

    @classmethod
    def tearDownClass(cls):
        for srv in cls.servidores:
            srv.shutdown()

    def pedir(self, caminho, corpo=None, base=None, metodo=None):
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request((base or self.base_enrich) + caminho, data=dados, method=metodo,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                texto = resp.read()
                return resp.status, json.loads(texto) if texto else {}, resp.headers
        except urllib.error.HTTPError as erro:
            return erro.code, json.loads(erro.read()), erro.headers

    def test_get_um_cnpj(self):
        status, corpo, headers = self.pedir("/api/enrich?cnpj=47.960.950/0001-21")
        self.assertEqual(status, 200)
        self.assertEqual(corpo["status"], "completo")
        self.assertEqual(corpo["fontes"]["email"], "cnpjws")
        self.assertEqual(corpo["dados"]["dominio"], "magazineluiza.com.br")
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_post_lote_com_invalido(self):
        status, corpo, _ = self.pedir("/api/enrich", {"cnpjs": [MAGALU, "12.345.678/0001-00"]})
        self.assertEqual(status, 200)
        self.assertEqual([r["status"] for r in corpo["resultados"]], ["completo", "cnpj_invalido"])
        self.assertEqual(corpo["pendentes"], [])

    def test_post_obrigatorios_customizados(self):
        status, corpo, _ = self.pedir("/api/enrich", {"cnpjs": [MAGALU], "obrigatorios": ["razao_social"]})
        self.assertEqual(corpo["resultados"][0]["provedores_consultados"], ["brasilapi"])

    def test_erros_de_entrada(self):
        self.assertEqual(self.pedir("/api/enrich")[0], 400)
        self.assertEqual(self.pedir("/api/enrich", {"cnpjs": []})[0], 400)
        self.assertEqual(self.pedir("/api/enrich", {"cnpjs": ["1"] * 11})[0], 400)
        status, corpo, _ = self.pedir("/api/enrich", {"cnpjs": [MAGALU], "provedores": ["serasa"]})
        self.assertEqual(status, 400)
        self.assertIn("serasa", corpo["erro"])

    def test_options_cors(self):
        status, _, headers = self.pedir("/api/enrich", metodo="OPTIONS")
        self.assertEqual(status, 204)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])

    def test_info(self):
        status, corpo, _ = self.pedir("/api/info", base=self.base_info)
        self.assertEqual(status, 200)
        self.assertIn("POST /api/enrich", corpo["rotas"])


if __name__ == "__main__":
    unittest.main()
