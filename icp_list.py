"""Monta listas de empresas por ICP a partir da base aberta de CNPJ da Receita Federal.

Uso:
    python icp_list.py dados_receita/2026-10 --cnae 62,6311 --uf SC,SP --porte EPP,DEMAIS \
        --idade-min 2 -o listas/icp.csv

Lê os arquivos da Receita (zip ou extraídos), filtra os estabelecimentos pelo
ICP, junta razão social, porte e capital, opcionalmente exclui MEIs, e grava
um CSV com a coluna "cnpj" (pronto para o enrich.py) e um relatório de TAM.
"""

import argparse
import csv
import glob
import io
import os
import sys
import time
import unicodedata
import zipfile
from collections import Counter
from datetime import date

from waterfall import setor_por_cnae

# Layout dos arquivos (sem cabeçalho, separados por ";", tudo entre aspas, latin-1).
EST = {  # Estabelecimentos: 30 colunas
    "cnpj_basico": 0, "cnpj_ordem": 1, "cnpj_dv": 2, "matriz_filial": 3, "nome_fantasia": 4,
    "situacao": 5, "inicio_atividade": 10, "cnae_principal": 11, "cnae_secundaria": 12,
    "tipo_logradouro": 13, "logradouro": 14, "numero": 15, "complemento": 16, "bairro": 17,
    "cep": 18, "uf": 19, "municipio": 20, "ddd1": 21, "telefone1": 22, "email": 27,
}
EMP = {"cnpj_basico": 0, "razao_social": 1, "natureza": 2, "capital": 4, "porte": 5}
SIMPLES = {"cnpj_basico": 0, "opcao_mei": 4, "data_exclusao_mei": 6}

SITUACOES = {"01": "Nula", "02": "Ativa", "03": "Suspensa", "04": "Inapta", "08": "Baixada"}
PORTES = {"00": "Não informado", "01": "ME", "03": "EPP", "05": "Demais"}
PORTE_CODIGO = {"ME": "01", "EPP": "03", "DEMAIS": "05", "NAO_INFORMADO": "00"}

SAIDA = [
    "cnpj", "razao_social", "nome_fantasia", "situacao", "data_abertura", "idade_anos", "porte",
    "natureza_juridica", "capital_social", "cnae_principal", "cnae_descricao", "setor",
    "municipio", "uf", "cep", "endereco", "telefone", "email", "matriz_filial", "mei",
]


# --------------------------------------------------------------------------
# Leitura dos arquivos da Receita
# --------------------------------------------------------------------------

def arquivos(pasta, prefixo_zip, sufixo_extraido):
    """Acha os arquivos de um tipo: Estabelecimentos3.zip ou K3241...ESTABELE."""
    achados = sorted(glob.glob(os.path.join(pasta, f"{prefixo_zip}*.zip")))
    achados += sorted(
        p for p in glob.glob(os.path.join(pasta, "*"))
        if sufixo_extraido in os.path.basename(p).upper() and not p.lower().endswith(".zip")
    )
    return achados


def linhas(caminho):
    """Itera as linhas de um arquivo da Receita como listas de campos."""
    if caminho.lower().endswith(".zip"):
        zf = zipfile.ZipFile(caminho)
        bruto = zf.open(zf.namelist()[0])
    else:
        zf, bruto = None, open(caminho, "rb")
    try:
        texto = io.TextIOWrapper(bruto, encoding="latin-1", newline="")
        for linha in texto:
            linha = linha.rstrip("\r\n")
            if not linha:
                continue
            # Todos os campos vêm entre aspas: dividir em '";"' é ~5x mais rápido que o csv.
            campos = linha[1:-1].split('";"') if linha.startswith('"') and linha.endswith('"') else None
            if campos is None or len(campos) < 2:
                campos = next(csv.reader([linha], delimiter=";"))
            yield campos
    finally:
        bruto.close()
        if zf:
            zf.close()


def tabela(pasta, prefixo_zip, sufixo_extraido):
    """Lê uma tabela de apoio (Cnaes, Municipios, Naturezas) como dict código -> descrição."""
    resultado = {}
    for caminho in arquivos(pasta, prefixo_zip, sufixo_extraido):
        for campos in linhas(caminho):
            if len(campos) >= 2:
                resultado[campos[0]] = campos[1]
    return resultado


