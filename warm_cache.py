"""
Aquece o cache do grafo de ruas ANTES do serviço começar a receber
requisições. Sem isso, a primeira chamada a /comparar depois de cada deploy
dispararia o download do OpenStreetMap (minutos) dentro do tempo de resposta
da requisição — e provavelmente estouraria o timeout do servidor de produção
(gunicorn) ou do proxy da hospedagem.

Uso: chamado automaticamente pelo buildCommand do render.yaml. Também pode
ser rodado manualmente pra testar ANTES de fazer deploy:

    python scripts/warm_cache.py

Reporta o uso real de memória (RSS) depois de carregar o grafo — compare
esse número com o teto do seu plano de hospedagem (512MB no free/starter do
Render) antes de fazer deploy, em vez de descobrir isso em produção.

Não depende de GOOGLE_API_KEY (só toca o grafo, não a API do Google), então
funciona mesmo se essa variável ainda não estiver configurada no ambiente
de build.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import routing_engine  # noqa: E402


def _memoria_atual_mb():
    """Memória residente (RSS) do processo atual, em MB. Funciona em
    Linux/Mac (inclusive no Render, que é Linux) via o módulo padrão
    'resource'. No Windows local, esse módulo não existe — nesse caso,
    acompanhe pelo Gerenciador de Tarefas enquanto o script roda."""
    try:
        import resource
        uso = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reporta em KB, macOS em bytes — normaliza pra MB
        return uso / 1024 if sys.platform == "darwin" else uso / 1024
    except ImportError:
        return None


def main():
    inicio = time.time()
    print("[warm_cache] Iniciando aquecimento do grafo de ruas...")
    print(f"[warm_cache] Configuração: OSM_PLACE={routing_engine.PLACE_NAME!r}, "
          f"bbox={routing_engine._bbox_configurado()}, "
          f"GRAFO_SIMPLIFICAR={routing_engine.GRAFO_SIMPLIFICAR}")
    grafo = routing_engine.obter_grafo()
    duracao = time.time() - inicio
    print(f"[warm_cache] Concluído em {duracao:.1f}s — "
          f"{grafo.number_of_nodes()} nós, {grafo.number_of_edges()} arestas.")
    print(f"[warm_cache] Cache salvo em: {routing_engine.GRAPH_CACHE_PATH}")

    tamanho_arquivo_mb = os.path.getsize(routing_engine.GRAPH_CACHE_PATH) / (1024 * 1024)
    print(f"[warm_cache] Tamanho do arquivo em disco: {tamanho_arquivo_mb:.1f} MB")

    mem_mb = _memoria_atual_mb()
    if mem_mb is not None:
        print(f"[warm_cache] Memória RSS do processo depois de carregar o grafo: {mem_mb:.0f} MB")
        print(f"[warm_cache] Referência: plano free/starter do Render tem 512MB de TETO TOTAL "
              f"(incluindo Python, Flask, numpy, ortools, etc. — não só o grafo). "
              f"Se esse número já estiver acima de ~300MB sozinho, ainda não cabe com folga.")
    else:
        print("[warm_cache] Não consegui medir RSS neste sistema operacional (comum no Windows). "
              "Acompanhe o uso de memória pelo Gerenciador de Tarefas enquanto este script roda.")


if __name__ == "__main__":
    main()
