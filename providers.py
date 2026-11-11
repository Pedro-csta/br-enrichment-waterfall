"""Adaptadores das APIs públicas de CNPJ.

Cada provedor devolve um JSON diferente. O adaptador traduz a resposta
para o mesmo esquema (CAMPOS), para que a cascata possa comparar e
completar campo a campo.
"""

import re
from datetime import datetime

# Campos que cada provedor pode preencher. "setor" e "dominio" são
# derivados depois do merge (ver waterfall.py).
CAMPOS = [
    "razao_social",
    "nome_fantasia",
    "situacao",
    "data_abertura",
    "porte",
    "natureza_juridica",
    "cnae_principal",
    "cnae_descricao",
    "municipio",
    "uf",
    "cep",
    "telefone",
    "email",
    "capital_social",
]

_MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


# --------------------------------------------------------------------------
# Normalizadores
# --------------------------------------------------------------------------

def _texto(valor):
    if valor is None:
        return None
    valor = str(valor).strip()
    return valor or None


def _titulo(valor):
    valor = _texto(valor)
    if not valor:
        return None
    palavras = valor.lower().split()
    return " ".join(
        p if (i and p in _MINUSCULAS) else p.capitalize() for i, p in enumerate(palavras)
    )


def _so_digitos(valor):
    return re.sub(r"\D", "", str(valor or ""))


def _cnae(valor):
    d = _so_digitos(valor)
    if not d or set(d) == {"0"}:
        return None
    return d.zfill(7)


def _cep(valor):
    d = _so_digitos(valor)
    return d if len(d) == 8 else None


def _telefone(valor):
    """Normaliza para E.164 (+55DDNNNNNNNN). Usa o primeiro número se vierem vários."""
    if not valor:
        return None
    primeiro = re.split(r"[/;,]", str(valor))[0]
    d = _so_digitos(primeiro)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) not in (10, 11):
        return None
    return f"+55{d}"


def _email(valor):
    valor = _texto(valor)
    if not valor or "@" not in valor:
        return None
    return valor.lower()


