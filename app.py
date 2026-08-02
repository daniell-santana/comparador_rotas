import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

import numpy as np
import polyline as pl
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

import routing_engine as roteirizador
from dados_demo import (EXPEDIENTE_FIM_H, EXPEDIENTE_INICIO_H, formatar_hora,
                         formatar_janela, gerar_clientes)
from google_tempo_real import matriz_tempo_google
from tsp_sa import resolver_sa

load_dotenv()

app = Flask(__name__, static_folder=None)
CORS(app)

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY não definida no .env")

CAPACIDADE_PADRAO_KG = 1200
JORNADA_MAXIMA_PADRAO_H = 8
HORIZONTE_RELOGIO_S = (EXPEDIENTE_FIM_H - EXPEDIENTE_INICIO_H) * 3600
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

_cache_reparo = {}  # evita rechamar a API pro mesmo par de pontos na mesma execução


def reparar_segmento_reto(origem, destino):
    """Busca no Google Directions API a geometria real de um trecho pontual
    (origem->destino direto, sem waypoints), pra substituir cirurgicamente um
    salto reto detectado no OSMnx — chamado só para os poucos trechos
    sinalizados (raro), nunca para a rota inteira. Retorna None se não
    conseguir melhorar nada (nesse caso o salto reto original é mantido)."""
    chave = (round(origem[0], 5), round(origem[1], 5), round(destino[0], 5), round(destino[1], 5))
    if chave in _cache_reparo:
        return _cache_reparo[chave]
    try:
        url = (
            "https://maps.googleapis.com/maps/api/directions/json"
            f"?origin={origem[0]},{origem[1]}&destination={destino[0]},{destino[1]}"
            f"&key={GOOGLE_API_KEY}"
        )
        resp = requests.get(url, timeout=10).json()
        if resp["status"] != "OK":
            print(f"[app] Reparo via Google FALHOU (erro de API, status={resp['status']}) "
                  f"pra {origem}->{destino}.")
            _cache_reparo[chave] = None
            return None
        coords = pl.decode(resp["routes"][0]["overview_polyline"]["points"])
        coords = [[lat, lng] for lat, lng in coords]
        if len(coords) <= 2:
            # A API respondeu OK, mas o próprio Google também não achou curva
            # pra esse trecho — não é uma falha, é uma CONFIRMAÇÃO de que a
            # via é curta/reta mesmo (comum abaixo de ~400m). Não substitui
            # por nada "melhor", porque não existe nada melhor a substituir.
            print(f"[app] Reparo via Google CONFIRMOU reta (não é falha): {origem}->{destino} — "
                  f"o próprio Google também devolveu só {len(coords)} pontos pra esse trecho curto.")
            _cache_reparo[chave] = None
            return None
        print(f"[app] Reparo via Google OK pra {origem}->{destino}: {len(coords)} pontos "
              f"substituindo a reta original.")
        _cache_reparo[chave] = coords
        return coords
    except Exception as e:
        print(f"[app] Reparo via Google deu erro pra {origem}->{destino}: {e}")
        _cache_reparo[chave] = None
        return None


