"""Código compartilhado pelas funções web (Vercel).

Arquivos em api/ que começam com "_" não viram rotas no Vercel.
Reaproveita os módulos da CLI (providers, waterfall, cnpj) sem alterá-los.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import cnpj as cnpj_util  # noqa: E402
from providers import CAMPOS, PROVEDORES, BrasilAPI  # noqa: E402
from waterfall import (  # noqa: E402
    CAMPOS_OBRIGATORIOS_PADRAO,
    Cascata,
    _com_derivados,
)

UA = "br-enrichment-waterfall-web/1.0 (+https://github.com/Pedro-csta/br-enrichment-waterfall)"
CACHE_DIR = "/tmp/cache"


class MinhaReceita(BrasilAPI):
    """minhareceita.org devolve o mesmo formato da BrasilAPI (os dois servem a base da Receita)."""

    nome = "minhareceita"
    intervalo_minimo = 0.3

    def url(self, cnpj):
        return f"https://minhareceita.org/{cnpj}"


MINHA_RECEITA = MinhaReceita()
PROVEDORES.setdefault("minhareceita", MINHA_RECEITA)

# Depois da busca no Minha Receita, a cascata só consulta estas APIs.
CASCATA_WEB = ["cnpjws", "cnpja", "receitaws"]


# ----------------------------------------------------------------- HTTP
def get_json(url, timeout=25.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as erro:
        return erro.code, None


def get_json_com_retry(url, tentativas=3, timeout=25.0, dormir=time.sleep):
    status = None
    for i in range(tentativas):
        try:
            status, dados = get_json(url, timeout=timeout)
        except (OSError, ValueError):
            status, dados = None, None
        if status == 200:
            return status, dados
        if status in (400, 404):
            return status, None
        if i < tentativas - 1:
            dormir(2.0 * (i + 1))
    return status, None


def responder(handler, status, corpo):
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(dados)))
    handler.end_headers()
    handler.wfile.write(dados)


def ler_corpo_json(handler, maximo=1_000_000):
    tamanho = int(handler.headers.get("Content-Length") or 0)
    if tamanho <= 0 or tamanho > maximo:
        return {}
    try:
        return json.loads(handler.rfile.read(tamanho).decode("utf-8"))
    except ValueError:
        return {}


def parametros(handler):
    consulta = urllib.parse.urlparse(handler.path).query
    return {k: v[-1] for k, v in urllib.parse.parse_qs(consulta).items()}


# ---------------------------------------------------------------- cidades
def cidades_da_uf(uf, http=None):
    """Cidades da UF com código IBGE: API oficial do IBGE, com a BrasilAPI de reserva."""
    http = http or (lambda url: get_json_com_retry(url, tentativas=1, timeout=8.0))
    uf = str(uf or "").strip().upper()[:2]
    if len(uf) != 2 or not uf.isalpha():
        return []
    fontes = [
        (f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios", "nome", "id"),
        (f"https://brasilapi.com.br/api/ibge/municipios/v1/{uf}?providers=gov", "nome", "codigo_ibge"),
    ]
    for url, campo_nome, campo_codigo in fontes:
        status, dados = http(url)
        if status != 200 or not isinstance(dados, list):
            continue
        cidades = [
            {"nome": str(c.get(campo_nome) or "").strip(), "ibge": str(c.get(campo_codigo) or "")}
            for c in dados
        ]
        cidades = [c for c in cidades if c["nome"] and c["ibge"].isdigit() and len(c["ibge"]) == 7]
        # Descarta respostas quebradas (ex.: o mesmo nome repetido em todas as cidades).
        if len(cidades) >= 2 and len({c["nome"] for c in cidades}) > len(cidades) // 2:
            if cidades[0]["nome"].isupper():
                for c in cidades:
                    c["nome"] = c["nome"].title()
            return sorted(cidades, key=lambda c: c["nome"])
    return []


# ------------------------------------------------------------ busca (ICP)
PORTES = {"ME": 1, "EPP": 3, "DEMAIS": 5}


def lista(valor):
    return [v.strip() for v in str(valor or "").replace(";", ",").split(",") if v.strip()]


def cnaes_validos(valor):
    codigos = []
    for bruto in lista(valor):
        digitos = "".join(c for c in bruto if c.isdigit())
        if len(digitos) != 7:
            raise ValueError(f"CNAE '{bruto}' precisa ter 7 dígitos (subclasse), ex.: 6201501")
        codigos.append(digitos)
    return codigos


def idade_em_anos(data_iso, hoje=None):
    if not data_iso:
        return None
    try:
        a, m, d = (int(x) for x in str(data_iso)[:10].split("-"))
    except ValueError:
        return None
    hoje = hoje or date.today()
    return hoje.year - a - ((hoje.month, hoje.day) < (m, d))


def passa_filtros(bruto, filtros, hoje=None):
    """Aplica no registro bruto do Minha Receita os filtros que a API não faz."""
    if not filtros.get("incluir_inativas") and bruto.get("situacao_cadastral") != 2:
        return False
    if not filtros.get("incluir_filiais") and bruto.get("identificador_matriz_filial") != 1:
        return False
    if filtros.get("excluir_mei") and bruto.get("opcao_pelo_mei"):
        return False
    portes = filtros.get("portes")
    if portes and bruto.get("codigo_porte") not in portes:
        return False
    idade = idade_em_anos(bruto.get("data_inicio_atividade"), hoje)
    if filtros.get("idade_min") is not None and (idade is None or idade < filtros["idade_min"]):
        return False
    if filtros.get("idade_max") is not None and (idade is None or idade > filtros["idade_max"]):
        return False
    capital = bruto.get("capital_social") or 0
    if filtros.get("capital_min") is not None and capital < filtros["capital_min"]:
        return False
    if filtros.get("so_com_contato") and not (bruto.get("email") or bruto.get("ddd_telefone_1")):
        return False
    return True


def ler_filtros(p):
    def numero(chave, tipo=float):
        valor = (p.get(chave) or "").strip()
        if not valor:
            return None
        return tipo(valor.replace(".", "").replace(",", ".")) if tipo is float else tipo(valor)

    filtros = {
        "uf": [u.upper() for u in lista(p.get("uf")) if len(u) == 2],
        "municipio": [m for m in lista(p.get("municipio")) if m.isdigit()],
        "cnae": cnaes_validos(p.get("cnae")),
        "cnae_secundario": p.get("cnae_secundario") in ("1", "true", "on"),
        "portes": [PORTES[x.upper()] for x in lista(p.get("porte")) if x.upper() in PORTES],
        "excluir_mei": p.get("excluir_mei", "1") in ("1", "true", "on"),
        "incluir_filiais": p.get("incluir_filiais") in ("1", "true", "on"),
        "incluir_inativas": p.get("incluir_inativas") in ("1", "true", "on"),
        "so_com_contato": p.get("so_com_contato") in ("1", "true", "on"),
        "idade_min": numero("idade_min", int),
        "idade_max": numero("idade_max", int),
        "capital_min": numero("capital_min"),
    }
    if not (filtros["uf"] or filtros["municipio"] or filtros["cnae"]):
        raise ValueError("Escolha pelo menos uma UF, cidade ou CNAE.")
    return filtros


def url_busca(filtros, cursor=None, limite=200):
    q = []
    for uf in filtros["uf"]:
        q.append(("uf", uf))
    for m in filtros["municipio"]:
        q.append(("municipio", m))
    chave_cnae = "cnae" if filtros["cnae_secundario"] else "cnae_fiscal"
    for c in filtros["cnae"]:
        q.append((chave_cnae, c))
    q.append(("limit", str(limite)))
    if cursor:
        q.append(("cursor", cursor))
    return "https://minhareceita.org/?" + urllib.parse.urlencode(q)


def resumo_empresa(bruto):
    """Registro no esquema comum (CAMPOS) + o CNPJ, para mandar ao navegador."""
    registro = MINHA_RECEITA.converter(bruto) or {}
    return {"cnpj": cnpj_util.limpar(bruto.get("cnpj")), "registro": registro}


def buscar(filtros, cursor=None, quero=50, orcamento_s=22.0, limite=200, relogio=time.monotonic,
           http=None):
    """Percorre páginas do Minha Receita até achar `quero` empresas ou estourar o orçamento de tempo."""
    http = http or get_json_com_retry
    inicio = relogio()
    empresas, lidos, fim = [], 0, False
    while len(empresas) < quero and relogio() - inicio < orcamento_s:
        status, dados = http(url_busca(filtros, cursor, limite))
        if status != 200 or not isinstance(dados, dict):
            if not empresas and lidos == 0:
                raise RuntimeError(
                    f"O Minha Receita não respondeu à busca (HTTP {status}). Tente de novo em alguns segundos."
                )
            break
        pagina = dados.get("data") or []
        lidos += len(pagina)
        for bruto in pagina:
            if passa_filtros(bruto, filtros):
                empresas.append(resumo_empresa(bruto))
        del empresas[quero:]  # o navegador para quando completa a quantidade pedida
        cursor = dados.get("cursor")
        if not cursor or not pagina:
            fim = True
            break
    return {"empresas": empresas, "cursor": cursor, "lidos": lidos, "fim": fim}


# -------------------------------------------------------- enriquecimento
def enriquecer(valor_cnpj, base=None, ultimas=None, obrigatorios=None, cascata=None,
               consultar_outros=True):
    """Completa um registro vindo do Minha Receita usando a cascata da CLI.

    `ultimas` traz o horário (epoch) da última chamada a cada provedor, guardado pelo
    navegador entre requisições, para respeitar os limites gratuitos entre instâncias.
    """
    cascata = cascata or Cascata(
        provedores=CASCATA_WEB,
        campos_obrigatorios=obrigatorios or CAMPOS_OBRIGATORIOS_PADRAO,
        cache_dir=CACHE_DIR,
        relogio=time.time,
    )
    for nome, quando in (ultimas or {}).items():
        try:
            cascata._ultima_chamada[nome] = float(quando)
        except (TypeError, ValueError):
            pass

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
        return resultado, cascata

    registro, fontes = {}, {}
    if base is None:
        resultado["provedores_consultados"].append(MINHA_RECEITA.nome)
        base = cascata.consultar(MINHA_RECEITA, cnpj) or {}
    else:
        resultado["provedores_consultados"].append(MINHA_RECEITA.nome)
    for campo, valor in (base or {}).items():
        if campo in CAMPOS and valor not in (None, ""):
            registro[campo] = valor
            fontes[campo] = MINHA_RECEITA.nome
    resultado["registro_primeiro_provedor"] = _com_derivados(dict(registro))

    for provedor in (cascata.provedores if consultar_outros else []):
        if registro and not cascata._faltando(registro):
            break
        resultado["provedores_consultados"].append(provedor.nome)
        dados = cascata.consultar(provedor, cnpj)
        if not dados:
            continue
        for campo, valor in dados.items():
            if not registro.get(campo):
                registro[campo] = valor
                fontes[campo] = provedor.nome

    if registro:
        _com_derivados(registro)
        for derivado, origem in (("setor", "cnae_principal"), ("dominio", "email")):
            if registro[derivado]:
                fontes[derivado] = f"derivado:{origem}"

    resultado["registro"] = registro
    resultado["fontes"] = fontes
    if not registro:
        resultado["status"] = "nao_encontrado"
    elif cascata._faltando(registro):
        resultado["status"] = "parcial"
    else:
        resultado["status"] = "completo"
    return resultado, cascata