# --------------------------------------------------------------------------
# Normalização
# --------------------------------------------------------------------------

def sem_acento(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).upper().strip()


def titulo(texto):
    minusculas = {"de", "da", "do", "das", "dos", "e"}
    palavras = (texto or "").lower().split()
    return " ".join(p if i and p in minusculas else p.capitalize() for i, p in enumerate(palavras))


def data_iso(aaaammdd):
    if not aaaammdd or len(aaaammdd) != 8 or aaaammdd == "00000000":
        return None
    return f"{aaaammdd[:4]}-{aaaammdd[4:6]}-{aaaammdd[6:]}"


def idade_anos(iso, hoje):
    if not iso:
        return None
    a, m, d = (int(x) for x in iso.split("-"))
    return hoje.year - a - ((hoje.month, hoje.day) < (m, d))


def telefone(ddd, numero):
    d = "".join(ch for ch in f"{ddd}{numero}" if ch.isdigit())
    return f"+55{d}" if len(d) in (10, 11) else ""


def capital(valor):
    try:
        return float((valor or "0").replace(".", "").replace(",", "."))
    except ValueError:
        return 0.0


def lista(valor, maiusculas=True):
    itens = [v.strip() for v in (valor or "").split(",") if v.strip()]
    return [v.upper() for v in itens] if maiusculas else itens


# --------------------------------------------------------------------------
# Filtros
# --------------------------------------------------------------------------

class Filtros:
    def __init__(self, args, municipios, hoje):
        self.cnaes = tuple("".join(ch for ch in c if ch.isdigit()) for c in lista(args.cnae))
        self.cnae_secundario = args.cnae_secundario
        self.ufs = set(lista(args.uf))
        self.situacoes = {"02"} if not args.incluir_inativas else set(SITUACOES)
        self.so_matriz = not args.incluir_filiais
        self.portes = {PORTE_CODIGO[p] for p in lista(args.porte)} if args.porte else set()
        self.capital_min = args.capital_min
        self.excluir_mei = args.excluir_mei
        self.idade_min = args.idade_min
        self.idade_max = args.idade_max
        self.hoje = hoje
        self.municipios = set()
        if args.municipio:
            por_nome = {}
            for codigo, nome in municipios.items():
                por_nome.setdefault(sem_acento(nome), set()).add(codigo)
            for nome in lista(args.municipio, maiusculas=False):
                codigos = por_nome.get(sem_acento(nome))
                if not codigos:
                    raise SystemExit(f"Município não encontrado na tabela da Receita: {nome}")
                self.municipios |= codigos

    def estabelecimento(self, c):
        if c[EST["situacao"]] not in self.situacoes:
            return False
        if self.so_matriz and c[EST["matriz_filial"]] != "1":
            return False
        if self.ufs and c[EST["uf"]] not in self.ufs:
            return False
        if self.municipios and c[EST["municipio"]] not in self.municipios:
            return False
        if self.cnaes:
            ok = c[EST["cnae_principal"]].startswith(self.cnaes)
            if not ok and self.cnae_secundario:
                ok = any(s.startswith(self.cnaes) for s in c[EST["cnae_secundaria"]].split(","))
            if not ok:
                return False
        if self.idade_min is not None or self.idade_max is not None:
            idade = idade_anos(data_iso(c[EST["inicio_atividade"]]), self.hoje)
            if idade is None:
                return False
            if self.idade_min is not None and idade < self.idade_min:
                return False
            if self.idade_max is not None and idade > self.idade_max:
                return False
        return True

    def empresa(self, porte, capital_social):
        if self.portes and porte not in self.portes:
            return False
        if self.capital_min and capital_social < self.capital_min:
            return False
        return True


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def progresso(lidas, inicio, etapa):
    if lidas % 1_000_000 == 0:
        taxa = lidas / max(time.monotonic() - inicio, 0.001)
        print(f"  {etapa}: {lidas:,} linhas ({taxa:,.0f}/s)".replace(",", "."), file=sys.stderr)