def rota_google(origin, destinations):
    """TSP puro via Google Directions API (optimize:true). Não suporta
    capacidade de carga nem janela de horário — serve como referência de
    mercado, não como concorrente direto do VRP. departure_time=now pede
    duração com trânsito real (sem isso, o Google devolve uma estimativa
    típica sem trânsito ao vivo, que é o que causava divergência de tempo
    inflada nas comparações)."""
    waypoints_str = "|".join(f"{lat},{lng}" for lat, lng in destinations)
    url = (
        "https://maps.googleapis.com/maps/api/directions/json"
        f"?origin={origin[0]},{origin[1]}&destination={origin[0]},{origin[1]}"
        f"&waypoints=optimize:true|{waypoints_str}&departure_time=now&key={GOOGLE_API_KEY}"
    )
    resposta = requests.get(url, timeout=20).json()
    if resposta["status"] != "OK":
        raise Exception(f"Erro Google API: {resposta['status']} - {resposta.get('error_message', '')}")
    rota = resposta["routes"][0]
    # Com múltiplos waypoints, o Google devolve uma perna (leg) por trecho
    # (depósito -> parada 1 -> parada 2 -> ... -> depósito). A distância/tempo
    # da rota inteira é a SOMA de todas as pernas, não apenas legs[0]. Com
    # departure_time definido, cada perna tem 'duration' (típica, sem
    # trânsito) E 'duration_in_traffic' (com trânsito real) — preferimos a
    # segunda quando disponível.
    def _duracao_leg(leg):
        return leg.get("duration_in_traffic", leg["duration"])["value"]

    distancia_total = sum(leg["distance"]["value"] for leg in rota["legs"])
    duracao_total = sum(_duracao_leg(leg) for leg in rota["legs"])

    # Horário estimado de chegada em cada parada, assumindo saída às
    # EXPEDIENTE_INICIO_H (mesma referência usada na rota própria, para a
    # comparação lado a lado fazer sentido). O último leg é o retorno ao
    # depósito, por isso fica de fora do acumulado por parada.
    chegadas, acumulado = [], 0
    for leg in rota["legs"][:-1]:
        acumulado += _duracao_leg(leg)
        chegadas.append(acumulado)

    return {
        "order": rota["waypoint_order"],
        "coords": pl.decode(rota["overview_polyline"]["points"]),
        "distance": distancia_total,
        "duration": duracao_total,
        "chegadas": chegadas,
        # Um valor por perna (n+1 pernas: depósito->1, 1->2, ..., n->depósito),
        # para montar o itinerário trecho a trecho no frontend.
        "pernas_distancia_m": [leg["distance"]["value"] for leg in rota["legs"]],
        "pernas_duracao_s": [_duracao_leg(leg) for leg in rota["legs"]],
    }


