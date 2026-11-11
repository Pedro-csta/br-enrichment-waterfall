"""Baixa a base aberta de CNPJ da Receita Federal (último mês disponível).

Uso:
    python baixar_base.py                     # conjunto "teste": ~350 MB, bom para experimentar
    python baixar_base.py --conjunto icp      # tudo que o icp_list.py usa: ~7 GB
    python baixar_base.py --mes 2026-10 --arquivos Estabelecimentos3.zip,Cnaes.zip

Os arquivos ficam em dados_receita/AAAA-MM/. Downloads interrompidos continuam
de onde pararam na próxima execução.
"""

import argparse
import base64
import os
import re
import sys
import time
import urllib.request
from xml.etree import ElementTree

# Compartilhamento público da Receita (Nextcloud) com a pasta Dados/Cadastros/CNPJ.
TOKEN = "gn672Ad4CF8N6TK"
WEBDAV = "https://arquivos.receitafederal.gov.br/public.php/webdav/Dados/Cadastros/CNPJ/"
DOWNLOAD = f"https://arquivos.receitafederal.gov.br/public.php/dav/files/{TOKEN}/Dados/Cadastros/CNPJ/"

TABELAS = ["Cnaes.zip", "Municipios.zip", "Naturezas.zip"]
CONJUNTOS = {
    # Um arquivo de estabelecimentos (~10% da base) e as tabelas de apoio, sem Empresas:
    # serve para testar filtros de CNAE, UF, município e idade. Porte e capital ficam de fora.
    "teste": ["Estabelecimentos1.zip"] + TABELAS,
    # Tudo que o icp_list.py usa: estabelecimentos, empresas, Simples e tabelas.
    "icp": [f"Estabelecimentos{i}.zip" for i in range(10)]
    + [f"Empresas{i}.zip" for i in range(10)] + ["Simples.zip"] + TABELAS,
}


def propfind(url):
    """Lista uma pasta do WebDAV: devolve [(nome, tamanho_em_bytes)]."""
    credencial = base64.b64encode(f"{TOKEN}:".encode()).decode()
    req = urllib.request.Request(
        url, method="PROPFIND", headers={"Authorization": f"Basic {credencial}", "Depth": "1"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return parse_propfind(resp.read())


def parse_propfind(xml_bytes):
    ns = {"d": "DAV:"}
    itens = []
    for resposta in ElementTree.fromstring(xml_bytes).findall("d:response", ns):
        href = resposta.find("d:href", ns).text or ""
        nome = href.rstrip("/").split("/")[-1]
        tamanho = resposta.find(".//d:getcontentlength", ns)
        itens.append((nome, int(tamanho.text) if tamanho is not None and tamanho.text else 0))
    return itens[1:]  # o primeiro item é a própria pasta


def ultimo_mes():
    meses = sorted(n for n, _ in propfind(WEBDAV) if re.fullmatch(r"\d{4}-\d{2}", n))
    if not meses:
        raise SystemExit("Não encontrei as pastas mensais no site da Receita.")
    return meses[-1]


def baixar(url, destino, tamanho_total):
    ja_baixado = os.path.getsize(destino) if os.path.exists(destino) else 0
    if tamanho_total and ja_baixado == tamanho_total:
        print(f"  {os.path.basename(destino)}: já baixado", file=sys.stderr)
        return
    if ja_baixado > tamanho_total:
        ja_baixado = 0
    req = urllib.request.Request(url, headers={"Range": f"bytes={ja_baixado}-"} if ja_baixado else {})
    inicio, ultimo_aviso = time.monotonic(), 0.0
    with urllib.request.urlopen(req, timeout=120) as resp, \
            open(destino, "ab" if ja_baixado and resp.status == 206 else "wb") as f:
        baixado = ja_baixado if resp.status == 206 else 0
        while True:
            bloco = resp.read(1 << 20)
            if not bloco:
                break
            f.write(bloco)
            baixado += len(bloco)
            agora = time.monotonic()
            if agora - ultimo_aviso > 2:
                mb_s = (baixado - ja_baixado) / 1e6 / max(agora - inicio, 0.001)
                pct = 100 * baixado / tamanho_total if tamanho_total else 0
                print(f"  {os.path.basename(destino)}: {pct:5.1f}% ({mb_s:.1f} MB/s)", file=sys.stderr)
                ultimo_aviso = agora
    print(f"  {os.path.basename(destino)}: ok", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Baixa a base aberta de CNPJ da Receita Federal.")
    ap.add_argument("--conjunto", choices=sorted(CONJUNTOS), default="teste")
    ap.add_argument("--arquivos", help="Lista explícita de arquivos (substitui --conjunto)")
    ap.add_argument("--mes", help="AAAA-MM (padrão: o mais recente)")
    ap.add_argument("--pasta", default="dados_receita")
    args = ap.parse_args(argv)

    mes = args.mes or ultimo_mes()
    disponiveis = dict(propfind(WEBDAV + mes + "/"))
    pedidos = [a.strip() for a in args.arquivos.split(",")] if args.arquivos else CONJUNTOS[args.conjunto]
    faltando = [a for a in pedidos if a not in disponiveis]
    if faltando:
        raise SystemExit(f"Não existem em {mes}: {', '.join(faltando)}")

    destino = os.path.join(args.pasta, mes)
    os.makedirs(destino, exist_ok=True)
    total_gb = sum(disponiveis[a] for a in pedidos) / 1e9
    print(f"Base {mes}: {len(pedidos)} arquivos, {total_gb:.1f} GB → {destino}", file=sys.stderr)
    for nome in pedidos:
        baixar(DOWNLOAD + mes + "/" + nome, os.path.join(destino, nome), disponiveis[nome])
    print(f"\nPronto. Próximo passo:\n  python icp_list.py {destino} --cnae 62 --uf SC", file=sys.stderr)


if __name__ == "__main__":
    main()
