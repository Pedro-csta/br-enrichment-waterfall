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
    "descricao_situacao_cadastral": "ATIVA", "descricao_tipo_de_logradouro": "AVENIDA",
    "logradouro": "PREFEITO OSMAR CUNHA", "numero": "416", "complemento": "SALA 1108", "bairro": "CENTRO",
    "ddd_telefone_2": "", "opcao_pelo_simples": True,
    "cnaes_secundarios": [{"codigo": 6203100, "descricao": "Desenvolvimento de programas não-customizáveis"}],
    "qsa": [
        {"nome_socio": "MARIA DA SILVA", "qualificacao_socio": "Sócio", "faixa_etaria": "Entre 41 a 50 anos",
         "data_entrada_sociedade": "2021-03-01"},
        {"nome_socio": "EVERTON LUAN GOMES DA COSTA", "qualificacao_socio": "Sócio-Administrador",
         "faixa_etaria": "Entre 31 a 40 anos", "data_entrada_sociedade": "2020-01-10"},
    ],
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


class TestExtras(unittest.TestCase):
    def test_socios_endereco_e_simples(self):
        e = _comum.resumo_empresa(MATRIZ)["extras"]
        self.assertEqual(e["socio_administrador"], "Everton Luan Gomes da Costa")
        self.assertEqual(e["qtd_socios"], 2)
        self.assertIn("Maria da Silva (Sócio, Entre 41 a 50 anos, desde 2021)", e["socios"])
        self.assertEqual(e["endereco"], "Avenida Prefeito Osmar Cunha, 416 - Sala 1108")
        self.assertEqual((e["bairro"], e["simples_nacional"], e["telefone_2"]), ("Centro", "sim", ""))
        self.assertTrue(e["cnaes_secundarios"].startswith("6203100 "))

    def test_sem_socios(self):
        e = _comum.extras_receita({"cnpj": "1"})
        self.assertEqual((e["socio_administrador"], e["qtd_socios"], e["simples_nacional"]), ("", 0, ""))


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


class TestCidades(unittest.TestCase):
    def test_ibge_e_reserva_quando_quebrado(self):
        quebrado = [{"nome": "ORD", "id": 4200051}, {"nome": "ORD", "id": 4200101}, {"nome": "ORD", "id": 4200200}]
        reserva = [{"nome": "JOINVILLE", "codigo_ibge": "4209102"}, {"nome": "BLUMENAU", "codigo_ibge": "4202404"}]
        http = lambda url, **k: (200, quebrado) if "ibge.gov.br" in url else (200, reserva)
        self.assertEqual(_comum.cidades_da_uf("sc", http=http),
                         [{"nome": "Blumenau", "ibge": "4202404"}, {"nome": "Joinville", "ibge": "4209102"}])
        self.assertEqual(_comum.cidades_da_uf("x1", http=http), [])

    def test_busca_corta_na_quantidade_pedida(self):
        f = _comum.ler_filtros({"uf": "SC", "cnae": "6201501", "excluir_mei": "0"})
        self.assertEqual(len(_comum.buscar(f, quero=1, http=busca_falsa)["empresas"]), 1)


class TestRotas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.originais = (_comum.get_json_com_retry, _comum.enriquecer)
        cidades = [{"nome": "Florianópolis", "id": 4205407}, {"nome": "Joinville", "id": 4209102}]
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
        self.assertEqual(corpo["cidades"][0], {"nome": "Florianópolis", "ibge": "4205407"})
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