@app.route("/comparar", methods=["POST"])
def comparar():
    try:
        data = request.get_json(force=True) or {}
        origin = data.get("origin", [-23.5505, -46.6333])
        num_clientes = int(data.get("num_clientes", 10))
        raio_km = float(data.get("raio_km", 4))
        num_veiculos = int(data.get("num_veiculos", 1))
        capacidade_kg = float(data.get("capacidade_veiculo_kg", CAPACIDADE_PADRAO_KG))
        jornada_h = float(data.get("jornada_maxima_horas", JORNADA_MAXIMA_PADRAO_H))
        algoritmo = data.get("algoritmo", "vrp")  # 'vrp' ou 'sa'

        if not 2 <= num_clientes <= 25:
            return jsonify({"erro": "Número de clientes deve estar entre 2 e 25."}), 400
        if not 1 <= num_veiculos <= 6:
            return jsonify({"erro": "Número de veículos deve estar entre 1 e 6."}), 400

        clientes = gerar_clientes(origin, num_clientes, raio_km)
        destinos = [[c["lat"], c["lng"]] for c in clientes]

        # --- Rota Google: TSP puro, referência de mercado ---
        google_result = rota_google(origin, destinos)

        # --- Grafo real + matriz de distância pela malha viária ---
        pontos = [origin] + destinos
        grafo = roteirizador.obter_grafo()
        D, Tm, rotas_no = roteirizador.calcular_matrizes(pontos, grafo)

        # --- Tempo real do Google (mesma fonte que a rota de referência usa) ---
        # A distância continua vindo do OSMnx (é o que desenha a rota no mapa
        # e já é real). O tempo estimado por "distância / velocidade da via"
        # ignora semáforo, cruzamento e trânsito, e pode divergir muito do
        # tempo real do Google — então buscamos o tempo direto do Google para
        # os mesmos pares de pontos, e SÓ caímos de volta para a estimativa
        # própria onde a chamada falhar ou não retornar rota.
        tempo_real_indisponivel = False
        try:
            Tm_google = matriz_tempo_google(pontos, tempo_real=True)
            n_faltando = int(np.isnan(Tm_google).sum())
            if n_faltando:
                print(f"[app] Routes API não retornou tempo para {n_faltando} par(es) — "
                      f"usando a estimativa própria nesses casos.")
            Tm = np.where(np.isnan(Tm_google), Tm, Tm_google)
        except Exception as e:
            tempo_real_indisponivel = True
            print(f"[app] ATENÇÃO: não foi possível obter tempos reais do Google "
                  f"(Routes API) — usando a estimativa própria (sem trânsito) para "
                  f"TODOS os pares. Motivo: {e}")

        demandas = [0] + [c["demanda_kg"] for c in clientes]
        jornada_s = jornada_h * 3600
        janelas = [(0, HORIZONTE_RELOGIO_S)] + [
            (c["janela_inicio_s"], c["janela_fim_s"]) for c in clientes
        ]

        restricoes_relaxadas = False
        inicio_solver = time.time()
        if algoritmo == "sa":
            ordem, custo_sa = resolver_sa(D)
            veiculos_solucao = [{"ordem": ordem, "chegadas": None}]
        else:
            capacidades = [capacidade_kg] * num_veiculos
            veiculos_solucao = roteirizador.resolver_vrp(
                D, Tm, demandas, capacidades, janelas, HORIZONTE_RELOGIO_S,
                jornada_s, num_veiculos
            )
            if veiculos_solucao is None:
                # Fallback: relaxa capacidade/janelas para sempre devolver uma rota utilizável
                restricoes_relaxadas = True
                capacidades_relax = [c * 10 for c in capacidades]
                janelas_relax = [(0, HORIZONTE_RELOGIO_S)] * len(janelas)
                veiculos_solucao = roteirizador.resolver_vrp(
                    D, Tm, demandas, capacidades_relax, janelas_relax,
                    HORIZONTE_RELOGIO_S, jornada_s * 2, num_veiculos
                )
            if veiculos_solucao is None:
                return jsonify({
                    "erro": "Não foi possível encontrar uma rota viável nem relaxando as "
                            "restrições. Tente reduzir o número de clientes ou aumentar a "
                            "capacidade/jornada."
                }), 422
        tempo_execucao_solver_s = time.time() - inicio_solver

        veiculos_resp = []
        dist_total_custom = 0.0
        tempo_total_custom = 0.0
        paradas_no_prazo = 0
        paradas_totais = 0

        for v_idx, sol in enumerate(veiculos_solucao):
            ordem = sol["ordem"]
            if len(ordem) <= 1:
                continue  # veículo não utilizado nesta solução

            trechos = roteirizador.montar_geometria_rota(
                ordem, pontos, rotas_no, grafo, reparo_callback=reparar_segmento_reto
            )
            coords, trechos_sem_rua, diagnostico_retas, saltos_sem_geometria = [], [], [], []
            for t in trechos:
                c = t["coords"]
                if coords and coords[-1] == c[0]:
                    c = c[1:]
                coords.extend(c)
                if not t["real"]:
                    trechos_sem_rua.append(t["coords"])
                    print(f"[app] ATENÇÃO: trecho sem caminho viável na rota — "
                          f"fallback em linha reta entre {t['coords'][0]} e {t['coords'][-1]}")
                elif t["suspeito_reta"]:
                    diagnostico_retas.append({
                        "coords": t["coords"],
                        "circuidade": t["circuidade"],
                        "n_pontos": t["n_pontos"],
                    })
                    print(f"[app] DIAGNÓSTICO: trecho real (achou caminho na malha) mas quase "
                          f"perfeitamente reto — circuidade={t['circuidade']} (1.0=reta perfeita), "
                          f"{t['n_pontos']} pontos ao longo do trecho, de {t['coords'][0]} a "
                          f"{t['coords'][-1]}. Pode ser via expressa real ou merece investigar.")
                for salto in t["saltos_sem_geometria"]:
                    saltos_sem_geometria.append(salto)
                    if salto.get("reparado"):
                        print(f"[app] SALTO REPARADO: {salto['distancia_m']}m em linha reta detectados "
                              f"entre {salto['coords'][0]} e {salto['coords'][1]} — geometria real "
                              f"buscada no Google e usada no lugar da reta.")
                    else:
                        print(f"[app] SALTO SEM GEOMETRIA (NÃO REPARADO): {salto['distancia_m']}m em "
                              f"linha reta entre {salto['coords'][0]} e {salto['coords'][1]} — aresta do "
                              f"OSM sem curva salva, e o reparo via Google falhou ou não estava "
                              f"disponível. Ainda aparece como reta no mapa.")

            # Checagem final, no array JÁ COSTURADO (pós-concatenação dos
            # trechos) — tudo acima verifica cada trecho ISOLADO; isso aqui
            # verifica especificamente a COSTURA entre um trecho e o
            # próximo, ponto a ponto, no exato array que vai pro navegador.
            # Se existir bug na concatenação (não nos trechos individuais),
            # é aqui que aparece. Usa índice manual (while, não for) porque
            # o reparo pode INSERIR pontos no meio do array durante o loop.
            k = 0
            while k < len(coords) - 1:
                salto_costura = roteirizador._haversine_m(coords[k], coords[k + 1])
                if salto_costura > 300:
                    reparo = reparar_segmento_reto(coords[k], coords[k + 1])
                    reparo_util = reparo is not None and len(reparo) > 2
                    saltos_sem_geometria.append({
                        "coords": [coords[k], coords[k + 1]],
                        "distancia_m": round(salto_costura),
                        "reparado": reparo_util,
                    })
                    if reparo_util:
                        print(f"[app] SALTO NA COSTURA REPARADO: {round(salto_costura)}m entre os "
                              f"pontos {k} e {k+1} — {len(reparo)} pontos do Google substituindo "
                              f"o pulo no array final.")
                        coords[k:k + 2] = reparo  # substitui o pulo de 2 pontos pelo caminho real
                        k += len(reparo) - 1
                        continue
                    else:
                        print(f"[app] SALTO NA COSTURA (NÃO REPARADO): {round(salto_costura)}m entre "
                              f"os pontos {k} e {k+1} do array final já concatenado — {coords[k]} -> "
                              f"{coords[k+1]}.")
                k += 1

            dist_v = sum(D[ordem[i], ordem[(i + 1) % len(ordem)]] for i in range(len(ordem)))
            tempo_deslocamento_v = sum(Tm[ordem[i], ordem[(i + 1) % len(ordem)]] for i in range(len(ordem)))
            carga_v = sum(demandas[i] for i in ordem)

            # Tempo de deslocamento (soma das viagens) é diferente de tempo
            # decorrido no relógio: se o veículo esperou parado entre duas
            # paradas até a janela do próximo cliente abrir, o relógio avança
            # mas isso não é tempo "dirigindo". Calculamos os dois para deixar
            # isso explícito em vez de esconder a espera dentro de um único
            # número de minutos.
            if sol.get("chegadas"):
                ultima_chegada = sol["chegadas"][-1]
                volta_ao_deposito = Tm[ordem[-1], ordem[0]]
                tempo_decorrido_v = ultima_chegada + volta_ao_deposito
            else:
                tempo_decorrido_v = tempo_deslocamento_v  # SA: sem janelas, não há espera
            tempo_espera_v = max(0.0, tempo_decorrido_v - tempo_deslocamento_v)

            paradas = []
            for pos, node_idx in enumerate(ordem):
                if node_idx == 0:
                    continue
                cliente = clientes[node_idx - 1]
                chegada_s = sol["chegadas"][pos] if sol.get("chegadas") else None
                dentro_janela = (
                    chegada_s is not None
                    and cliente["janela_inicio_s"] <= chegada_s <= cliente["janela_fim_s"]
                )
                paradas_totais += 1
                if chegada_s is None or dentro_janela:
                    paradas_no_prazo += 1
                paradas.append({
                    "ordem": pos,
                    "cliente_id": cliente["id"],
                    "lat": cliente["lat"],
                    "lng": cliente["lng"],
                    "demanda_kg": cliente["demanda_kg"],
                    "janela": formatar_janela(cliente["janela_inicio_s"], cliente["janela_fim_s"]),
                    "chegada_estimada": formatar_hora(chegada_s),
                    "dentro_da_janela": dentro_janela if chegada_s is not None else None,
                })

            # Pernas (trechos) da rota, na ordem percorrida, incluindo a volta
            # ao depósito. Isola por trecho quanto foi deslocamento e quanto
            # foi espera — é o que permite ver ONDE exatamente a espera
            # aconteceu, em vez de só o total acumulado.
            def _nome_no(idx):
                return "Depósito" if idx == 0 else f"Cliente {clientes[idx - 1]['id']}"

            if sol.get("chegadas"):
                chegadas_estendida = list(sol["chegadas"]) + [tempo_decorrido_v]
            else:
                chegadas_estendida = None
            pernas = []
            acumulado_sa = 0.0
            for i in range(len(ordem)):
                u, v = ordem[i], ordem[(i + 1) % len(ordem)]
                tempo_viagem_leg = float(Tm[u, v])
                if chegadas_estendida:
                    chegada_de = chegadas_estendida[i]
                    chegada_para = chegadas_estendida[i + 1]
                    espera_leg = max(0.0, (chegada_para - chegada_de) - tempo_viagem_leg)
                    chegada_fmt = formatar_hora(chegada_para)
                else:
                    espera_leg = 0.0
                    acumulado_sa += tempo_viagem_leg
                    chegada_fmt = formatar_hora(acumulado_sa)
                janela_destino = (
                    formatar_janela(clientes[v - 1]["janela_inicio_s"], clientes[v - 1]["janela_fim_s"])
                    if v != 0 else None
                )
                trecho_geo = trechos[i]  # mesmo índice: pernas[i] percorre o mesmo trecho que trechos[i]
                pernas.append({
                    "de": _nome_no(u),
                    "para": _nome_no(v),
                    "de_lat": pontos[u][0], "de_lng": pontos[u][1],
                    "para_lat": pontos[v][0], "para_lng": pontos[v][1],
                    "distancia_m": float(D[u, v]),
                    "duracao_viagem_s": tempo_viagem_leg,
                    "espera_antes_s": float(espera_leg),
                    "chegada": chegada_fmt,
                    "janela_destino": janela_destino,
                    "geometria_real": trecho_geo["real"],
                    "circuidade": trecho_geo["circuidade"],
                    "n_pontos_geometria": trecho_geo["n_pontos"],
                })

            dist_total_custom += dist_v
            tempo_total_custom += tempo_deslocamento_v
            veiculos_resp.append({
                "veiculo": v_idx + 1,
                "coords": coords,
                "trechos_sem_rua": trechos_sem_rua,
                "diagnostico_retas": diagnostico_retas,
                "saltos_sem_geometria": saltos_sem_geometria,
                "distancia_m": float(dist_v),
                "duracao_s": float(tempo_deslocamento_v),
                "tempo_decorrido_s": float(tempo_decorrido_v),
                "tempo_espera_s": float(tempo_espera_v),
                "chegada_volta_deposito": formatar_hora(tempo_decorrido_v),
                "carga_kg": carga_v,
                "capacidade_kg": capacidade_kg,
                "paradas": paradas,
                "pernas": pernas,
            })

        google_paradas = []
        for pos, cliente_idx in enumerate(google_result["order"]):
            cliente = clientes[cliente_idx]
            google_paradas.append({
                "ordem": pos + 1,
                "cliente_id": cliente["id"],
                "lat": cliente["lat"],
                "lng": cliente["lng"],
                "chegada_estimada": formatar_hora(google_result["chegadas"][pos]),
            })

        # Pernas do Google no mesmo formato das pernas do veículo (sem espera,
        # já que o Google não trabalha com janela de horário).
        nomes_ordem_google = ["Depósito"] + [
            f"Cliente {clientes[i]['id']}" for i in google_result["order"]
        ] + ["Depósito"]
        coords_ordem_google = [origin] + [destinos[i] for i in google_result["order"]] + [origin]
        pernas_google = []
        for i in range(len(nomes_ordem_google) - 1):
            pernas_google.append({
                "de": nomes_ordem_google[i],
                "para": nomes_ordem_google[i + 1],
                "de_lat": coords_ordem_google[i][0], "de_lng": coords_ordem_google[i][1],
                "para_lat": coords_ordem_google[i + 1][0], "para_lng": coords_ordem_google[i + 1][1],
                "distancia_m": float(google_result["pernas_distancia_m"][i]),
                "duracao_viagem_s": float(google_result["pernas_duracao_s"][i]),
                "espera_antes_s": 0.0,
                "janela_destino": None,
                "chegada": (
                    formatar_hora(google_result["chegadas"][i])
                    if i < len(google_result["chegadas"])
                    else formatar_hora(google_result["duration"])
                ),
            })

        total_trechos_sem_rua = sum(len(v["trechos_sem_rua"]) for v in veiculos_resp)
        total_trechos_suspeitos = sum(len(v["diagnostico_retas"]) for v in veiculos_resp)
        total_saltos_sem_geometria = sum(len(v["saltos_sem_geometria"]) for v in veiculos_resp)
        tempo_decorrido_max = max((v["tempo_decorrido_s"] for v in veiculos_resp), default=0.0)

        economia_distancia = (
            (google_result["distance"] - dist_total_custom) / google_result["distance"] * 100
            if google_result["distance"] > 0 else 0
        )
        economia_tempo = (
            (google_result["duration"] - tempo_total_custom) / google_result["duration"] * 100
            if google_result["duration"] > 0 else 0
        )

        if algoritmo == "sa":
            modelo_info = {
                "algoritmo_nome": "Simulated Annealing (TSP puro, sem restrições)",
                "hiperparametros": {
                    "temperatura_inicial": 1000, "temperatura_final": 1,
                    "alpha_resfriamento": 0.97, "iteracoes_por_temperatura": 200,
                },
                "funcao_objetivo": "distância total (m)",
                "valor_objetivo_m": round(float(dist_total_custom), 1),
                "tempo_execucao_s": round(tempo_execucao_solver_s, 3),
                "n_clientes": num_clientes,
                "n_veiculos": 1,
            }
        else:
            modelo_info = {
                "algoritmo_nome": "VRP com OR-Tools (capacidade + janela de horário + jornada)",
                "hiperparametros": {
                    "estrategia_inicial": roteirizador.CONFIG_SOLVER_VRP["estrategia_inicial"],
                    "metaheuristica": roteirizador.CONFIG_SOLVER_VRP["metaheuristica"],
                    "tempo_limite_busca_s": roteirizador.CONFIG_SOLVER_VRP["tempo_limite_busca_s"],
                },
                "funcao_objetivo": roteirizador.CONFIG_SOLVER_VRP["funcao_objetivo"],
                "valor_objetivo_m": round(float(dist_total_custom), 1),
                "dimensoes_restricao": roteirizador.CONFIG_SOLVER_VRP["dimensoes_restricao"],
                "tempo_execucao_s": round(tempo_execucao_solver_s, 3),
                "n_clientes": num_clientes,
                "n_veiculos": num_veiculos,
                "restricoes_relaxadas": restricoes_relaxadas,
            }

        resposta = {
            "clientes": clientes,
            "modelo_info": modelo_info,
            "rota_google": {
                "coords": google_result["coords"],
                "distance": google_result["distance"],
                "duration": google_result["duration"],
                "order": google_result["order"],
                "paradas": google_paradas,
                "pernas": pernas_google,
                "chegada_volta_deposito": formatar_hora(google_result["duration"]),
                "suporta_restricoes": False,
            },
            "rota_personalizada": {
                "algoritmo": algoritmo,
                "veiculos": veiculos_resp,
                "distancia_total_m": float(dist_total_custom),
                "duracao_total_s": float(tempo_total_custom),
                "tempo_decorrido_max_s": float(tempo_decorrido_max),
                "restricoes_relaxadas": restricoes_relaxadas,
                "paradas_no_prazo": paradas_no_prazo,
                "paradas_totais": paradas_totais,
                "total_trechos_sem_rua": total_trechos_sem_rua,
                "total_trechos_suspeitos": total_trechos_suspeitos,
                "total_saltos_sem_geometria": total_saltos_sem_geometria,
                "tempo_real_indisponivel": tempo_real_indisponivel,
            },
            "economia_percentual": round(economia_distancia, 2),
            "economia_tempo_percentual": round(economia_tempo, 2),
        }

        nome_log = _gravar_log_verificacao(resposta, num_clientes, num_veiculos, algoritmo)
        resposta["log_url"] = f"/logs/{nome_log}"

        return jsonify(resposta)
    except Exception as e:
        traceback.print_exc()
        return jsonify({"erro": str(e)}), 500