def montar_lista(pasta, filtros, cnaes, municipios, naturezas):
    contagem = Counter()

    # 1) Estabelecimentos: filtra pelo ICP (situação, matriz, UF, município, CNAE, idade).
    achados = {}
    inicio = time.monotonic()
    est_arquivos = arquivos(pasta, "Estabelecimentos", "ESTABELE")
    if not est_arquivos:
        raise SystemExit(f"Nenhum arquivo de Estabelecimentos em {pasta}. Rode baixar_base.py antes.")
    for caminho in est_arquivos:
        print(f"Lendo {os.path.basename(caminho)}", file=sys.stderr)
        for c in linhas(caminho):
            contagem["estabelecimentos_lidos"] += 1
            progresso(contagem["estabelecimentos_lidos"], inicio, "estabelecimentos")
            if len(c) < 28 or not filtros.estabelecimento(c):
                continue
            achados.setdefault(c[EST["cnpj_basico"]], []).append(c)
    contagem["passaram_estabelecimento"] = sum(len(v) for v in achados.values())

    # 2) Empresas: razão social, natureza, capital e porte (só das empresas encontradas).
    empresas = {}
    emp_arquivos = arquivos(pasta, "Empresas", "EMPRECSV")
    if emp_arquivos:
        inicio = time.monotonic()
        for caminho in emp_arquivos:
            print(f"Lendo {os.path.basename(caminho)}", file=sys.stderr)
            for e in linhas(caminho):
                contagem["empresas_lidas"] += 1
                progresso(contagem["empresas_lidas"], inicio, "empresas")
                if e[EMP["cnpj_basico"]] in achados:
                    empresas[e[EMP["cnpj_basico"]]] = e
    elif filtros.portes or filtros.capital_min:
        raise SystemExit("Os filtros de porte e capital precisam dos arquivos Empresas*.zip.")
    else:
        print("Aviso: sem arquivos Empresas*.zip; razão social, porte e capital ficarão vazios.", file=sys.stderr)

    # 3) Simples: identifica MEIs (opção pelo MEI ativa).
    meis = set()
    simples_arquivos = arquivos(pasta, "Simples", "SIMPLES")
    if filtros.excluir_mei:
        if not simples_arquivos:
            raise SystemExit("--excluir-mei precisa do arquivo Simples.zip.")
        for caminho in simples_arquivos:
            print(f"Lendo {os.path.basename(caminho)}", file=sys.stderr)
            for s in linhas(caminho):
                if s[SIMPLES["cnpj_basico"]] in achados and s[SIMPLES["opcao_mei"]] == "S" and \
                        s[SIMPLES["data_exclusao_mei"]] in ("", "00000000"):
                    meis.add(s[SIMPLES["cnpj_basico"]])

    # 4) Junta tudo e aplica os filtros da empresa.
    registros = []
    for basico, estabelecimentos in achados.items():
        e = empresas.get(basico)
        porte = e[EMP["porte"]] if e else ""
        cap = capital(e[EMP["capital"]]) if e else 0.0
        if e is None and (filtros.portes or filtros.capital_min):
            continue
        if not filtros.empresa(porte, cap):
            continue
        mei = basico in meis
        if mei and filtros.excluir_mei:
            contagem["meis_excluidos"] += 1
            continue
        for c in estabelecimentos:
            abertura = data_iso(c[EST["inicio_atividade"]])
            cnae = c[EST["cnae_principal"]]
            endereco = " ".join(
                p for p in (c[EST["tipo_logradouro"]], c[EST["logradouro"]], c[EST["numero"]],
                            c[EST["complemento"]], "-", c[EST["bairro"]]) if p
            )
            registros.append({
                "cnpj": f"{basico}{c[EST['cnpj_ordem']]}{c[EST['cnpj_dv']]}",
                "razao_social": e[EMP["razao_social"]] if e else "",
                "nome_fantasia": c[EST["nome_fantasia"]],
                "situacao": SITUACOES.get(c[EST["situacao"]], c[EST["situacao"]]),
                "data_abertura": abertura or "",
                "idade_anos": idade_anos(abertura, filtros.hoje),
                "porte": PORTES.get(porte, porte),
                "natureza_juridica": naturezas.get(e[EMP["natureza"]], e[EMP["natureza"]]) if e else "",
                "capital_social": cap if e else "",
                "cnae_principal": cnae,
                "cnae_descricao": cnaes.get(cnae, ""),
                "setor": setor_por_cnae(cnae) or "",
                "municipio": titulo(municipios.get(c[EST["municipio"]], "")),
                "uf": c[EST["uf"]],
                "cep": c[EST["cep"]],
                "endereco": endereco.strip(" -"),
                "telefone": telefone(c[EST["ddd1"]], c[EST["telefone1"]]),
                "email": (c[EST["email"]] or "").strip().lower(),
                "matriz_filial": "Matriz" if c[EST["matriz_filial"]] == "1" else "Filial",
                "mei": "sim" if mei else ("não" if filtros.excluir_mei else ""),
            })
    contagem["na_lista"] = len(registros)
    return registros, contagem


