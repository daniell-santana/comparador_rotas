"""
Motor de roteirização.

Responsabilidades:
- Baixar/cachear o grafo de ruas real (OpenStreetMap via OSMnx), com
  velocidade e tempo de viagem estimados por trecho.
- Calcular matrizes de distância (m) e tempo (s) entre todos os pontos,
  usando o caminho mais curto real sobre a malha viária (não linha reta).
- Resolver o problema de roteirização de veículos (VRP) com OR-Tools,
  respeitando capacidade de carga, janela de horário por cliente e
  jornada máxima por veículo. Suporta 1..N veículos.
"""
import os
import math
import threading

import networkx as nx
import numpy as np
import osmnx as ox
from ortools.constraint_solver import routing_enums_pb2, pywrapcp

GRAPH_CACHE_DIR = os.getenv("GRAPH_CACHE_DIR", "cache")
PLACE_NAME = os.getenv("OSM_PLACE", "São Paulo, Brazil")
VELOCIDADE_PADRAO_KMH = 30  # fallback para vias sem 'maxspeed' no OSM

# "true"/"1" simplifica o grafo (menos nós, MENOS RAM, mas reintroduz o risco
# de retas em vias sem geometria salva — mitigado pelo reparo automático via
# Google, ver montar_geometria_rota). Padrão é não simplificar (mais preciso,
# mais RAM). Em hospedagem com pouca memória, combine isso com BBOX_* antes
# de simplesmente pagar por uma máquina maior.
GRAFO_SIMPLIFICAR = os.getenv("GRAFO_SIMPLIFICAR", "false").lower() in ("1", "true", "yes")

TEMPO_LIMITE_BUSCA_PADRAO_S = 20
CONFIG_SOLVER_VRP = {
    "estrategia_inicial": "PATH_CHEAPEST_ARC",
    "metaheuristica": "GUIDED_LOCAL_SEARCH",
    "tempo_limite_busca_s": TEMPO_LIMITE_BUSCA_PADRAO_S,
    "funcao_objetivo": "distância total (m)",
    "dimensoes_restricao": ["Capacidade", "Relogio", "Jornada"],
}

_grafo = None
_grafo_lock = threading.Lock()


# --------------------------------------------------------------------------
# Grafo de ruas
# --------------------------------------------------------------------------

def _bbox_configurado():
    """Se as 4 variáveis de ambiente BBOX_* estiverem definidas, baixa só
    esse retângulo em vez da cidade inteira, reduz MUITO o uso de memória
    (importante para hospedagem com RAM limitada, como o plano gratuito ou
    Starter do Render, os dois têm 512MB, então isso importa mesmo pagando)."""
    chaves = ("BBOX_NORTE", "BBOX_SUL", "BBOX_LESTE", "BBOX_OESTE")
    valores = [os.getenv(k) for k in chaves]
    if all(valores):
        return tuple(float(v) for v in valores)
    return None


def _nome_cache():
    """O nome do arquivo de cache reflete a configuração (bbox ou cidade
    inteira, simplificado ou não) — se você trocar BBOX_*/GRAFO_SIMPLIFICAR
    num ambiente com disco persistente, isso evita reaproveitar por engano
    um cache antigo maior/menor do que o configurado agora."""
    bbox = _bbox_configurado()
    if bbox:
        assinatura = "bbox_" + "_".join(f"{v:.3f}" for v in bbox)
    else:
        assinatura = PLACE_NAME.lower().replace(" ", "_").replace(",", "")
    sufixo_simplify = "simples" if GRAFO_SIMPLIFICAR else "nosimplify"
    return os.path.join(GRAPH_CACHE_DIR, f"grafo_{assinatura}_{sufixo_simplify}.graphml")


GRAPH_CACHE_PATH = os.getenv("GRAPH_CACHE_PATH") or _nome_cache()


