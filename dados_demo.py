"""
Geração de clientes simulados para o modo demo: posição geográfica, demanda
de carga (kg) e janela de horário de entrega. Em uma versão com endereços
reais, este módulo seria substituído por geocodificação + integração com o
sistema de pedidos da empresa — mas o restante do pipeline (matrizes,
solver VRP, resposta da API) não muda.
"""
import random

EXPEDIENTE_INICIO_H = 8   # turno começa às 08:00
EXPEDIENTE_FIM_H = 18     # turno termina às 18:00
DEMANDA_MIN_KG = 10
DEMANDA_MAX_KG = 100
JANELA_MIN_H = 3          # duração mínima da janela de entrega prometida ao cliente
JANELA_MAX_H = 5

DISTANCIA_MINIMA_ENTRE_CLIENTES_KM = 0.25  # evita dois clientes caírem no
                                            # mesmo nó da malha viária (perna
                                            # de 0km, sem via real pra desenhar)


def _distancia_aproximada_km(a, b):
    """Distância aproximada (equiretangular, suficiente pra checar
    proximidade em curtas distâncias — não precisa da precisão de haversine
    aqui)."""
    dlat = (a[0] - b[0]) * 111.0
    dlng = (a[1] - b[1]) * 111.0 * 0.92  # ~cos(23.5°), compressão de longitude em SP
    return (dlat ** 2 + dlng ** 2) ** 0.5


def gerar_clientes(origem, n, raio_km=4.0):
    """Gera N clientes com posição aleatória em torno da origem, demanda de
    carga e uma janela de horário de entrega dentro do expediente. Garante
    distância mínima entre clientes (e do depósito) pra nenhum par cair no
    mesmo nó da malha viária."""
    raio_graus = raio_km / 111.0
    expediente_fim_s = (EXPEDIENTE_FIM_H - EXPEDIENTE_INICIO_H) * 3600

    pontos = []
    tentativas_max = 200
    for _ in range(n):
        candidato = None
        for _ in range(tentativas_max):
            lat = origem[0] + (random.random() - 0.5) * 2 * raio_graus
            lng = origem[1] + (random.random() - 0.5) * 2 * raio_graus
            candidato = (lat, lng)
            muito_perto = any(
                _distancia_aproximada_km(candidato, p) < DISTANCIA_MINIMA_ENTRE_CLIENTES_KM
                for p in pontos + [origem]
            )
            if not muito_perto:
                break
        pontos.append(candidato)

    clientes = []
    for i, (lat, lng) in enumerate(pontos):
        demanda = random.randint(DEMANDA_MIN_KG, DEMANDA_MAX_KG)
        duracao_janela_s = random.randint(JANELA_MIN_H, JANELA_MAX_H) * 3600
        inicio_max = max(expediente_fim_s - duracao_janela_s, 0)
        inicio = random.randint(0, inicio_max)
        fim = min(inicio + duracao_janela_s, expediente_fim_s)
        clientes.append({
            "id": i + 1,
            "lat": lat,
            "lng": lng,
            "demanda_kg": demanda,
            "janela_inicio_s": inicio,
            "janela_fim_s": fim,
        })
    return clientes


def formatar_hora(segundos):
    if segundos is None:
        return None
    total_min = round(segundos / 60)
    h = EXPEDIENTE_INICIO_H + total_min // 60
    m = total_min % 60
    return f"{int(h):02d}:{int(m):02d}"


def formatar_janela(inicio_s, fim_s):
    return f"{formatar_hora(inicio_s)} - {formatar_hora(fim_s)}"
