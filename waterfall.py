"""Cascata de enriquecimento: consulta os provedores em ordem e para
assim que os campos obrigatórios estiverem preenchidos.

Regra de merge: vale o primeiro valor não vazio, na ordem dos provedores.
Cada campo guarda o provedor que o preencheu, para auditoria.
"""

import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter

import cnpj as cnpj_util
from providers import CAMPOS, ORDEM_PADRAO, PROVEDORES

CAMPOS_OBRIGATORIOS_PADRAO = [
    "razao_social",
    "situacao",
    "cnae_principal",
    "municipio",
    "uf",
    "telefone",
    "email",
]

CAMPOS_SAIDA = CAMPOS + ["setor", "dominio"]

# Seções da CNAE 2.0 por divisão (2 primeiros dígitos), usadas para o campo "setor".
_SECOES_CNAE = [
    (1, 3, "A", "Agropecuária"),
    (5, 9, "B", "Indústrias extrativas"),
    (10, 33, "C", "Indústria de transformação"),
    (35, 35, "D", "Eletricidade e gás"),
    (36, 39, "E", "Água, esgoto e resíduos"),
    (41, 43, "F", "Construção"),
    (45, 47, "G", "Comércio"),
    (49, 53, "H", "Transporte e logística"),
    (55, 56, "I", "Alojamento e alimentação"),
    (58, 63, "J", "Informação e comunicação"),
    (64, 66, "K", "Serviços financeiros"),
    (68, 68, "L", "Atividades imobiliárias"),
    (69, 75, "M", "Serviços profissionais e técnicos"),
    (77, 82, "N", "Serviços administrativos"),
    (84, 84, "O", "Administração pública"),
    (85, 85, "P", "Educação"),
    (86, 88, "Q", "Saúde e serviços sociais"),
    (90, 93, "R", "Artes, cultura e esporte"),
    (94, 96, "S", "Outros serviços"),
    (97, 97, "T", "Serviços domésticos"),
    (99, 99, "U", "Organismos internacionais"),
]

# Provedores de e-mail gratuitos: não servem como domínio da empresa.
_EMAIL_GRATUITO = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yahoo.com.br",
    "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br", "live.com",
    "icloud.com", "globo.com", "msn.com",
}


def setor_por_cnae(cnae: str):
    if not cnae or len(cnae) < 2:
        return None
    divisao = int(cnae[:2])
    for inicio, fim, _secao, nome in _SECOES_CNAE:
        if inicio <= divisao <= fim:
            return nome
    return None


def dominio_por_email(email: str):
    if not email or "@" not in email:
        return None
    dominio = email.split("@", 1)[1].strip().lower()
    if dominio in _EMAIL_GRATUITO:
        return None
    # Contadores costumam cadastrar o próprio e-mail; ainda assim é o melhor sinal público.
    return dominio


def _com_derivados(registro: dict) -> dict:
    registro["setor"] = setor_por_cnae(registro.get("cnae_principal"))
    registro["dominio"] = dominio_por_email(registro.get("email"))
    return registro


