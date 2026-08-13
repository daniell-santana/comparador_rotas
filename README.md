# Otimização de Rotas: Solução de VRP (OR-Tools) vs Google Directions

![Python](https://img.shields.io/badge/Python-3.12-blue)
[![Deploy on Render](https://img.shields.io/badge/Deploy%20on%20Render-46E3B7?style=flat&logo=render&logoColor=white)](https://comparador-rotas.onrender.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
![Flask](https://img.shields.io/badge/Flask-3.x-black)
![OR--Tools](https://img.shields.io/badge/OR--Tools-VRP-orange)
![License](https://img.shields.io/badge/license-MIT-green)

Uma ferramenta analítica desenvolvida para comparar cenários de logística, confrontando o cálculo de rotas tradicionais ponto a ponto (Google Directions) com uma solução avançada de **otimização de rotas** baseada no problema de roteamento de veículos (VRP).

O objetivo principal é demonstrar de forma técnica e visual a redução de custos operacionais e a eficiência de quilometragem obtida através de algoritmos heurísticos.

---
## O que a aplicação faz

A aplicação resolve o **VRP (Vehicle Routing Problem)** simulando dois cenários distintos para a mesma lista de endereços:

### 1. Rota Convencional (Google Directions API)
* **Abordagem:** Utiliza o motor de mapas do Google para calcular trajetos ponto a ponto com alta precisão. Ele processa matrizes de distância e tempo levando em conta o trânsito em tempo real, restrições de tráfego e infraestrutura viária real.
* **Limitação:**  Ela calcula o melhor caminho para uma sequência predefinida, mas não decide estrategicamente qual veículo deve atender qual cliente quando há limites de capacidade e dezenas de destinos sobrepostos.

### 2. Rota Otimizada (OR-Tools)
* **Abordagem:**  Atua como a camada de inteligência matemática do projeto. Ele recebe as matrizes reais de distância e tempo geradas pelo Google Directions e aplica algoritmos avançados de otimização de restrições e meta-heurísticas.
* **Diferencial:** O OR-Tools rearranja globalmente toda a operação. Ele descobre a melhor combinação de paradas entre múltiplos veículos simultaneamente, garantindo o respeito estrito a janelas de horário e limites de capacidade de carga, reduzindo a quilometragem total que a API do Google roteará depois.

---

## Motor de roteirização

| Componente | O que faz |
|---|---|
| `routing_engine.resolver_vrp` | VRP com OR-Tools (capacidade + janela de horário + jornada máxima), 1–N veículos. Se as restrições forem inviáveis, relaxa automaticamente e sinaliza isso na resposta em vez de falhar. |
| `routing_engine.calcular_matrizes` | Distância real de rua entre todos os pontos (OSMnx + NetworkX shortest path), não linha reta. |
| `routing_engine.montar_geometria_rota` | Geometria real da via desenhada no mapa, com diagnóstico de circuidade e reparo automático via Google Directions para trechos sem curva salva no OpenStreetMap. |
| `google_tempo_real.matriz_tempo_google` | Tempo de viagem real (com trânsito) via Google Routes API, evita comparar tempo de dois modelos de velocidade diferentes. |
| `tsp_sa.resolver_sa` | Simulated Annealing (TSP puro, sem restrições), mantido como alternativa mais simples, para efeito de comparação metodológica. |

## Tecnologias Utilizadas

* **Python:** Linguagem base para o processamento de dados e backend.
* **Google OR-Tools:** Biblioteca de código aberto para a execução dos algoritmos de otimização de rotas.
* **Google Directions API:** Utilizada para obter as matrizes de distância, tempos de trânsito reais e plotagem visual dos caminhos.
* **Html:** Interface gráfica para exibição dos indicadores de performance (KPIs) e mapas comparativos.

---

## Indicadores Comparativos (KPIs)

O projeto extrai e compara as seguintes métricas após o processamento:
1. **Distância Total Percorrida (km):** O principal indicador de economia de combustível.
2. **Tempo Total de Viagem (horas):** Eficiência de tempo de jornada dos motoristas.
3. **Ocupação da Frota (%):** Métrica de eficiência no uso do espaço dos veículos.
---

## Rodando localmente

**Pré-requisitos:** Python 3.11+, uma chave de API do Google com
**Directions API** e **Routes API** habilitadas ([console.cloud.google.com](https://console.cloud.google.com)).

```bash
git clone https://github.com/daniell-santana/comparador_rotas.git
cd comparador_rotas
pip install -r requirements.txt
cp .env.example .env   # preencha GOOGLE_API_KEY
python app.py
```

Abra `http://localhost:5000`. Na primeira execução, o backend baixa o grafo
de ruas de São Paulo (pode levar alguns minutos); nas próximas, carrega do
cache em `cache/`.

## Usando a interface

1. Ajuste número de clientes, raio de dispersão, capacidade e jornada
   máxima no painel lateral, e escolha o algoritmo (VRP ou Simulated
   Annealing).
2. Clique em **"Gerar clientes e comparar"**. O mapa mostra as duas rotas
   (Google em vermelho, própria em azul), com setas de sentido e animação
   de percurso por rota.
3. Role até **"Mesmo carro, mesma hora de saída"** para o itinerário
   trecho a trecho, distância, tempo, espera (quando existe) e janela
   prometida a cada cliente.
4. Os gráficos e cards no topo resumem distância, tempo dirigindo e
   percentual de entregas dentro da janela.
5. Cada simulação gera um log em `logs/ultima_simulacao.txt`, cruzando a
   ordem calculada com a geometria desenhada, útil para auditar qualquer
   trecho que pareça estranho no mapa.

## Deploy em produção

Guia completo (Render.com) em [`deploy.md`](./deploy.md), incluindo como
dimensionar o grafo de ruas para caber no plano de hospedagem escolhido,
leia essa parte antes do primeiro deploy.

## 🗂️ Estrutura

```
app.py                  # Flask: orquestra clientes, rota Google, rota VRP, log de verificação
routing_engine.py       # Grafo OSMnx, matrizes de distância/tempo, solver VRP, geometria/reparo
google_tempo_real.py    # Tempo de viagem real via Google Routes API (computeRouteMatrix)
tsp_sa.py               # Simulated Annealing (TSP puro, alternativa mais simples)
dados_demo.py           # Geração de clientes simulados (posição, demanda, janela)
frontend/index.html     # Interface (Leaflet + Chart.js + painel de parâmetros)
scripts/warm_cache.py   # Aquece o cache do grafo no build (produção)
render.yaml             # Blueprint de deploy no Render
deploy.md               # Guia de deploy passo a passo
cache/                  # Grafo do OSM cacheado em disco (gerado automaticamente, não versionado)
logs/                   # Logs de verificação por simulação (gerado automaticamente, não versionado)
```

## Limitações conhecidas

- **Dados simulados**: clientes, demanda e janelas de horário são gerados
  aleatoriamente, não vêm de um sistema de pedidos real.
- **Heurística, não exata**: o solver de VRP não garante a rota
  matematicamente ótima, busca a melhor solução dentro de um tempo
  limitado (ver seção metodológica na interface).
- **Malha viária pesada**: o grafo é baixado sem simplificação (decisão
  deliberada para não desenhar retas onde não existe rua), o que aumenta o
  uso de memória, ver `deploy.md` antes de hospedar em plano com pouca RAM.
- **Custo de API**: Directions API e Routes API são pagas acima de uma
  cota gratuita mensal, não deixe a chave pública/sem limite de uso se for
  expor a aplicação publicamente.
