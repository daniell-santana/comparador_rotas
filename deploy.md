# 🚀 Deploy no Render

Este guia parte do zero: repositório local → GitHub → Render. Leia a seção
**"Antes de fazer deploy"** antes de tudo — ela evita o erro mais provável
(o serviço morrer por falta de memória).

## 0. Antes de fazer deploy: dimensione o grafo

O motor de roteirização baixa o grafo de ruas **sem simplificação**
(`simplify=False`) — decisão deliberada para nunca desenhar linha reta onde
não existe rua (ver README.md). O efeito colateral é que o grafo fica bem
maior em memória do que a versão simplificada padrão do OSMnx, e baixar
**"São Paulo, Brazil" inteira** (1.521 km²) sem simplificar pode:

- Levar vários minutos pra baixar/processar;
- Consumir mais RAM do que um plano gratuito de hospedagem costuma oferecer.

Você tem três caminhos, do mais simples ao mais trabalhoso:

| Caminho | O que fazer | Quando escolher |
|---|---|---|
| **A. Plano com mais RAM** | Deixe `OSM_PLACE` como está, use `plan: starter` (já é o padrão no `render.yaml`) ou superior | Quer a malha viária inteira da cidade, sem restrição de área |
| **B. Restringir a área (bbox)** | Configure `BBOX_NORTE/SUL/LESTE/OESTE` (ver abaixo) pra baixar só a região onde seus clientes de demo aparecem | Quer ficar num plano mais barato/gratuito |
| **C. Voltar a simplificar** | Trocar `simplify=False` por `simplify=True` em `routing_engine.py` (aceita o risco residual de retas — o reparo automático via Google ainda cobre a maioria dos casos) | Prioriza custo baixo acima de tudo |

**Recomendo o caminho B** pra manter a correção de geometria e ainda caber
num plano enxuto. Exemplo cobrindo a área usada nos seus testes (centro
expandido de SP, uns 15km de raio):

```
BBOX_NORTE=-23.45
BBOX_SUL=-23.68
BBOX_LESTE=-46.50
BBOX_OESTE=-46.75
```

Ajuste esses 4 números pra cobrir a área que seu depósito + raio de
dispersão realmente usam. Se um cliente cair fora da bbox, a rota pra ele
vai falhar (nó mais próximo pode ficar muito longe ou inexistente) —
prefira uma bbox folgada.

**Teste local antes de decidir**: rode `python scripts/warm_cache.py`
localmente com e sem as variáveis `BBOX_*` definidas, e compare o tamanho do
arquivo gerado em `cache/` e o tempo que levou. Isso te dá uma noção real de
RAM/tempo antes de gastar cota de build no Render.

## 1. Preparar o repositório local

Se este projeto ainda não é um repositório Git local (rode no terminal do
VS Code, na pasta do projeto):

```bash
cd caminho/para/comparador_rotas
git init
git branch -M main
```

Se já é um repositório Git (por exemplo, se você já rodou `git init` antes),
pule pra próxima seção.

## 2. Conectar ao GitHub

Você já tem o repositório vazio criado em
`https://github.com/daniell-santana/comparador_rotas.git`. Conecte o
remoto local a ele:

```bash
git remote add origin https://github.com/daniell-santana/comparador_rotas.git
```

Se já existir um remote chamado `origin` apontando pra outro lugar:

```bash
git remote set-url origin https://github.com/daniell-santana/comparador_rotas.git
```

## 3. Conferir o que vai ser enviado

**Antes do primeiro commit**, confirme que segredos e arquivos gerados não
vão junto (o `.gitignore` já cobre isso, mas vale conferir):

```bash
git status
```

Você **não** deve ver `.env`, `cache/*.graphml`, nem `logs/` na lista de
arquivos a serem adicionados. Se aparecerem, confira se o `.gitignore` foi
mesmo salvo na raiz do projeto.

## 4. Primeiro commit e push

```bash
git add .
git commit -m "Comparador de rotas: VRP proprio vs Google Directions"
git push -u origin main
```

Se o GitHub pedir autenticação: desde 2021 ele não aceita mais senha da
conta por HTTPS, só **token de acesso pessoal** (Settings → Developer
settings → Personal access tokens, no github.com) ou login via GitHub CLI
(`gh auth login`). O VS Code também tem integração nativa: se aparecer um
popup pedindo pra autenticar com o GitHub, aceite — ele cuida do token pra
você.

## 5. Criar o serviço no Render

1. Entre em [dashboard.render.com](https://dashboard.render.com) e faça
   login (dá pra usar a conta do GitHub direto).
2. Clique em **New** → **Blueprint**.
3. Selecione o repositório `comparador_rotas`. O Render vai ler o
   `render.yaml` automaticamente e mostrar o serviço `comparador-rotas`
   configurado.
4. Quando pedir o valor de `GOOGLE_API_KEY` (por causa do `sync: false` no
   blueprint), cole sua chave. **Nunca** cole a chave em nenhum arquivo do
   repositório.
5. Confirme e deixe o build rodar. Ele demora mais que o normal porque
   `warm_cache.py` baixa e processa o grafo de ruas nessa etapa — normal
   levar alguns minutos.
6. Quando o deploy terminar, sua URL será algo como
   `https://comparador-rotas.onrender.com`.

## 6. Habilitar as APIs do Google

Sua chave `GOOGLE_API_KEY` precisa ter **duas** APIs habilitadas no mesmo
projeto do Google Cloud Console (console.cloud.google.com → APIs e
serviços → Biblioteca):

- **Directions API** — usada pra rota de referência do Google.
- **Routes API** — usada pra tempos reais de viagem (`computeRouteMatrix`)
  e pro reparo automático de geometria. Sem ela habilitada, o app não
  quebra, mas cai pra estimativas próprias e avisa isso na interface.

Ambas são pagas acima de uma cota gratuita mensal generosa — confira os
limites atuais em [cloud.google.com/pricing](https://mapsplatform.google.com/pricing/)
antes de deixar o app público, pra não ter surpresa de custo se muita gente
usar.

## 7. Depois do primeiro deploy

- **Cada novo `git push` na branch `main` dispara um novo deploy
  automaticamente** (comportamento padrão do Render pra serviços conectados
  a um repositório Git).
- O grafo é rebaixado a cada novo deploy (a menos que você configure um
  disco persistente — ver comentário no `render.yaml`). Isso é esperado e
  faz parte do trade-off do caminho B/A escolhido no passo 0.
- Se o plano for `free`, o serviço "dorme" depois de ~15 minutos sem
  requisições, e a próxima leva ~1 minuto pra acordar — normal, não é bug.
- Para ver logs em tempo real (útil pra acompanhar os `[app] SALTO...` e
  `[routing_engine]...` que aparecem no terminal local): aba **Logs** do
  serviço no dashboard do Render.

## Troubleshooting

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Build falha com `MemoryError` ou processo morto | Grafo grande demais pro plano | Volte ao passo 0, use bbox ou suba de plano |
| Build demora e falha por timeout | Download do OSM lento nesse horário/rede | Rode de novo (Overpass API às vezes está sobrecarregada); considere bbox menor |
| Primeiro `/comparar` dá erro 502/504 | `warm_cache.py` não rodou ou falhou silenciosamente no build | Confira os logs do build; teste `python scripts/warm_cache.py` localmente primeiro |
| App sobe mas rota do Google falha | `GOOGLE_API_KEY` não configurada ou Directions API não habilitada | Settings → Environment no dashboard do Render |
| Tempo "estimativa própria" sempre aparece | Routes API não habilitada nessa chave | Habilitar em console.cloud.google.com |