def obter_grafo():
    """Carrega o grafo de ruas (do cache em disco, se existir), já com
    velocidade e tempo de viagem calculados por aresta. Baixar o grafo de
    São Paulo inteiro é lento (minutos) e consome bastante RAM (a versão sem
    simplificação, usada aqui, tem bem mais nós que o padrão do OSMnx); por
    isso ele é cacheado em disco e reutilizado entre reinícios do servidor.
    Se BBOX_NORTE/SUL/LESTE/OESTE estiverem definidas, baixa só esse
    retângulo (recomendado para hospedagem com RAM limitada) em vez do
    município inteiro."""
    global _grafo
    if _grafo is not None:
        return _grafo
    with _grafo_lock:
        if _grafo is not None:
            return _grafo
        precisa_resalvar = False
        if os.path.exists(GRAPH_CACHE_PATH):
            print(f"[routing_engine] Carregando grafo do cache: {GRAPH_CACHE_PATH}")
            G = ox.load_graphml(GRAPH_CACHE_PATH)
        else:
            bbox = _bbox_configurado()
            if bbox:
                norte, sul, leste, oeste = bbox
                print(f"[routing_engine] Baixando grafo do OpenStreetMap para a bbox "
                      f"N={norte} S={sul} L={leste} O={oeste} (simplify={GRAFO_SIMPLIFICAR})...")
                G = ox.graph_from_bbox((oeste, sul, leste, norte), network_type="drive",
                                        simplify=GRAFO_SIMPLIFICAR)
            else:
                print(f"[routing_engine] Baixando grafo do OpenStreetMap para '{PLACE_NAME}' inteiro "
                      f"(simplify={GRAFO_SIMPLIFICAR})... pode levar minutos e consumir bastante RAM — "
                      f"considere configurar BBOX_NORTE/SUL/LESTE/OESTE e/ou GRAFO_SIMPLIFICAR=true "
                      f"em produção com RAM limitada.")
                G = ox.graph_from_place(PLACE_NAME, network_type="drive", simplify=GRAFO_SIMPLIFICAR)
            precisa_resalvar = True

        G, foi_podado = _garantir_componente_conexo(G)
        precisa_resalvar = precisa_resalvar or foi_podado

        if not _tem_travel_time(G):
            G = _adicionar_velocidades_e_tempos(G)
            precisa_resalvar = True

        if precisa_resalvar:
            os.makedirs(os.path.dirname(GRAPH_CACHE_PATH) or ".", exist_ok=True)
            ox.save_graphml(G, GRAPH_CACHE_PATH)
            print("[routing_engine] Grafo processado e cacheado com sucesso.")

        _grafo = G
        return _grafo


def _garantir_componente_conexo(G):
    """Mantém só o maior componente fortemente conexo do grafo: garante que
    existe caminho de ida E volta entre quaisquer dois nós roteáveis. Sem
    isso, pares de pontos em 'ilhas' desconectadas da malha viária caem no
    fallback de linha reta em calcular_matrizes."""
    n_antes = G.number_of_nodes()
    G = ox.truncate.largest_component(G, strongly=True)
    foi_podado = G.number_of_nodes() != n_antes
    if foi_podado:
        print(f"[routing_engine] Removidos {n_antes - G.number_of_nodes()} nós "
              f"desconectados da malha viária principal.")
    return G, foi_podado


def _tem_travel_time(G):
    _, _, dados = next(iter(G.edges(data=True)))
    return "travel_time" in dados


def _adicionar_velocidades_e_tempos(G):
    """Estima velocidade (km/h) e tempo de viagem (s) por aresta, usando o
    'maxspeed' do OSM quando disponível e um fallback fixo caso contrário."""
    try:
        G = ox.routing.add_edge_speeds(G, fallback=VELOCIDADE_PADRAO_KMH)
        G = ox.routing.add_edge_travel_times(G)
    except AttributeError:
        # versões do osmnx anteriores à 2.0 expõem essas funções direto no pacote
        G = ox.add_edge_speeds(G, fallback=VELOCIDADE_PADRAO_KMH)
        G = ox.add_edge_travel_times(G)
    return G


# --------------------------------------------------------------------------
# Matrizes de distância / tempo
# --------------------------------------------------------------------------