def _data(valor):
    """Aceita AAAA-MM-DD ou DD/MM/AAAA e devolve AAAA-MM-DD."""
    valor = _texto(valor)
    if not valor:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(valor[:10], formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _porte(valor):
    v = (_texto(valor) or "").upper()
    if not v or "NÃO INFORMADO" in v or "NAO INFORMADO" in v:
        return None
    if v == "ME" or "MICRO" in v:
        return "ME"
    if v == "EPP" or "PEQUENO" in v:
        return "EPP"
    if "DEMAIS" in v:
        return "Demais"
    return _titulo(v)


def _natureza(valor):
    # ReceitaWS manda "204-6 - Sociedade Anônima Aberta"
    valor = _texto(valor)
    if not valor:
        return None
    return re.sub(r"^\d{3}-\d\s*-\s*", "", valor)


def _numero(valor):
    if valor in (None, ""):
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _situacao(valor):
    return _titulo(valor)


def _limpo(registro: dict) -> dict:
    """Mantém só os campos conhecidos e com valor."""
    return {k: v for k, v in registro.items() if k in CAMPOS and v not in (None, "")}


# --------------------------------------------------------------------------
# Provedores
# --------------------------------------------------------------------------

class Provedor:
    nome = ""
    intervalo_minimo = 1.0  # segundos entre chamadas (respeita o limite gratuito)

    def url(self, cnpj: str) -> str:
        raise NotImplementedError

    def converter(self, dados: dict):
        """Converte a resposta da API no esquema comum. Devolve None se não encontrou."""
        raise NotImplementedError


class BrasilAPI(Provedor):
    nome = "brasilapi"
    intervalo_minimo = 1.0

    def url(self, cnpj):
        return f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"

    def converter(self, d):
        if not d or not d.get("cnpj"):
            return None
        return _limpo({
            "razao_social": _texto(d.get("razao_social")),
            "nome_fantasia": _texto(d.get("nome_fantasia")),
            "situacao": _situacao(d.get("descricao_situacao_cadastral")),
            "data_abertura": _data(d.get("data_inicio_atividade")),
            "porte": _porte(d.get("porte")),
            "natureza_juridica": _natureza(d.get("natureza_juridica")),
            "cnae_principal": _cnae(d.get("cnae_fiscal")),
            "cnae_descricao": _texto(d.get("cnae_fiscal_descricao")),
            "municipio": _titulo(d.get("municipio")),
            "uf": _texto(d.get("uf")),
            "cep": _cep(d.get("cep")),
            "telefone": _telefone(d.get("ddd_telefone_1")),
            "email": _email(d.get("email")),
            "capital_social": _numero(d.get("capital_social")),
        })


class CNPJws(Provedor):
    nome = "cnpjws"
    intervalo_minimo = 21.0  # API pública: poucas consultas por minuto

    def url(self, cnpj):
        return f"https://publica.cnpj.ws/cnpj/{cnpj}"

    def converter(self, d):
        est = (d or {}).get("estabelecimento") or {}
        if not est:
            return None
        ativ = est.get("atividade_principal") or {}
        ddd, fone = est.get("ddd1"), est.get("telefone1")
        return _limpo({
            "razao_social": _texto(d.get("razao_social")),
            "nome_fantasia": _texto(est.get("nome_fantasia")),
            "situacao": _situacao(est.get("situacao_cadastral")),
            "data_abertura": _data(est.get("data_inicio_atividade")),
            "porte": _porte((d.get("porte") or {}).get("descricao")),
            "natureza_juridica": _natureza((d.get("natureza_juridica") or {}).get("descricao")),
            "cnae_principal": _cnae(ativ.get("id")),
            "cnae_descricao": _texto(ativ.get("descricao")),
            "municipio": _titulo((est.get("cidade") or {}).get("nome")),
            "uf": _texto((est.get("estado") or {}).get("sigla")),
            "cep": _cep(est.get("cep")),
            "telefone": _telefone(f"{ddd}{fone}") if ddd and fone else None,
            "email": _email(est.get("email")),
            "capital_social": _numero(d.get("capital_social")),
        })


class CNPJa(Provedor):
    nome = "cnpja"
    intervalo_minimo = 12.0

    def url(self, cnpj):
        return f"https://open.cnpja.com/office/{cnpj}"

    def converter(self, d):
        if not d or not d.get("taxId"):
            return None
        empresa = d.get("company") or {}
        endereco = d.get("address") or {}
        ativ = d.get("mainActivity") or {}
        fones = d.get("phones") or []
        emails = d.get("emails") or []
        return _limpo({
            "razao_social": _texto(empresa.get("name")),
            "nome_fantasia": _texto(d.get("alias")),
            "situacao": _situacao((d.get("status") or {}).get("text")),
            "data_abertura": _data(d.get("founded")),
            "porte": _porte((empresa.get("size") or {}).get("acronym")),
            "natureza_juridica": _natureza((empresa.get("nature") or {}).get("text")),
            "cnae_principal": _cnae(ativ.get("id")),
            "cnae_descricao": _texto(ativ.get("text")),
            "municipio": _titulo(endereco.get("city")),
            "uf": _texto(endereco.get("state")),
            "cep": _cep(endereco.get("zip")),
            "telefone": _telefone(f"{fones[0].get('area', '')}{fones[0].get('number', '')}") if fones else None,
            "email": _email(emails[0].get("address")) if emails else None,
            "capital_social": _numero(empresa.get("equity")),
        })


class ReceitaWS(Provedor):
    nome = "receitaws"
    intervalo_minimo = 21.0

    def url(self, cnpj):
        return f"https://receitaws.com.br/v1/cnpj/{cnpj}"

    def converter(self, d):
        # A ReceitaWS responde 200 com {"status": "ERROR"} quando não encontra.
        if not d or d.get("status") == "ERROR":
            return None
        ativ = (d.get("atividade_principal") or [{}])[0]
        return _limpo({
            "razao_social": _texto(d.get("nome")),
            "nome_fantasia": _texto(d.get("fantasia")),
            "situacao": _situacao(d.get("situacao")),
            "data_abertura": _data(d.get("abertura")),
            "porte": _porte(d.get("porte")),
            "natureza_juridica": _natureza(d.get("natureza_juridica")),
            "cnae_principal": _cnae(ativ.get("code")),
            "cnae_descricao": _texto(ativ.get("text")),
            "municipio": _titulo(d.get("municipio")),
            "uf": _texto(d.get("uf")),
            "cep": _cep(d.get("cep")),
            "telefone": _telefone(d.get("telefone")),
            "email": _email(d.get("email")),
            "capital_social": _numero(d.get("capital_social")),
        })


PROVEDORES = {p.nome: p for p in (BrasilAPI(), CNPJws(), CNPJa(), ReceitaWS())}
ORDEM_PADRAO = ["brasilapi", "cnpjws", "cnpja", "receitaws"]
