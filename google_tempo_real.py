"""
Matriz de tempo de viagem via Routes API do Google (computeRouteMatrix).

Por que isso existe: a distância real de rua (OSMnx) e o tempo de viagem são
duas coisas diferentes. Estimar tempo como "distância / velocidade da via"
(o que routing_engine.calcular_matrizes faz) ignora semáforos, cruzamentos,
paradas e trânsito — o Google, mesmo sem trânsito ao vivo, já modela isso
com dados históricos reais, e por isso as duas estimativas de tempo podem
divergir MUITO (observado: ~42 km/h no modelo simples vs ~19 km/h no Google
para o mesmo trajeto). Comparar "tempo dirigindo" da rota própria contra o
tempo do Google só é uma comparação justa se os dois usarem a MESMA fonte de
tempo — por isso esta matriz busca o tempo diretamente do Google para os
mesmos pares de pontos, e o motor de roteirização usa isso em vez da
estimativa própria sempre que a chamada tiver sucesso.

A distância (km) continua vindo do OSMnx — essa parte já é real e é o que
desenha a rota no mapa; só o TEMPO precisava de uma fonte melhor.
"""
import os

import numpy as np
import requests

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
ROUTES_API_URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"
LIMITE_ELEMENTOS_POR_CHAMADA = 600  # margem de segurança abaixo do teto de 625 da API


def _waypoint(ponto):
    lat, lng = ponto
    return {"waypoint": {"location": {"latLng": {"latitude": lat, "longitude": lng}}}}


def _preencher_bloco(Tm, pontos, origem_idx, destino_idx, routing_preference):
    body = {
        "origins": [_waypoint(pontos[i]) for i in origem_idx],
        "destinations": [_waypoint(pontos[j]) for j in destino_idx],
        "travelMode": "DRIVE",
        "routingPreference": routing_preference,
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_API_KEY,
        "X-Goog-FieldMask": "originIndex,destinationIndex,duration,condition,status",
    }
    resp = requests.post(ROUTES_API_URL, json=body, headers=headers, timeout=25)
    resp.raise_for_status()
    elementos = resp.json()
    for el in elementos:
        if el.get("condition") == "ROUTE_EXISTS":
            i = origem_idx[el["originIndex"]]
            j = destino_idx[el["destinationIndex"]]
            duracao_str = el["duration"]  # formato "160s"
            Tm[i, j] = float(duracao_str.rstrip("s"))


def matriz_tempo_google(pontos, tempo_real=True):
    """
    pontos: lista de (lat, lng), índice 0 = depósito.
    tempo_real: se True, usa routingPreference=TRAFFIC_AWARE (reflete
        condições de trânsito no momento da chamada). Se False, usa
        TRAFFIC_UNAWARE (mais rápido/barato, mas sem trânsito).

    Retorna matriz NxN de segundos, com NaN nas posições em que o Google não
    retornou uma rota (para o chamador decidir se cai de volta para outra
    fonte). Pode levantar exceção (erro de rede, API não habilitada,
    quota excedida) — o chamador deve tratar isso e ter um fallback, nunca
    travar a aplicação por causa desta chamada extra.
    """
    n = len(pontos)
    Tm = np.full((n, n), np.nan)
    routing_preference = "TRAFFIC_AWARE" if tempo_real else "TRAFFIC_UNAWARE"

    tam_origem = max(1, LIMITE_ELEMENTOS_POR_CHAMADA // max(n, 1))
    todos_destinos = list(range(n))
    for i0 in range(0, n, tam_origem):
        origem_idx = list(range(i0, min(i0 + tam_origem, n)))
        _preencher_bloco(Tm, pontos, origem_idx, todos_destinos, routing_preference)

    np.fill_diagonal(Tm, 0)
    return Tm