def ordenar(registros, criterio):
    if criterio == "capital":
        registros.sort(key=lambda r: r["capital_social"] or 0, reverse=True)
    elif criterio == "abertura":
        registros.sort(key=lambda r: r["data_abertura"] or "9999")
    elif criterio == "razao":
        registros.sort(key=lambda r: r["razao_social"] or r["nome_fantasia"])
    return registros


def relatorio(pasta, args, registros, contagem, total_antes_limite):
    def pct(parte, total):
        return f"{100.0 * parte / total:.0f}%" if total else "0%"

    def top(campo, n=10):
        return Counter(r[campo] or "—" for r in registros).most_common(n)

    out = [f"# TAM do ICP — base {os.path.basename(os.path.normpath(pasta))}\n"]
    filtros = [
        ("CNAE", args.cnae + (" (inclui secundário)" if args.cnae_secundario else "") if args.cnae else None),
        ("UF", args.uf), ("Município", args.municipio), ("Porte", args.porte),
        ("Idade mínima (anos)", args.idade_min), ("Idade máxima (anos)", args.idade_max),
        ("Capital mínimo (R$)", args.capital_min),
        ("Situação", "todas" if args.incluir_inativas else "Ativa"),
        ("Estabelecimentos", "matrizes e filiais" if args.incluir_filiais else "só matrizes"),
        ("MEI", "excluídos" if args.excluir_mei else "incluídos"),
    ]
    out.append("## Filtros\n")
    out += [f"- {nome}: {valor}" for nome, valor in filtros if valor not in (None, "", 0)]

    out.append("\n## Funil\n")
    out.append("| Etapa | Quantidade |")
    out.append("| --- | --- |")
    out.append(f"| Estabelecimentos na base | {contagem['estabelecimentos_lidos']:,} |".replace(",", "."))
    out.append(f"| Passaram nos filtros de estabelecimento | {contagem['passaram_estabelecimento']:,} |".replace(",", "."))
    if args.excluir_mei:
        out.append(f"| MEIs excluídos | {contagem['meis_excluidos']:,} |".replace(",", "."))
    out.append(f"| **TAM do ICP** | **{total_antes_limite:,}** |".replace(",", "."))
    if args.limite and args.limite < total_antes_limite:
        out.append(f"| Exportados (--limite) | {len(registros):,} |".replace(",", "."))

    n = len(registros)
    out.append(f"\n## Perfil da lista exportada ({n} empresas)\n")
    for titulo_secao, campo in (("UF", "uf"), ("Porte", "porte"), ("Setor", "setor"),
                                ("CNAE principal", "cnae_descricao")):
        out.append(f"**{titulo_secao}:** " + "; ".join(f"{k} ({v})" for k, v in top(campo, 8)))
        out.append("")
    faixas = Counter()
    for r in registros:
        i = r["idade_anos"]
        faixas["sem data" if i is None else "0–2 anos" if i < 3 else "3–5 anos" if i < 6
               else "6–10 anos" if i < 11 else "mais de 10 anos"] += 1
    out.append("**Idade:** " + "; ".join(f"{k} ({v})" for k, v in faixas.most_common()))

    com_email = sum(1 for r in registros if r["email"])
    com_tel = sum(1 for r in registros if r["telefone"])
    out.append("\n## Contato na base da Receita\n")
    out.append(f"- Com e-mail: {com_email} ({pct(com_email, n)})")
    out.append(f"- Com telefone: {com_tel} ({pct(com_tel, n)})")
    out.append(
        "\nA base é um retrato mensal. Antes de prospectar, passe a lista pelo "
        "`enrich.py` para confirmar a situação atual e completar o que faltar."
    )
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lista de empresas por ICP a partir da base aberta de CNPJ.")
    ap.add_argument("pasta", help="Pasta com os arquivos da Receita (ex.: dados_receita/2026-10)")
    ap.add_argument("-o", "--saida", default="listas/icp.csv")
    ap.add_argument("--relatorio", help="Markdown do relatório (padrão: ao lado da saída)")
    ap.add_argument("--cnae", help="Prefixos de CNAE, separados por vírgula (ex.: 62,6311,7319)")
    ap.add_argument("--cnae-secundario", action="store_true", help="Aceita o CNAE também como atividade secundária")
    ap.add_argument("--uf", help="UFs separadas por vírgula (ex.: SP,SC)")
    ap.add_argument("--municipio", help="Municípios separados por vírgula (ex.: Florianópolis,Joinville)")
    ap.add_argument("--porte", help="ME, EPP, DEMAIS (separados por vírgula)")
    ap.add_argument("--idade-min", type=int, help="Anos mínimos desde a abertura")
    ap.add_argument("--idade-max", type=int, help="Anos máximos desde a abertura")
    ap.add_argument("--capital-min", type=float, default=0, help="Capital social mínimo em R$")
    ap.add_argument("--excluir-mei", action="store_true", help="Remove MEIs (precisa do Simples.zip)")
    ap.add_argument("--incluir-filiais", action="store_true", help="Inclui filiais (padrão: só matrizes)")
    ap.add_argument("--incluir-inativas", action="store_true", help="Inclui baixadas, inaptas e suspensas")
    ap.add_argument("--ordenar", choices=["capital", "abertura", "razao"], default="capital")
    ap.add_argument("--limite", type=int, help="Exporta só as N primeiras após ordenar")
    args = ap.parse_args(argv)

    if args.porte:
        invalidos = [p for p in lista(args.porte) if p not in PORTE_CODIGO]
        if invalidos:
            raise SystemExit(f"Porte inválido: {', '.join(invalidos)}. Use ME, EPP ou DEMAIS.")
    if not any([args.cnae, args.uf, args.municipio]):
        print("Aviso: sem filtro de CNAE, UF ou município a lista pode ter milhões de linhas.", file=sys.stderr)

    hoje = date.today()
    cnaes = tabela(args.pasta, "Cnaes", "CNAECSV")
    municipios = tabela(args.pasta, "Municipios", "MUNICCSV")
    naturezas = tabela(args.pasta, "Naturezas", "NATJUCSV")
    if args.municipio and not municipios:
        raise SystemExit("--municipio precisa do arquivo Municipios.zip.")
    filtros = Filtros(args, municipios, hoje)

    registros, contagem = montar_lista(args.pasta, filtros, cnaes, municipios, naturezas)
    total = len(registros)
    ordenar(registros, args.ordenar)
    if args.limite:
        registros = registros[: args.limite]

    os.makedirs(os.path.dirname(os.path.abspath(args.saida)), exist_ok=True)
    with open(args.saida, "w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=SAIDA)
        escritor.writeheader()
        for r in registros:
            linha = dict(r)
            if isinstance(linha["capital_social"], float):
                linha["capital_social"] = f"{linha['capital_social']:.2f}"
            escritor.writerow(linha)

    texto = relatorio(args.pasta, args, registros, contagem, total)
    caminho_rel = args.relatorio or os.path.join(os.path.dirname(os.path.abspath(args.saida)), "relatorio_icp.md")
    with open(caminho_rel, "w", encoding="utf-8") as f:
        f.write(texto)
    print(texto)
    print(f"CSV: {args.saida}\nRelatório: {caminho_rel}", file=sys.stderr)


if __name__ == "__main__":
    main()
