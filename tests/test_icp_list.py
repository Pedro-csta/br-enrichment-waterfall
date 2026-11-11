"""Testes do icp_list.py e do baixar_base.py com arquivos no layout oficial da Receita
(sem cabeçalho, ";" e aspas, latin-1, dentro de zip). Os dados são fictícios.

Rodar: python -m unittest discover -s tests
"""

import csv
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import baixar_base  # noqa: E402
import icp_list  # noqa: E402


def estabelecimento(basico, ordem="0001", dv="00", matriz="1", fantasia="", situacao="02",
                    inicio="20150101", cnae="6201501", secundarios="", uf="SC", municipio="8105",
                    email="", ddd="48", fone="32223333"):
    c = [""] * 30
    c[0], c[1], c[2], c[3], c[4], c[5] = basico, ordem, dv, matriz, fantasia, situacao
    c[10], c[11], c[12] = inicio, cnae, secundarios
    c[13], c[14], c[15], c[17], c[18], c[19], c[20] = "RUA", "DAS FLORES", "10", "CENTRO", "88000000", uf, municipio
    c[21], c[22], c[27] = ddd, fone, email
    return c


def empresa(basico, razao, porte="03", capital="100000,00", natureza="2062"):
    return [basico, razao, natureza, "49", capital, porte, ""]


