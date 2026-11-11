"""Enriquece uma lista de empresas brasileiras a partir do CNPJ.

Uso:
    python enrich.py data/empresas_exemplo.csv -o saida/empresas_enriquecidas.csv

Lê um CSV (separado por vírgula ou ponto e vírgula), consulta as APIs
públicas em cascata e grava o CSV enriquecido e um relatório de cobertura
em Markdown.
"""

import argparse
import csv
import os
import sys
from collections import Counter
from datetime import date

import cnpj as cnpj_util
from providers import CAMPOS, ORDEM_PADRAO
from waterfall import CAMPOS_OBRIGATORIOS_PADRAO, CAMPOS_SAIDA, Cascata

STATUS = ["completo", "parcial", "nao_encontrado", "cnpj_invalido"]


def ler_csv(caminho):
    with open(caminho, encoding="utf-8-sig", newline="") as f:
        amostra = f.read(4096)
        f.seek(0)
        try:
            dialeto = csv.Sniffer().sniff(amostra, delimiters=",;\t")
        except csv.Error:
            dialeto = csv.excel
        leitor = csv.DictReader(f, dialect=dialeto)
        return leitor.fieldnames or [], list(leitor)


def achar_coluna_cnpj(colunas, escolhida=None):
    if escolhida:
        if escolhida not in colunas:
            raise SystemExit(f"Coluna '{escolhida}' não existe. Colunas: {', '.join(colunas)}")
        return escolhida
    for coluna in colunas:
        if "cnpj" in coluna.lower():
            return coluna
    raise SystemExit("Não achei uma coluna com 'cnpj' no nome. Use --coluna.")


def pct(parte, total):
    return 100.0 * parte / total if total else 0.0