def _gravar_log_verificacao(resposta, num_clientes, num_veiculos, algoritmo):
    """Grava um .txt legível cruzando, PARA CADA TRECHO desenhado no mapa, a
    ordem exata em que ele foi percorrido, se achou via real ou caiu em
    fallback, e a circuidade — para servir de contraprova independente do
    que é mostrado na tela. Sempre sobrescreve 'ultima_simulacao.txt' (fácil
    de achar) e também grava uma cópia com timestamp (histórico)."""
    linhas = [
        f"=== LOG DE VERIFICAÇÃO — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===",
        f"Parâmetros: {num_clientes} clientes, {num_veiculos} veículo(s), algoritmo={algoritmo}",
        "",
        "--- ROTA GOOGLE (referência, geometria vem pronta do Google, sem diagnóstico próprio) ---",
    ]
    for i, p in enumerate(resposta["rota_google"]["pernas"], start=1):
        linhas.append(
            f"Perna {i}: {p['de']} -> {p['para']} | {p['distancia_m']/1000:.2f} km | "
            f"chegada {p['chegada']}"
        )

    for v in resposta["rota_personalizada"]["veiculos"]:
        linhas += ["", f"--- ROTA PRÓPRIA · Veículo {v['veiculo']} (ordem de desenho no mapa) ---"]
        for i, p in enumerate(v["pernas"], start=1):
            flag = "OK (via real)" if p["geometria_real"] else "*** FALLBACK: SEM VIA REAL ***"
            suspeito = " <<< CIRCUIDADE BAIXA, CONFIRME NO MAPA" if p["circuidade"] < 1.02 and p["distancia_m"] > 300 else ""
            linhas.append(
                f"Perna {i}: {p['de']} -> {p['para']} | {p['distancia_m']/1000:.2f} km | "
                f"chegada {p['chegada']} | {flag} | circuidade={p['circuidade']} | "
                f"{p['n_pontos_geometria']} pontos de geometria{suspeito}"
            )
        if v["saltos_sem_geometria"]:
            n_reparados = sum(1 for s in v["saltos_sem_geometria"] if s.get("reparado"))
            linhas.append("")
            linhas.append(f"  >>> {len(v['saltos_sem_geometria'])} SALTO(S) RETO(S) DETECTADO(S) NO ARRAY "
                           f"FINAL ({n_reparados} consertado(s) automaticamente com dado do Google):")
            for s in v["saltos_sem_geometria"]:
                status = "CONSERTADO (Google)" if s.get("reparado") else "ainda reto no mapa"
                linhas.append(f"      {s['distancia_m']}m em linha reta entre {s['coords'][0]} e "
                               f"{s['coords'][1]} — {status}")

    conteudo = "\n".join(linhas) + "\n"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"simulacao_{timestamp}.txt"
    (LOG_DIR / nome_arquivo).write_text(conteudo, encoding="utf-8")
    (LOG_DIR / "ultima_simulacao.txt").write_text(conteudo, encoding="utf-8")
    return "ultima_simulacao.txt"


FRONTEND_BUILD = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # muda a cada restart do servidor


@app.route("/logs/<path:nome_arquivo>")
def servir_log(nome_arquivo):
    resp = send_from_directory(LOG_DIR.resolve(), nome_arquivo, mimetype="text/plain")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/")
def servir_frontend():
    # Cache-Control: no-store força o navegador a sempre buscar a versão mais
    # nova do HTML/JS no servidor, em vez de reaproveitar uma versão antiga
    # já salva localmente — depois de tantas rodadas de correção, isso evita
    # a dúvida "será que estou vendo o código atualizado?".
    html = Path("frontend/index.html").read_text(encoding="utf-8")
    html = html.replace("{{BUILD}}", FRONTEND_BUILD)
    resp = app.response_class(html, mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/build")
def build_info():
    """Consulta rápida (sem precisar rodar uma simulação) pra confirmar se o
    servidor está com o código mais recente: abra /build no navegador."""
    return jsonify({"build": FRONTEND_BUILD})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