def _menor_aresta(grafo, u, v, atributo):
    """Em um MultiDiGraph pode haver mais de uma via entre dois nós; pega a menor."""
    return min(d.get(atributo, 0) for d in grafo[u][v].values())


def _haversine_m(p1, p2):
    R = 6371000
    lat1, lon1, lat2, lon2 = map(math.radians, [p1[0], p1[1], p2[0], p2[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def calcular_matrizes(pontos, grafo):
    """
    pontos: lista de (lat, lng); índice 0 = depósito.

    Retorna:
      D:      matriz NxN de distâncias reais em metros (caminho mais curto na malha viária)
      Tm:     matriz NxN de tempos de viagem em segundos (mesmo caminho, somando travel_time)
      rotas:  matriz NxN com a lista de nós do caminho, para reconstruir a geometria depois
    """
    n = len(pontos)
    nos = [ox.distance.nearest_nodes(grafo, lng, lat) for lat, lng in pontos]

    D = np.zeros((n, n))
    Tm = np.zeros((n, n))
    rotas = [[None] * n for _ in range(n)]

    for i in range(n):
        for j in range(i + 1, n):
            if nos[i] == nos[j]:
                continue
            try:
                caminho = nx.shortest_path(grafo, nos[i], nos[j], weight="length")
                dist = sum(_menor_aresta(grafo, u, v, "length")
                           for u, v in zip(caminho[:-1], caminho[1:]))
                tempo = sum(_menor_aresta(grafo, u, v, "travel_time")
                            for u, v in zip(caminho[:-1], caminho[1:]))
            except nx.NetworkXNoPath:
                dist = _haversine_m(pontos[i], pontos[j])
                tempo = dist / (VELOCIDADE_PADRAO_KMH * 1000 / 3600)
                caminho = None
            D[i, j] = D[j, i] = dist
            Tm[i, j] = Tm[j, i] = tempo
            rotas[i][j] = rotas[j][i] = caminho
    return D, Tm, rotas


def _coords_do_trecho(grafo, u, v):
    """Coordenadas [lat,lng] do trecho de rua entre os nós u e v, e se usou
    geometria real da via (True) ou caiu numa reta nó-a-nó (False, usa a
    geometria real (atributo 'geometry', um shapely LineString) quando
    disponível, o OSMnx guarda isso em arestas resultantes de simplificação
    (fusão de vários cruzamentos intermediários numa via mais longa). Sem
    usar essa geometria, ligar só os dois nós-extremos com reta corta a curva
    real da via. Cai numa reta nó-a-nó só quando a aresta realmente não tem
    geometria salva, o que É esperado para trechos curtos entre cruzamentos
    vizinhos (reta e curva real coincidem), mas fica sinalizado quando esse
    salto é longo."""
    dados = min(grafo[u][v].values(), key=lambda d: d.get("length", 0))
    geometria = dados.get("geometry")
    if geometria is not None:
        pontos_xy = list(geometria.coords)  # shapely: (lon, lat)
        no_u_xy = (grafo.nodes[u]["x"], grafo.nodes[u]["y"])
        no_v_xy = (grafo.nodes[v]["x"], grafo.nodes[v]["y"])
        dist_inicio = math.dist(pontos_xy[0], no_u_xy)
        dist_fim = math.dist(pontos_xy[0], no_v_xy)
        if dist_fim < dist_inicio:
            pontos_xy = pontos_xy[::-1]  # LineString pode estar armazenada na direção v->u
        return [[lat, lon] for lon, lat in pontos_xy], True
    return [
        [grafo.nodes[u]["y"], grafo.nodes[u]["x"]],
        [grafo.nodes[v]["y"], grafo.nodes[v]["x"]],
    ], False


def _circuidade(coords):
    """Razão entre a distância realmente percorrida (soma de todos os
    segmentos do trecho) e a distância em linha reta do início ao fim. Perto
    de 1.0 = trecho quase reto. Serve como diagnóstico objetivo: uma via real
    de mais de ~300m com circuidade < 1.02 é suspeita (raríssimo em malha
    urbana comum — mais esperado numa via expressa/marginal genuinamente reta
    do que num bug, mas vale checar)."""
    if len(coords) < 2:
        return 1.0, 0.0
    reta = _haversine_m(coords[0], coords[-1])
    percorrida = sum(_haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))
    if reta < 1:
        return 1.0, percorrida
    return percorrida / reta, reta


LIMIAR_SALTO_SEM_GEOMETRIA_M = 200  # acima disso, um "pulo" nó-a-nó sem curva salva é suspeito


def montar_geometria_rota(ordem, pontos, rotas, grafo, reparo_callback=None):
    """Retorna a lista de trechos entre paradas consecutivas de uma rota
    (fecha o ciclo de volta ao primeiro ponto = depósito). Cada trecho é
    {'coords': [[lat,lng], ...], 'real': bool, 'circuidade': float,
    'suspeito_reta': bool, 'saltos_sem_geometria': [...]}.

    'real'=False só ocorre quando calcular_matrizes não achou caminho na
    malha viária entre os dois pontos.

    'suspeito_reta'=True é um alerta sobre o trecho INTEIRO (ponta a ponta)
    ficar quase perfeitamente reto, mas um trecho longo com muitos pontos
    pode ter boa circuidade MÉDIA mesmo escondendo, no meio dele, um único
    salto reto e longo entre dois nós vizinhos sem geometria salva. Isso
    acontece quando a via original do OpenStreetMap, naquele pedaço
    específico, foi digitalizada com poucos vértices (comum em trevos/
    viadutos de áreas mapeadas de forma mais grosseira), não é um bug de
    simplificação, é limitação do dado fonte.

    reparo_callback(origem, destino) -> [[lat,lng],...] | None: se
    fornecido, é chamado SÓ para os saltos detectados (raro), pra buscar a
    geometria real desse pedacinho específico numa fonte externa (ex:
    Google Directions) e substituir a reta por ela. O resto da rota
    continua vindo do OSMnx normalmente."""
    trechos = []
    for i in range(len(ordem)):
        u, v = ordem[i], ordem[(i + 1) % len(ordem)]
        caminho = rotas[u][v]
        if caminho:
            coords = []
            saltos_sem_geometria = []
            for a, b in zip(caminho[:-1], caminho[1:]):
                pedaco, usou_geometria = _coords_do_trecho(grafo, a, b)
                if not usou_geometria:
                    dist_salto = _haversine_m(pedaco[0], pedaco[-1])
                    if dist_salto > LIMIAR_SALTO_SEM_GEOMETRIA_M:
                        reparo = reparo_callback(pedaco[0], pedaco[-1]) if reparo_callback else None
                        reparo_util = reparo is not None and len(reparo) > 2
                        saltos_sem_geometria.append({
                            "coords": [pedaco[0], pedaco[-1]],
                            "distancia_m": round(dist_salto),
                            "reparado": reparo_util,
                        })
                        if reparo_util:
                            pedaco = reparo
                if coords and coords[-1] == pedaco[0]:
                    pedaco = pedaco[1:]
                coords.extend(pedaco)
            circuidade, dist_reta = _circuidade(coords)
            suspeito = dist_reta > 300 and circuidade < 1.02
            trechos.append({
                "coords": coords, "real": True,
                "circuidade": round(circuidade, 3),
                "suspeito_reta": suspeito,
                "n_pontos": len(coords),
                "saltos_sem_geometria": saltos_sem_geometria,
            })
        else:
            trechos.append({
                "coords": [list(pontos[u]), list(pontos[v])], "real": False,
                "circuidade": 1.0, "suspeito_reta": False, "n_pontos": 2,
                "saltos_sem_geometria": [],
            })
    return trechos


# --------------------------------------------------------------------------
# Solver VRP (OR-Tools): capacidade + janela de horário + jornada máxima
# --------------------------------------------------------------------------

def resolver_vrp(D, Tm, demandas, capacidades, janelas, horizonte_relogio_s,
                  tempo_maximo_por_veiculo, num_veiculos, deposito=0,
                  limite_busca_segundos=TEMPO_LIMITE_BUSCA_PADRAO_S):
    """
    D, Tm:      matrizes NxN (metros, segundos)
    demandas:   lista de N valores (kg); demandas[deposito] = 0
    capacidades: lista de num_veiculos valores (kg)
    janelas:    lista de N tuplas (inicio_s, fim_s) no relógio do dia
                (0 = início do expediente); janelas[deposito] tipicamente
                (0, horizonte_relogio_s)
    horizonte_relogio_s: duração do expediente (ex.: 08h-18h = 36000s), teto
                do relógio da rota E o quanto o veículo pode ficar parado
                esperando a janela do próximo cliente abrir.
    tempo_maximo_por_veiculo: jornada de trabalho do motorista (deslocamento
                + atendimento). Tempo de ESPERA parado NÃO consome jornada,
                se o motorista tem 5h de jornada e passa 3h dirigindo/
                atendendo, sobram 2h de saldo, mesmo que ele tenha ficado
                parado horas esperando uma janela abrir.
    deposito:   índice do depósito (0 por padrão)

    Retorna lista de dicts por veículo, {'ordem': [nós], 'chegadas': [segundos
    no relógio do dia]}, ou None se não houver solução viável.
    """
    n = D.shape[0]
    manager = pywrapcp.RoutingIndexManager(n, num_veiculos, deposito)
    routing = pywrapcp.RoutingModel(manager)

    D_int, T_int = D.astype(int), Tm.astype(int)

    def dist_callback(i, j):
        return D_int[manager.IndexToNode(i)][manager.IndexToNode(j)]

    def tempo_callback(i, j):
        return T_int[manager.IndexToNode(i)][manager.IndexToNode(j)]

    dist_idx = routing.RegisterTransitCallback(dist_callback)
    tempo_idx = routing.RegisterTransitCallback(tempo_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(dist_idx)  # otimiza por distância

    # Dimensão de capacidade
    def demanda_callback(i):
        return int(demandas[manager.IndexToNode(i)])
    demanda_idx = routing.RegisterUnaryTransitCallback(demanda_callback)
    routing.AddDimensionWithVehicleCapacity(
        demanda_idx, 0, [int(c) for c in capacidades], True, "Capacidade"
    )

    # Dimensão 1: Relógio do dia: garante a janela de horário de cada
    # cliente. Slack generoso (até o horizonte inteiro) permite o veículo
    # esperar parado até a janela do próximo cliente abrir, sem violar nada.
    horizonte_int = int(horizonte_relogio_s)
    routing.AddDimension(tempo_idx, horizonte_int, horizonte_int, True, "Relogio")
    dim_relogio = routing.GetDimensionOrDie("Relogio")
    for node in range(1, n):
        idx = manager.NodeToIndex(node)
        inicio, fim = janelas[node]
        dim_relogio.CumulVar(idx).SetRange(int(inicio), int(fim))

    # Dimensão 2: Jornada de trabalho: soma só o tempo de deslocamento entre
    # paradas (slack=0 → tempo parado esperando não entra na conta), limitada
    # à jornada máxima de cada veículo.
    jornada_int = int(tempo_maximo_por_veiculo)
    routing.AddDimension(tempo_idx, 0, jornada_int, True, "Jornada")
    dim_jornada = routing.GetDimensionOrDie("Jornada")
    for v in range(num_veiculos):
        dim_jornada.SetSpanUpperBoundForVehicle(jornada_int, v)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(limite_busca_segundos)

    solucao = routing.SolveWithParameters(params)
    if solucao is None:
        return None

    resultado = []
    for v in range(num_veiculos):
        idx = routing.Start(v)
        ordem, chegadas = [], []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            ordem.append(node)
            chegadas.append(solucao.Value(dim_relogio.CumulVar(idx)))
            idx = solucao.Value(routing.NextVar(idx))
        resultado.append({"ordem": ordem, "chegadas": chegadas})
    return resultado
