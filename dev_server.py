"""Servidor local que imita a Vercel: serve o index.html e as funções de /api.

Uso:
    python dev_server.py            # http://localhost:8000
"""

import importlib.util
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

RAIZ = os.path.dirname(os.path.abspath(__file__))


def carregar_funcao(nome):
    caminho = os.path.join(RAIZ, "api", f"{nome}.py")
    spec = importlib.util.spec_from_file_location(f"api_{nome}", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.handler


FUNCOES = {
    "/api/enrich": carregar_funcao("enrich"),
    "/api/info": carregar_funcao("info"),
}


class Roteador(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=RAIZ, **kwargs)

    def _funcao(self):
        return FUNCOES.get(self.path.split("?", 1)[0].rstrip("/"))

    def _delegar(self, metodo):
        funcao = self._funcao()
        if funcao is None:
            return False
        # Reaproveita a conexão já aberta com a classe da função (mesma interface da Vercel).
        self.__class__ = type("Funcao", (funcao,), {})
        getattr(self, metodo)()
        return True

    def do_GET(self):
        if not self._delegar("do_GET"):
            super().do_GET()

    def do_POST(self):
        if not self._delegar("do_POST"):
            self.send_error(404)

    def do_OPTIONS(self):
        if not self._delegar("do_OPTIONS"):
            self.send_error(404)


if __name__ == "__main__":
    porta = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Abrindo em http://localhost:{porta}")
    ThreadingHTTPServer(("", porta), Roteador).serve_forever()
