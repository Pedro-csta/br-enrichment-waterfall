"""Validação e formatação de CNPJ (dígitos verificadores da Receita Federal)."""

import re

_PESOS_1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_PESOS_2 = [6] + _PESOS_1


def limpar(valor) -> str:
    """Remove tudo que não é dígito e completa com zeros à esquerda.

    Exportações de CRM e planilhas costumam perder o zero inicial
    (ex.: 191 em vez de 00.000.000/0001-91).
    """
    digitos = re.sub(r"\D", "", str(valor or ""))
    if 0 < len(digitos) < 14:
        digitos = digitos.zfill(14)
    return digitos


def _digito(base: str, pesos: list) -> str:
    resto = sum(int(d) * p for d, p in zip(base, pesos)) % 11
    return "0" if resto < 2 else str(11 - resto)


def valido(valor) -> bool:
    cnpj = limpar(valor)
    if len(cnpj) != 14 or cnpj == cnpj[0] * 14:
        return False
    d1 = _digito(cnpj[:12], _PESOS_1)
    d2 = _digito(cnpj[:12] + d1, _PESOS_2)
    return cnpj[-2:] == d1 + d2


def formatar(valor) -> str:
    c = limpar(valor)
    if len(c) != 14:
        return str(valor)
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"
