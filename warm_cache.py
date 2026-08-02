"""
Aquece o cache do grafo de ruas ANTES do serviço começar a receber
requisições. Sem isso, a primeira chamada a /comparar depois de cada deploy
dispararia o download do OpenStreetMap (minutos) dentro do tempo de resposta
da requisição — e provavelmente estouraria o timeout do servidor de produção
(gunicorn) ou do proxy da hospedagem.

Uso: chamado automaticamente pelo buildCommand do render.yaml. Também pode
ser rodado manualmente:

    python scripts/warm_cache.py

Não depende de GOOGLE_API_KEY (só toca o grafo, não a API do Google), então
funciona mesmo se essa variável ainda não estiver configurada no ambiente
de build.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routing_engine  # noqa: E402


def main():
    inicio = time.time()
    print("[warm_cache] Iniciando aquecimento do grafo de ruas...")
    grafo = routing_engine.obter_grafo()
    duracao = time.time() - inicio
    print(f"[warm_cache] Concluído em {duracao:.1f}s — "
          f"{grafo.number_of_nodes()} nós, {grafo.number_of_edges()} arestas.")
    print(f"[warm_cache] Cache salvo em: {routing_engine.GRAPH_CACHE_PATH}")


if __name__ == "__main__":
    main()
