"""Testes da busca por ICP (api/buscar.py) e do enriquecimento web (api/enriquecer.py), offline."""

import json
import os
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "api"))
sys.path.insert(0, RAIZ)

import _comum  # noqa: E402
import buscar  # noqa: E402
import enriquecer  # noqa: E402
from waterfall import Cascata  # noqa: E402

# Formato real de um item da busca do minhareceita.org (dados resumidos).
MATRIZ = {
    "uf": "SC", "cep": "88015100", "cnpj": "64003399000147", "email": None, "porte": "MICRO EMPRESA",
    "municipio": "FLORIANOPOLIS", "cnae_fiscal": 6201501, "codigo_porte": 1,
    "razao_social": "NEXTEC SERVICOS LTDA", "nome_fantasia": "NEXTEC", "capital_social": 1500000,
    "ddd_telefone_1": "6733455871", "opcao_pelo_mei": False, "situacao_cadastral": 2,
    "natureza_juridica": "Sociedade Empresária Limitada", "cnae_fiscal_descricao": "Desenvolvimento",
    "data_inicio_atividade": "2020-01-10", "identificador_matriz_filial": 1,
    "descricao_situacao_cadastral": "ATIVA",
}
FILIAL = dict(MATRIZ, cnpj="11222333000281", identificador_matriz_filial=2)
GRANDE = dict(MATRIZ, cnpj="11222333000181", codigo_porte=5)
MEI = dict(MATRIZ, cnpj="11444777000161", opcao_pelo_mei=True)
PAGINAS = {None: {"data": [MATRIZ, FILIAL, MEI], "cursor": "9"}, "9": {"data": [GRANDE], "cursor": None}}


def busca_falsa(url, **_):
    cursor = url.split("cursor=")[1].split("&")[0] if "cursor=" in url else None
    return 200, PAGINAS[cursor]


def cnpjws_falso(chamadas):
    def http_get(url):
        chamadas.append(url)
        if "cnpj.ws" in url:
            return 200, {"razao_social": "NEXTEC SERVICOS LTDA",
                         "estabelecimento": {"email": "contato@nextec.com.br"}}
        return 404, None
    return http_get


class TestBusca(unittest.TestCase):
    def test_filtros_e_paginas(self):
        f = _comum.ler_filtros({"uf": "sc", "cnae": "6201-5/01", "porte": "ME,EPP"})
        self.assertIn("cnae_fiscal=6201501", _comum.url_busca(f))
        r = _comum.buscar(f, quero=10, http=busca_falsa)
        self.assertEqual([e["cnpj"] for e in r["empresas"]], ["64003399000147"])
        self.assertEqual((r["lidos"], r["fim"]), (4, True))
        self.assertEqual(r["empresas"][0]["registro"]["telefone"], "+556733455871")

    def test_cnae_secundario_e_idade(self):
        f = _comum.ler_filtros({"uf": "SC", "cnae": "6201501", "cnae_secundario": "1", "idade_min": "10"})
        self.assertIn("cnae=6201501", _comum.url_busca(f))
        self.assertEqual(_comum.buscar(f, http=busca_falsa)["empresas"], [])

    def test_entrada_invalida(self):
        with self.assertRaises(ValueError):
            _comum.ler_filtros({"uf": "SP", "cnae": "62"})
        with self.assertRaises(ValueError):
            _comum.ler_filtros({})


class TestEnriquecer(unittest.TestCase):
    def test_completa_email_respeitando_intervalo(self):
        chamadas, dormiu = [], []
        cascata = Cascata(provedores=_comum.CASCATA_WEB, cache_dir=None, http_get=cnpjws_falso(chamadas),
                          relogio=time.time, dormir=dormiu.append)
        base = _comum.resumo_empresa(MATRIZ)["registro"]
        r, _ = _comum.enriquecer("64003399000147", base=base, ultimas={"cnpjws": time.time() - 5},
                                 cascata=cascata)
        self.assertEqual(r["status"], "completo")
        self.assertEqual(r["fontes"]["email"], "cnpjws")
        self.assertEqual(r["fontes"]["telefone"], "minhareceita")
        self.assertEqual(r["registro"]["dominio"], "nextec.com.br")
        self.assertEqual(len(chamadas), 1)
        self.assertTrue(15 < dormiu[0] <= 16.5)

    def test_so_receita(self):
        cascata = Cascata(provedores=_comum.CASCATA_WEB, cache_dir=None, http_get=cnpjws_falso([]))
        r, _ = _comum.enriquecer("64003399000147", base=_comum.resumo_empresa(MATRIZ)["registro"],
                                 cascata=cascata, consultar_outros=False)
        self.assertEqual((r["status"], r["provedores_consultados"]), ("parcial", ["minhareceita"]))


class TestRotas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originais = (_comum.get_json_com_retry, _comum.enriquecer)
        cidades = [{"nome": "FLORIANÓPOLIS", "codigo_ibge": 4205407}]
        _comum.get_json_com_retry = lambda url, **k: busca_falsa(url) if "minhareceita" in url else (200, cidades)
        original = cls.originais[1]
        _comum.enriquecer = lambda c, **k: original(
            c, cascata=Cascata(provedores=_comum.CASCATA_WEB, cache_dir=None, http_get=cnpjws_falso([]),
                               dormir=lambda s: None, relogio=time.time), **k)
        cls.servidores = []
        for modulo in (buscar, enriquecer):
            srv = ThreadingHTTPServer(("127.0.0.1", 0), modulo.handler)
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            cls.servidores.append(srv)

    @classmethod
    def tearDownClass(cls):
        _comum.get_json_com_retry, _comum.enriquecer = cls.originais
        for srv in cls.servidores:
            srv.shutdown()
            srv.server_close()

    def pedir(self, srv, caminho, corpo=None):
        porta = srv.server_address[1]
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{porta}{caminho}", data=dados,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as erro:
            return erro.code, json.loads(erro.read())

    def test_buscar(self):
        status, corpo = self.pedir(self.servidores[0], "/api/buscar?uf=SC&cnae=6201501&quero=5")
        self.assertEqual(status, 200)
        self.assertEqual(len(corpo["empresas"]), 2)
        status, corpo = self.pedir(self.servidores[0], "/api/buscar?municipios=SC")
        self.assertEqual(corpo["cidades"], [{"nome": "Florianópolis", "ibge": "4205407"}])
        self.assertEqual(self.pedir(self.servidores[0], "/api/buscar?uf=SC&cnae=1")[0], 400)

    def test_enriquecer(self):
        base = _comum.resumo_empresa(MATRIZ)["registro"]
        status, corpo = self.pedir(self.servidores[1], "/api/enriquecer",
                                   {"cnpj": "64003399000147", "registro": base, "ultimas": {}})
        self.assertEqual((status, corpo["status"], corpo["chamadas"]), (200, "completo", {"cnpjws": 1}))
        self.assertIn("cnpjws", corpo["ultimas"])
        self.assertEqual(self.pedir(self.servidores[1], "/api/enriquecer", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
