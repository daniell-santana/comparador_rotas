# Comparador de Rotas, VRP próprio vs Google Directions

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Flask](https://img.shields.io/badge/Flask-3.x-black)
![OR--Tools](https://img.shields.io/badge/OR--Tools-VRP-orange)
![License](https://img.shields.io/badge/license-MIT-green)

Aplicação web que resolve um problema de roteirização de veículos (VRP) com
restrições operacionais reais, capacidade de carga, janela de horário por
cliente, jornada máxima do motorista, e compara o resultado, lado a lado no
mapa e em km/tempo, com a rota que o Google Directions sugeriria para o
mesmo conjunto de paradas.

> **Por que isso importa:** o Google Directions resolve bem um problema
> específico (menor distância/tempo para visitar pontos com 1 veículo, sem
> restrições). Não é o problema que uma operação logística real precisa
> resolver. Este projeto reproduz, em escala pequena, o motor que empresas
> de logística de médio/grande porte constroem para isso, e explica, na
> própria interface, por que essa escolha de arquitetura existe.

## O que a aplicação faz

- Gera clientes simulados (posição, demanda de carga, janela de entrega) ao
  redor de um depósito em São Paulo.
- Resolve a rota com um solver de VRP próprio (OR-Tools: Guided Local
  Search sobre distância real de rua), respeitando capacidade, janela de
  horário e jornada máxima.
- Busca, para o mesmo conjunto de paradas, a rota que o Google Directions
  sugeriria (sem essas restrições) como referência.
- Mostra as duas rotas no mapa (Leaflet), com itinerário trecho a trecho,
  animação do percurso, gráficos comparativos e um log de verificação
  auditável para conferir se a geometria desenhada bate com a ordem
  calculada.

## Motor de roteirização

| Componente | O que faz |
|---|---|
| `routing_engine.resolver_vrp` | VRP com OR-Tools (capacidade + janela de horário + jornada máxima), 1–N veículos. Se as restrições forem inviáveis, relaxa automaticamente e sinaliza isso na resposta em vez de falhar. |
| `routing_engine.calcular_matrizes` | Distância real de rua entre todos os pontos (OSMnx + NetworkX shortest path), não linha reta. |
| `routing_engine.montar_geometria_rota` | Geometria real da via desenhada no mapa, com diagnóstico de circuidade e reparo automático via Google Directions para trechos sem curva salva no OpenStreetMap. |
| `google_tempo_real.matriz_tempo_google` | Tempo de viagem real (com trânsito) via Google Routes API, evita comparar tempo de dois modelos de velocidade diferentes. |
| `tsp_sa.resolver_sa` | Simulated Annealing (TSP puro, sem restrições), mantido como alternativa mais simples, para efeito de comparação metodológica. |

A seção **"ℹ️ Sobre este modelo"**, na própria interface, explica em
detalhe: por que o algoritmo é heurístico (não exato), por que uma
transportadora não usa só o Google Maps, e como julgar se o resultado é
bom (função objetivo, viabilidade, tempo de execução).

## ▶️ Rodando localmente

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

## ⚠️ Limitações conhecidas

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

## Licença

MIT, sinta-se livre para usar, estudar e adaptar este projeto.