def http_get_json(url: str, timeout: float = 20.0):
    """GET simples com urllib. Devolve (status_http, json_ou_None)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "br-enrichment-waterfall/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        return erro.code, None


class Cascata:
    def __init__(
        self,
        provedores=None,
        campos_obrigatorios=None,
        http_get=http_get_json,
        cache_dir=".cache",
        dormir=time.sleep,
        relogio=time.monotonic,
        max_tentativas=3,
    ):
        nomes = provedores or ORDEM_PADRAO
        desconhecidos = [n for n in nomes if n not in PROVEDORES]
        if desconhecidos:
            raise ValueError(f"Provedor desconhecido: {', '.join(desconhecidos)}")
        self.provedores = [PROVEDORES[n] for n in nomes]
        self.campos_obrigatorios = campos_obrigatorios or CAMPOS_OBRIGATORIOS_PADRAO
        self.http_get = http_get
        self.cache_dir = cache_dir
        self.dormir = dormir
        self.relogio = relogio
        self.max_tentativas = max_tentativas
        self._ultima_chamada = {}
        self.chamadas = Counter()   # chamadas reais à API, por provedor
        self.cache_hits = Counter()
        self.erros = Counter()

    # ---------------------------------------------------------------- cache
    def _caminho_cache(self, provedor, cnpj):
        return os.path.join(self.cache_dir, provedor.nome, f"{cnpj}.json")

    def _ler_cache(self, provedor, cnpj):
        if not self.cache_dir:
            return None
        caminho = self._caminho_cache(provedor, cnpj)
        if not os.path.exists(caminho):
            return None
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)

    def _gravar_cache(self, provedor, cnpj, status, dados):
        if not self.cache_dir:
            return
        caminho = self._caminho_cache(provedor, cnpj)
        os.makedirs(os.path.dirname(caminho), exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"status": status, "dados": dados}, f, ensure_ascii=False)

    # ------------------------------------------------------------ chamadas
    def _respeitar_limite(self, provedor):
        ultima = self._ultima_chamada.get(provedor.nome)
        if ultima is not None:
            espera = provedor.intervalo_minimo - (self.relogio() - ultima)
            if espera > 0:
                self.dormir(espera)
        self._ultima_chamada[provedor.nome] = self.relogio()

    def consultar(self, provedor, cnpj):
        """Devolve o registro convertido, ou None se o provedor não tem o CNPJ ou falhou."""
        em_cache = self._ler_cache(provedor, cnpj)
        if em_cache is not None:
            self.cache_hits[provedor.nome] += 1
            if em_cache["status"] != 200:
                return None
            return provedor.converter(em_cache["dados"])

        for tentativa in range(1, self.max_tentativas + 1):
            self._respeitar_limite(provedor)
            self.chamadas[provedor.nome] += 1
            try:
                status, dados = self.http_get(provedor.url(cnpj))
            except (OSError, ValueError):
                # Falha de rede ou resposta que não é JSON: tenta de novo e depois desiste.
                status, dados = None, None
            if status == 200:
                self._gravar_cache(provedor, cnpj, status, dados)
                return provedor.converter(dados)
            if status == 404:
                self._gravar_cache(provedor, cnpj, status, None)
                return None
            if status in (400, 422):
                return None
            # 429, 5xx ou falha de rede: espera e tenta de novo.
            if tentativa < self.max_tentativas:
                self.dormir(max(provedor.intervalo_minimo, 5.0) * tentativa)
        self.erros[provedor.nome] += 1
        return None

    # ------------------------------------------------------------- cascata
    def _faltando(self, registro):
        return [c for c in self.campos_obrigatorios if not registro.get(c)]

    def enriquecer(self, valor_cnpj):
        cnpj = cnpj_util.limpar(valor_cnpj)
        resultado = {
            "cnpj": cnpj_util.formatar(cnpj),
            "registro": {},
            "fontes": {},
            "registro_primeiro_provedor": {},
            "provedores_consultados": [],
            "status": "",
        }
        if not cnpj_util.valido(cnpj):
            resultado["status"] = "cnpj_invalido"
            return resultado

        registro, fontes = {}, {}
        for i, provedor in enumerate(self.provedores):
            if i > 0 and not self._faltando(registro):
                break  # completo: não gasta chamada no próximo provedor
            resultado["provedores_consultados"].append(provedor.nome)
            dados = self.consultar(provedor, cnpj)
            if not dados:
                continue
            for campo, valor in dados.items():
                if not registro.get(campo):
                    registro[campo] = valor
                    fontes[campo] = provedor.nome
            if i == 0:
                resultado["registro_primeiro_provedor"] = _com_derivados(dict(dados))

        if registro:
            _com_derivados(registro)
            for derivado, origem in (("setor", "cnae_principal"), ("dominio", "email")):
                if registro[derivado]:
                    fontes[derivado] = f"derivado:{origem}"

        resultado["registro"] = registro
        resultado["fontes"] = fontes
        if not registro:
            resultado["status"] = "nao_encontrado"
        elif self._faltando(registro):
            resultado["status"] = "parcial"
        else:
            resultado["status"] = "completo"
        return resultado