def montar_relatorio(arquivo, linhas, resultados, cascata):
    unicos = list(resultados.values())
    validos = [r for r in unicos if r["status"] != "cnpj_invalido"]
    status = Counter(r["status"] for r in unicos)
    primeiro = cascata.provedores[0].nome
    n = len(validos)

    out = []
    out.append(f"# Relatório de enriquecimento — {date.today().isoformat()}\n")
    out.append(f"- Arquivo: `{arquivo}`")
    out.append(f"- Linhas lidas: {linhas} · CNPJs únicos: {len(unicos)} · válidos: {n}")
    out.append(f"- Cascata: {' → '.join(p.nome for p in cascata.provedores)}")
    out.append(f"- Campos obrigatórios: {', '.join(cascata.campos_obrigatorios)}\n")

    out.append("## Status\n")
    out.append("| Status | Empresas |")
    out.append("| --- | --- |")
    for s in STATUS:
        out.append(f"| {s} | {status.get(s, 0)} |")

    out.append(f"\n## Cobertura por campo (base: {n} CNPJs válidos)\n")
    out.append(f"| Campo | Só {primeiro} | Cascata | Ganho |")
    out.append("| --- | --- | --- | --- |")
    for campo in CAMPOS_SAIDA:
        antes = sum(1 for r in validos if r["registro_primeiro_provedor"].get(campo))
        depois = sum(1 for r in validos if r["registro"].get(campo))
        marca = " *" if campo in cascata.campos_obrigatorios else ""
        out.append(
            f"| {campo}{marca} | {pct(antes, n):.0f}% | {pct(depois, n):.0f}% | "
            f"{pct(depois, n) - pct(antes, n):+.0f} p.p. |"
        )
    out.append("\n\\* campo obrigatório: a cascata só chama o próximo provedor se ele faltar.")

    obrig = cascata.campos_obrigatorios
    if n:
        media_antes = sum(
            sum(1 for c in obrig if r["registro_primeiro_provedor"].get(c)) for r in validos
        ) / (n * len(obrig))
        media_depois = sum(
            sum(1 for c in obrig if r["registro"].get(c)) for r in validos
        ) / (n * len(obrig))
        out.append(
            f"\n**Campos obrigatórios preenchidos:** {100 * media_antes:.0f}% só com {primeiro} "
            f"→ {100 * media_depois:.0f}% com a cascata."
        )

    out.append("\n## Custo em chamadas\n")
    out.append("| Provedor | Chamadas à API | Cache | Falhas | Campos que preencheu |")
    out.append("| --- | --- | --- | --- | --- |")
    preencheu = Counter(
        fonte for r in validos for fonte in r["fontes"].values() if not fonte.startswith("derivado")
    )
    for p in cascata.provedores:
        out.append(
            f"| {p.nome} | {cascata.chamadas[p.nome]} | {cascata.cache_hits[p.nome]} | "
            f"{cascata.erros[p.nome]} | {preencheu[p.nome]} |"
        )
    consultas = sum(len(r["provedores_consultados"]) for r in validos)
    maximo = n * len(cascata.provedores)
    if maximo:
        out.append(
            f"\nA parada antecipada usou {consultas} de {maximo} consultas possíveis "
            f"({pct(maximo - consultas, maximo):.0f}% evitadas)."
        )
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Enriquecimento de empresas brasileiras por CNPJ, em cascata.")
    ap.add_argument("entrada", help="CSV com uma coluna de CNPJ")
    ap.add_argument("-o", "--saida", default="saida/empresas_enriquecidas.csv")
    ap.add_argument("--relatorio", help="Markdown do relatório (padrão: ao lado da saída)")
    ap.add_argument("--coluna", help="Nome da coluna de CNPJ (padrão: a primeira com 'cnpj' no nome)")
    ap.add_argument("--provedores", default=",".join(ORDEM_PADRAO), help="Ordem da cascata")
    ap.add_argument(
        "--obrigatorios",
        default=",".join(CAMPOS_OBRIGATORIOS_PADRAO),
        help=f"Campos que justificam chamar o próximo provedor. Opções: {', '.join(CAMPOS)}",
    )
    ap.add_argument("--sem-cache", action="store_true", help="Ignora e não grava o cache local")
    args = ap.parse_args(argv)

    colunas, linhas = ler_csv(args.entrada)
    col_cnpj = achar_coluna_cnpj(colunas, args.coluna)
    obrigatorios = [c.strip() for c in args.obrigatorios.split(",") if c.strip()]
    invalidos = [c for c in obrigatorios if c not in CAMPOS]
    if invalidos:
        raise SystemExit(f"Campo obrigatório desconhecido: {', '.join(invalidos)}")

    cascata = Cascata(
        provedores=[p.strip() for p in args.provedores.split(",") if p.strip()],
        campos_obrigatorios=obrigatorios,
        cache_dir=None if args.sem_cache else ".cache",
    )

    resultados = {}
    for i, linha in enumerate(linhas, 1):
        bruto = linha.get(col_cnpj, "")
        chave = cnpj_util.limpar(bruto)
        if chave not in resultados:
            resultados[chave] = cascata.enriquecer(bruto)
        r = resultados[chave]
        print(f"[{i}/{len(linhas)}] {r['cnpj']}: {r['status']} "
              f"({' → '.join(r['provedores_consultados']) or '-'})", file=sys.stderr)

    extras = ["cnpj_formatado"] + CAMPOS_SAIDA + ["status_enriquecimento", "fontes", "provedores_consultados"]
    nomes = {c: (f"{c}_enriquecido" if c in colunas else c) for c in extras}
    os.makedirs(os.path.dirname(os.path.abspath(args.saida)), exist_ok=True)
    with open(args.saida, "w", encoding="utf-8-sig", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=colunas + [nomes[c] for c in extras])
        escritor.writeheader()
        for linha in linhas:
            chave = cnpj_util.limpar(linha.get(col_cnpj, ""))
            r = resultados[chave]
            saida = dict(linha)
            saida[nomes["cnpj_formatado"]] = r["cnpj"]
            for campo in CAMPOS_SAIDA:
                valor = r["registro"].get(campo)
                if isinstance(valor, float):
                    valor = f"{valor:.2f}"
                saida[nomes[campo]] = "" if valor is None else valor
            saida[nomes["status_enriquecimento"]] = r["status"]
            saida[nomes["fontes"]] = "; ".join(f"{k}={v}" for k, v in r["fontes"].items())
            saida[nomes["provedores_consultados"]] = " > ".join(r["provedores_consultados"])
            escritor.writerow(saida)

    relatorio = montar_relatorio(args.entrada, len(linhas), resultados, cascata)
    caminho_rel = args.relatorio or os.path.join(os.path.dirname(os.path.abspath(args.saida)), "relatorio.md")
    with open(caminho_rel, "w", encoding="utf-8") as f:
        f.write(relatorio)
    print(relatorio)
    print(f"CSV: {args.saida}\nRelatório: {caminho_rel}", file=sys.stderr)


if __name__ == "__main__":
    main()
