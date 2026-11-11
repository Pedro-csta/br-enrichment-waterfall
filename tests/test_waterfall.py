"""Testes offline: as respostas abaixo são recortes de respostas reais das
APIs para a Magazine Luiza (47.960.950/0001-21), sem os dados de sócios.

Rodar: python -m unittest discover -s tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cnpj  # noqa: E402
from providers import PROVEDORES  # noqa: E402
from waterfall import Cascata, dominio_por_email, setor_por_cnae  # noqa: E402

MAGALU = "47960950000121"

BRASILAPI = {
    "cnpj": MAGALU, "razao_social": "MAGAZINE LUIZA S/A", "nome_fantasia": "MAGAZINE LUIZA",
    "descricao_situacao_cadastral": "ATIVA", "data_inicio_atividade": "1966-10-24", "porte": "DEMAIS",
    "natureza_juridica": "Sociedade Anônima Aberta", "cnae_fiscal": 4713004,
    "cnae_fiscal_descricao": "Lojas de departamentos ou magazines, exceto lojas francas (Duty free)",
    "municipio": "FRANCA", "uf": "SP", "cep": "14400490", "ddd_telefone_1": "1637112002",
    "email": None, "capital_social": 14202162000,
}

CNPJWS = {
    "razao_social": "MAGAZINE LUIZA S/A", "capital_social": "14202162483.70",
    "porte": {"id": "05", "descricao": "Demais"},
    "natureza_juridica": {"id": "2046", "descricao": "Sociedade Anônima Aberta"},
    "estabelecimento": {
        "cnpj": MAGALU, "nome_fantasia": "MAGAZINE LUIZA", "situacao_cadastral": "Ativa",
        "data_inicio_atividade": "1966-10-24", "cep": "14400490", "ddd1": "16", "telefone1": "37112002",
        "email": "fiscal.estadual@magazineluiza.com.br",
        "atividade_principal": {"id": "4713004", "descricao": "Lojas de departamentos ou magazines, exceto lojas francas (Duty free)"},
        "estado": {"sigla": "SP"}, "cidade": {"nome": "Franca"},
    },
}

CNPJA = {
    "taxId": MAGALU, "alias": "Magazine Luiza", "founded": "1966-10-24",
    "company": {"name": "MAGAZINE LUIZA S/A", "equity": 14202162483.7,
                "nature": {"id": 2046, "text": "Sociedade Anônima Aberta"},
                "size": {"id": 5, "acronym": "DEMAIS", "text": "Demais"}},
    "status": {"id": 2, "text": "Ativa"},
    "address": {"city": "Franca", "state": "SP", "zip": "14400490"},
    "phones": [{"type": "LANDLINE", "area": "16", "number": "37112002"}],
    "emails": [{"ownership": "CORPORATE", "address": "fiscal.estadual@magazineluiza.com.br"}],
    "mainActivity": {"id": 4713004, "text": "Lojas de departamentos ou magazines, exceto lojas francas (Duty free)"},
}

RECEITAWS = {
    "atividade_principal": [{"code": "47.13-0-04", "text": "Lojas de departamentos ou magazines, exceto lojas francas (Duty free)"}],
    "nome": "MAGAZINE LUIZA S/A", "uf": "SP", "telefone": "(16) 3711-2002",
    "email": "fiscal.estadual@magazineluiza.com.br", "situacao": "ATIVA", "cep": "14.400-490",
    "municipio": "FRANCA", "porte": "DEMAIS", "abertura": "24/10/1966",
    "natureza_juridica": "204-6 - Sociedade Anônima Aberta", "fantasia": "MAGAZINE LUIZA",
    "capital_social": "14202162483.70", "status": "OK",
}

ESPERADO_COMUM = {
    "razao_social": "MAGAZINE LUIZA S/A",
    "situacao": "Ativa",
    "data_abertura": "1966-10-24",
    "porte": "Demais",
    "natureza_juridica": "Sociedade Anônima Aberta",
    "cnae_principal": "4713004",
    "municipio": "Franca",
    "uf": "SP",
    "cep": "14400490",
    "telefone": "+551637112002",
}


class FakeHTTP:
    """Simula as APIs: devolve (status, json) por URL e conta as chamadas."""

    def __init__(self, respostas):
        self.respostas = respostas
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        resposta = self.respostas.get(url, (404, None))
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, list):  # sequência de respostas para testar retry
            return resposta.pop(0)
        return resposta


def url(provedor, cnpj_):
    return PROVEDORES[provedor].url(cnpj_)


def cascata(respostas, **kwargs):
    http = FakeHTTP(respostas)
    kwargs.setdefault("cache_dir", None)
    return Cascata(http_get=http, dormir=lambda s: None, **kwargs), http


class TestCNPJ(unittest.TestCase):
    def test_valida_digitos(self):
        self.assertTrue(cnpj.valido("47.960.950/0001-21"))
        self.assertFalse(cnpj.valido("47.960.950/0001-22"))
        self.assertFalse(cnpj.valido("11.111.111/1111-11"))
        self.assertFalse(cnpj.valido(""))

    def test_recupera_zeros_perdidos_em_planilha(self):
        self.assertEqual(cnpj.limpar("191"), "00000000000191")
        self.assertTrue(cnpj.valido(191))
        self.assertEqual(cnpj.formatar("191"), "00.000.000/0001-91")


class TestAdaptadores(unittest.TestCase):
    def test_os_quatro_provedores_chegam_ao_mesmo_esquema(self):
        respostas = {"brasilapi": BRASILAPI, "cnpjws": CNPJWS, "cnpja": CNPJA, "receitaws": RECEITAWS}
        for nome, dados in respostas.items():
            with self.subTest(provedor=nome):
                registro = PROVEDORES[nome].converter(dados)
                for campo, valor in ESPERADO_COMUM.items():
                    self.assertEqual(registro.get(campo), valor, campo)

    def test_email_ausente_na_brasilapi(self):
        self.assertNotIn("email", PROVEDORES["brasilapi"].converter(BRASILAPI))
        self.assertEqual(
            PROVEDORES["cnpjws"].converter(CNPJWS)["email"], "fiscal.estadual@magazineluiza.com.br"
        )

    def test_receitaws_erro_vira_nao_encontrado(self):
        self.assertIsNone(PROVEDORES["receitaws"].converter({"status": "ERROR", "message": "CNPJ inválido"}))

    def test_derivados(self):
        self.assertEqual(setor_por_cnae("4713004"), "Comércio")
        self.assertEqual(setor_por_cnae("6203100"), "Informação e comunicação")
        self.assertEqual(dominio_por_email("contato@empresa.com.br"), "empresa.com.br")
        self.assertIsNone(dominio_por_email("dono@gmail.com"))


class TestCascata(unittest.TestCase):
    def test_completa_email_com_o_segundo_provedor_e_para(self):
        c, http = cascata({
            url("brasilapi", MAGALU): (200, BRASILAPI),
            url("cnpjws", MAGALU): (200, CNPJWS),
        })
        r = c.enriquecer("47.960.950/0001-21")
        self.assertEqual(r["status"], "completo")
        self.assertEqual(r["registro"]["email"], "fiscal.estadual@magazineluiza.com.br")
        self.assertEqual(r["fontes"]["email"], "cnpjws")
        self.assertEqual(r["fontes"]["razao_social"], "brasilapi")
        self.assertEqual(r["registro"]["dominio"], "magazineluiza.com.br")
        self.assertEqual(r["provedores_consultados"], ["brasilapi", "cnpjws"])
        self.assertEqual(len(http.urls), 2)  # nem CNPJá nem ReceitaWS foram chamados

    def test_nao_chama_ninguem_alem_do_primeiro_se_ja_estiver_completo(self):
        c, http = cascata({url("brasilapi", MAGALU): (200, BRASILAPI)},
                          campos_obrigatorios=["razao_social", "uf"])
        r = c.enriquecer(MAGALU)
        self.assertEqual(r["status"], "completo")
        self.assertEqual(len(http.urls), 1)

    def test_primeiro_fora_do_ar_cai_para_o_proximo(self):
        c, _ = cascata({
            url("brasilapi", MAGALU): OSError("timeout"),
            url("cnpjws", MAGALU): (200, CNPJWS),
        })
        r = c.enriquecer(MAGALU)
        self.assertEqual(r["fontes"]["razao_social"], "cnpjws")
        self.assertEqual(c.erros["brasilapi"], 1)
        self.assertEqual(r["status"], "completo")

    def test_retry_em_429(self):
        c, http = cascata({url("brasilapi", MAGALU): [(429, None), (200, BRASILAPI)]},
                          campos_obrigatorios=["razao_social"])
        r = c.enriquecer(MAGALU)
        self.assertEqual(r["status"], "completo")
        self.assertEqual(c.chamadas["brasilapi"], 2)

    def test_cnpj_invalido_nao_gasta_chamada(self):
        c, http = cascata({})
        r = c.enriquecer("12.345.678/0001-00")
        self.assertEqual(r["status"], "cnpj_invalido")
        self.assertEqual(http.urls, [])

    def test_nao_encontrado_em_nenhum(self):
        c, http = cascata({})
        r = c.enriquecer(MAGALU)
        self.assertEqual(r["status"], "nao_encontrado")
        self.assertEqual(len(http.urls), 4)

    def test_cache_evita_segunda_chamada(self):
        with tempfile.TemporaryDirectory() as tmp:
            respostas = {url("brasilapi", MAGALU): (200, BRASILAPI)}
            c1, http1 = cascata(respostas, cache_dir=tmp, campos_obrigatorios=["razao_social"])
            c1.enriquecer(MAGALU)
            c2, http2 = cascata(respostas, cache_dir=tmp, campos_obrigatorios=["razao_social"])
            r = c2.enriquecer(MAGALU)
            self.assertEqual(len(http2.urls), 0)
            self.assertEqual(c2.cache_hits["brasilapi"], 1)
            self.assertEqual(r["registro"]["razao_social"], "MAGAZINE LUIZA S/A")

    def test_respeita_intervalo_entre_chamadas(self):
        esperas = []
        agora = [0.0]
        http = FakeHTTP({})
        c = Cascata(provedores=["cnpjws"], http_get=http, cache_dir=None,
                    dormir=esperas.append, relogio=lambda: agora[0])
        c.enriquecer(MAGALU)
        c.enriquecer("71673990000177")
        self.assertEqual(esperas, [PROVEDORES["cnpjws"].intervalo_minimo])


if __name__ == "__main__":
    unittest.main()