def gravar_zip(pasta, nome_zip, nome_interno, linhas):
    conteudo = "\n".join(";".join(f'"{v}"' for v in linha) for linha in linhas) + "\n"
    with zipfile.ZipFile(os.path.join(pasta, nome_zip), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(nome_interno, conteudo.encode("latin-1"))


def base_ficticia(pasta):
    gravar_zip(pasta, "Estabelecimentos0.zip", "K3241.K03200Y0.D61010.ESTABELE", [
        estabelecimento("11111111", fantasia="SOFT SUL", email="Contato@SoftSul.com.br"),
        estabelecimento("22222222", fantasia="AGRO SC", cnae="0111301"),                    # CNAE fora
        estabelecimento("33333333", fantasia="SOFT SP", uf="SP", municipio="7107"),         # UF fora
        estabelecimento("44444444", fantasia="SOFT BAIXADA", situacao="08"),                # baixada
        estabelecimento("11111111", ordem="0002", matriz="2", fantasia="SOFT SUL FILIAL"),  # filial
    ])
    gravar_zip(pasta, "Estabelecimentos1.zip", "K3241.K03200Y1.D61010.ESTABELE", [
        estabelecimento("55555555", fantasia="DADOS JOINVILLE", cnae="6311900", municipio="8179",
                        inicio="20250601"),                                                 # empresa nova
        estabelecimento("66666666", fantasia="MEI DEV", cnae="6201501"),                    # MEI
        estabelecimento("77777777", fantasia="LOJA COM SOFTWARE", cnae="4751201",
                        secundarios="6201501,4752100"),                                     # só secundário
    ])
    gravar_zip(pasta, "Empresas0.zip", "K3241.K03200Y0.D61010.EMPRECSV", [
        empresa("11111111", "SOFT SUL TECNOLOGIA LTDA", porte="05", capital="2500000,00"),
        empresa("55555555", "DADOS JOINVILLE LTDA", porte="01", capital="10000,00"),
        empresa("66666666", "JOAO DA SILVA 12345678901", porte="01", capital="5000,00", natureza="2135"),
        empresa("77777777", "LOJA COM SOFTWARE LTDA", porte="03"),
    ])
    gravar_zip(pasta, "Simples.zip", "F.K03200$W.SIMPLES.CSV.D61010", [
        ["66666666", "S", "20200101", "00000000", "S", "20200101", "00000000"],
        ["11111111", "N", "20070701", "20070701", "N", "00000000", "00000000"],
    ])
    gravar_zip(pasta, "Cnaes.zip", "F.K03200$Z.D61010.CNAECSV", [
        ["6201501", "Desenvolvimento de programas de computador sob encomenda"],
        ["6311900", "Tratamento de dados, provedores de serviços de aplicação"],
    ])
    gravar_zip(pasta, "Municipios.zip", "F.K03200$Z.D61010.MUNICCSV", [
        ["8105", "FLORIANOPOLIS"], ["8179", "JOINVILLE"], ["7107", "SAO PAULO"],
    ])
    gravar_zip(pasta, "Naturezas.zip", "F.K03200$Z.D61010.NATJUCSV", [
        ["2062", "Sociedade Empresária Limitada"], ["2135", "Empresário (Individual)"],
    ])


class TestICP(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pasta = os.path.join(self.tmp.name, "2026-10")
        os.makedirs(self.pasta)
        base_ficticia(self.pasta)
        self.saida = os.path.join(self.tmp.name, "out", "icp.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def rodar(self, *args):
        icp_list.main([self.pasta, "-o", self.saida, *args])
        with open(self.saida, encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def test_filtra_cnae_uf_situacao_e_so_matriz(self):
        linhas = self.rodar("--cnae", "62,6311", "--uf", "SC")
        cnpjs = {r["cnpj"] for r in linhas}
        self.assertEqual(cnpjs, {"11111111000100", "55555555000100", "66666666000100"})

    def test_junta_empresa_e_normaliza(self):
        r = {x["cnpj"]: x for x in self.rodar("--cnae", "62", "--uf", "SC")}["11111111000100"]
        self.assertEqual(r["razao_social"], "SOFT SUL TECNOLOGIA LTDA")
        self.assertEqual(r["porte"], "Demais")
        self.assertEqual(r["capital_social"], "2500000.00")
        self.assertEqual(r["natureza_juridica"], "Sociedade Empresária Limitada")
        self.assertEqual(r["municipio"], "Florianopolis")
        self.assertEqual(r["telefone"], "+554832223333")
        self.assertEqual(r["email"], "contato@softsul.com.br")
        self.assertEqual(r["setor"], "Informação e comunicação")
        self.assertEqual(r["cnae_descricao"], "Desenvolvimento de programas de computador sob encomenda")

    def test_exclui_mei(self):
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "62", "--uf", "SC", "--excluir-mei")}
        self.assertNotIn("66666666000100", cnpjs)
        self.assertIn("11111111000100", cnpjs)

    def test_porte_e_capital(self):
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "62,6311", "--porte", "EPP,DEMAIS")}
        self.assertEqual(cnpjs, {"11111111000100"})
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "62,6311", "--capital-min", "8000")}
        self.assertEqual(cnpjs, {"11111111000100", "55555555000100"})

    def test_municipio_sem_acento(self):
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "62,6311", "--municipio", "Joinville")}
        self.assertEqual(cnpjs, {"55555555000100"})
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "62", "--municipio", "Florianópolis")}
        self.assertIn("11111111000100", cnpjs)

    def test_idade(self):
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "62,6311", "--uf", "SC", "--idade-min", "3")}
        self.assertNotIn("55555555000100", cnpjs)

    def test_cnae_secundario_e_filiais(self):
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "6201", "--uf", "SC", "--cnae-secundario")}
        self.assertIn("77777777000100", cnpjs)
        cnpjs = {r["cnpj"] for r in self.rodar("--cnae", "6201", "--uf", "SC", "--incluir-filiais")}
        self.assertIn("11111111000200", cnpjs)

    def test_ordena_por_capital_e_limita(self):
        linhas = self.rodar("--cnae", "62,6311", "--uf", "SC", "--limite", "1")
        self.assertEqual([r["cnpj"] for r in linhas], ["11111111000100"])

    def test_relatorio_tem_funil(self):
        self.rodar("--cnae", "62,6311", "--uf", "SC", "--excluir-mei")
        with open(os.path.join(os.path.dirname(self.saida), "relatorio_icp.md"), encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("| Estabelecimentos na base | 8 |", texto)
        self.assertIn("| MEIs excluídos | 1 |", texto)
        self.assertIn("**TAM do ICP** | **2**", texto)

    def test_sem_empresas_avisa_e_porte_falha(self):
        os.remove(os.path.join(self.pasta, "Empresas0.zip"))
        linhas = self.rodar("--cnae", "62", "--uf", "SC")
        self.assertEqual(linhas[0]["razao_social"], "")
        with self.assertRaises(SystemExit):
            self.rodar("--cnae", "62", "--porte", "EPP")

    def test_saida_alimenta_o_enrich(self):
        import enrich
        colunas, _ = enrich.ler_csv(self.rodar("--cnae", "62") and self.saida)
        self.assertEqual(enrich.achar_coluna_cnpj(colunas), "cnpj")

    def test_utilitarios(self):
        self.assertEqual(icp_list.capital("1.234,56"), 1234.56)
        self.assertEqual(icp_list.idade_anos("2020-11-12", date(2026, 11, 11)), 5)
        self.assertIsNone(icp_list.data_iso("0"))


class TestBaixarBase(unittest.TestCase):
    def test_parse_propfind(self):
        xml = b"""<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
 <d:response><d:href>/public.php/webdav/Dados/Cadastros/CNPJ/2026-10/</d:href>
  <d:propstat><d:prop/></d:propstat></d:response>
 <d:response><d:href>/public.php/webdav/Dados/Cadastros/CNPJ/2026-10/Cnaes.zip</d:href>
  <d:propstat><d:prop><d:getcontentlength>22080</d:getcontentlength></d:prop></d:propstat></d:response>
 <d:response><d:href>/public.php/webdav/Dados/Cadastros/CNPJ/2026-10/Estabelecimentos1.zip</d:href>
  <d:propstat><d:prop><d:getcontentlength>342000000</d:getcontentlength></d:prop></d:propstat></d:response>
</d:multistatus>"""
        self.assertEqual(
            baixar_base.parse_propfind(xml),
            [("Cnaes.zip", 22080), ("Estabelecimentos1.zip", 342000000)],
        )

    def test_conjuntos(self):
        self.assertEqual(len([a for a in baixar_base.CONJUNTOS["icp"] if a.startswith("Estabelecimentos")]), 10)
        self.assertNotIn("Empresas0.zip", baixar_base.CONJUNTOS["teste"])


if __name__ == "__main__":
    unittest.main()
