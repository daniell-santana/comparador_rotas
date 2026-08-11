# 🚀 Deploy no Render

Este guia parte do zero: repositório local → GitHub → Render. Se seu
serviço **já está no ar e quebrando**, pule direto pra seção
**"✅ Solução definitiva"** abaixo — ela resolve tanto o erro de memória
quanto o de timeout do Overpass, e é o caminho recomendado mesmo num
primeiro deploy.

## ✅ Solução definitiva: gere o grafo localmente e commite o resultado

Depois de duas rodadas de erro (falta de memória, depois timeout esperando
a API do Overpass responder), a causa comum é a mesma: **baixar o grafo de
dentro do Render é frágil** — RAM apertada e, principalmente, a API pública
do Overpass costuma ser mais lenta ou receber prioridade mais baixa vinda
de IP de provedor de nuvem do que da sua própria rede. Em vez de brigar com
isso a cada deploy, tire o Overpass da produção: gere o arquivo do grafo na
sua máquina (sua rede, sem essa limitação) e suba ele pronto junto com o
código. Em produção, o app só carrega um arquivo do disco — não baixa nada.

**Passo a passo:**

1. No seu `.env` local, defina as mesmas variáveis que o `render.yaml` usa
   (já vêm assim por padrão):

   ```
   BBOX_NORTE=-23.535
   BBOX_SUL=-23.565
   BBOX_LESTE=-46.615
   BBOX_OESTE=-46.650
   GRAFO_SIMPLIFICAR=true
   GRAPH_CACHE_PATH=cache/grafo_producao.graphml
   ```

2. Rode localmente:

   ```bash
   python scripts/warm_cache.py
   ```

   Isso baixa o grafo pela sua rede (deve funcionar sem o timeout que
   acontecia no Render) e salva em `cache/grafo_producao.graphml`. O script
   imprime o tamanho do arquivo e a memória RSS usada — confira se o RSS
   fica confortavelmente abaixo de ~350MB antes de seguir (ver seção
   "Antes de fazer deploy" abaixo para o porquê desse número).

3. Commite esse arquivo **de propósito** (o `.gitignore` já tem uma exceção
   só pra ele):

   ```bash
   git add cache/grafo_producao.graphml
   git add .
   git commit -m "Grafo de producao pre-gerado, sem depender do Overpass em runtime"
   git push
   ```

4. No dashboard do Render, confirme em **Environment** que `GRAPH_CACHE_PATH`
   está definido como `cache/grafo_producao.graphml` (já vem assim no
   `render.yaml`, mas — igual aconteceu com a bbox antes — se você editou
   variáveis manualmente no dashboard em rodadas anteriores, elas não se
   atualizam sozinhas quando o `render.yaml` muda. Confira os valores atuais
   um por um, não assuma que estão certos).

5. **Manual Deploy → Clear build cache & deploy.**

6. Nos logs do build, você deve ver `[routing_engine] Carregando grafo do
   cache: cache/grafo_producao.graphml` — sem nenhuma menção a "Baixando".
   Se aparecer "Baixando" mesmo assim, o arquivo não chegou no repositório
   (confira com `git log --stat` se o `.graphml` aparece no commit) ou o
   `GRAPH_CACHE_PATH` no dashboard não bate com o nome do arquivo commitado.

O arquivo `.graphml` de uma área de ~12km² simplificada deve ficar na casa
de poucos MB a algumas dezenas de MB — dentro do limite de 100MB por
arquivo do GitHub sem configuração extra. Se você aumentar bastante a bbox
e o arquivo passar disso, o Git vai recusar o push; nesse caso, considere
Git LFS ou volte a reduzir a área.

## 🆘 Se preferir não commitar o grafo: desbloqueio via variáveis

**Sintomas possíveis:**
- `Failed to execute 'json' on 'Response': Unexpected end of JSON input` +
  logs mostrando o processo reiniciando sem traceback → falta de memória
  (OOM kill — o sistema mata o processo na hora, sem chance de logar nada).
- `Unexpected token '<', "<html> <"... is not valid JSON` + `WORKER TIMEOUT`
  nos logs, preso dentro de `time.sleep` na lógica de retry do Overpass →
  a API do Overpass não respondeu a tempo (esse é o sintoma que a solução
  acima, commitando o grafo, elimina de vez — sem chamada ao Overpass em
  produção, não tem como esse timeout específico acontecer de novo).

**Correção (gratuita, sem commitar o grafo):**

0. Antes de mexer em qualquer variável: abra a aba **Metrics** do seu
   serviço no dashboard do Render. Ela mostra um gráfico de uso de memória
   ao longo do tempo — se você ver a linha subir e encostar no teto bem na
   hora em que tentou gerar uma rota, é confirmação direta de OOM, sem
   precisar inferir pelos logs.

1. No dashboard do Render, vá em **Environment** do seu serviço e adicione
   estas 6 variáveis (os valores no `render.yaml` já vêm assim por padrão
   se você ainda não tiver feito o primeiro deploy). Esses valores cobrem
   uma área pequena (~12 km², menos de 1% do município) de propósito — as
   próprias bibliotecas do projeto (OSMnx principalmente) já consomem
   ~220MB de RAM antes de qualquer grafo, então a folga real dentro de
   512MB é menor do que parece:

   ```
   BBOX_NORTE=-23.535
   BBOX_SUL=-23.565
   BBOX_LESTE=-46.615
   BBOX_OESTE=-46.650
   GRAFO_SIMPLIFICAR=true
   ```

2. Force um novo deploy (**Manual Deploy → Clear build cache & deploy**,
   não só um restart — precisa rodar o build de novo pra baixar o grafo já
   no tamanho reduzido).

3. Acompanhe os logs do build: deve aparecer `[routing_engine] Baixando
   grafo do OpenStreetMap para a bbox...` em vez de "...inteiro". Se
   aparecer "inteiro" mesmo assim, as variáveis não foram salvas antes do
   deploy — confira de novo em Environment.

**Não adianta trocar de plano gratuito pra Starter só por causa disso** —
ver por quê na seção abaixo.

## 0. Antes de fazer deploy: dimensione o grafo

⚠️ **Confirmado em produção (agosto/2026):** os planos **Free e Starter do
Render têm exatamente a mesma RAM: 512MB.** A diferença entre eles é só
CPU (0.1 vs 0.5 vCPU) e o Free dormir após 15min sem uso. Só o plano
**Standard (2GB, ~$25/mês)** dá mais memória de verdade — e é
desproporcional pro tamanho desse projeto. Por isso a recomendação aqui é
reduzir o grafo, não pagar mais.

⚠️ **Achado importante, medido localmente:** as bibliotecas do motor de
roteirização (numpy, networkx, OSMnx, OR-Tools) consomem **~220MB de RAM só
sendo importadas**, antes de qualquer grafo ser carregado — o OSMnx sozinho
responde por ~156MB disso (ele traz geopandas/shapely/pyproj junto). Some
uns 30-60MB de Flask/gunicorn, e a folga real pro grafo em si, dentro de
512MB, fica em torno de 200-250MB — bem menos do que os "512MB disponíveis"
sugerem à primeira vista. Isso não dá pra reduzir sem tirar o OSMnx do
projeto (inviável, é o coração do motor); o que dá pra controlar é o
tamanho do grafo.

O motor de roteirização, por padrão, baixa o grafo de ruas **sem
simplificação** (decisão deliberada pra nunca desenhar linha reta onde não
existe rua — ver README.md). O efeito colateral é que o grafo fica bem
maior em memória do que a versão simplificada padrão do OSMnx. Baixar
**"São Paulo, Brazil" inteira** (1.521 km²) sem simplificar não cabe em
512MB de jeito nenhum — foi isso que causou o erro OOM original.

Você tem três variáveis pra reduzir o uso de memória, combináveis entre si:

| Variável | O que faz | Efeito na RAM |
|---|---|---|
| `BBOX_NORTE/SUL/LESTE/OESTE` | Baixa só um retângulo geográfico em vez do município inteiro | Grande — é a alavanca principal |
| `GRAFO_SIMPLIFICAR=true` | Funde vértices intermediários das vias (comportamento padrão do OSMnx) | Médio |
| Reduzir `raio_km`/nº de clientes no uso da demo | Não afeta o grafo baixado, só a malha efetivamente consultada por simulação | Pequeno — não resolve sozinho, mas precisa ficar coerente com a bbox escolhida (ver abaixo) |

**`render.yaml` já vem com uma bbox bem pequena por padrão** (~3,6km x
3,3km ao redor do depósito da demo, menos de 1% do município), como ponto
de partida seguro. O campo "Raio de dispersão" do formulário também foi
reduzido (padrão 1,5km, máximo 3km) pra nenhum cliente simulado cair fora
dessa área — se você aumentar a bbox, pode voltar a aumentar esse máximo em
`frontend/index.html` (`id="raioKm"`).

### Protocolo pra crescer a área sem chutar

Não tente adivinhar um tamanho "seguro" e fazer deploy direto — cada
tentativa errada custa minutos de build. Faça assim:

1. Localmente, defina as variáveis `BBOX_*` (e `GRAFO_SIMPLIFICAR` se
   quiser) no seu `.env`.
2. Rode `python scripts/warm_cache.py`. Ele agora reporta o tamanho do
   arquivo em disco **e a memória RSS real do processo** depois de carregar
   o grafo (em Linux/Mac; no Windows, acompanhe pelo Gerenciador de
   Tarefas).
3. Se o RSS reportado ficar confortavelmente abaixo de ~350MB (deixando
   margem pra Flask/gunicorn/processamento de cada requisição dentro dos
   512MB totais), essa bbox é segura pra deploy.
4. Se quiser uma área maior, aumente a bbox aos poucos e repita o passo 2 —
   nunca pule direto pra "cidade inteira" de novo.
5. Só depois de confirmar localmente, atualize as mesmas variáveis no
   dashboard do Render (Environment) e faça **Clear build cache & deploy**.

Se depois quiser a malha viária inteira sem essa concessão, migre pro plano
`standard` no `render.yaml` e remova as 5 variáveis (`BBOX_*` e
`GRAFO_SIMPLIFICAR`).

Se um cliente simulado cair fora da bbox configurada, a rota pra ele vai
falhar (nó mais próximo pode ficar muito longe ou inexistente) — prefira
uma bbox um pouco folgada em vez de justa.

**Teste local antes de decidir**: rode `python scripts/warm_cache.py`
localmente com e sem essas variáveis definidas, e compare o tamanho do
arquivo gerado em `cache/` e o tempo que levou. Isso te dá uma noção real
de RAM/tempo antes de gastar cota de build no Render.

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
| Navegador mostra "Unexpected end of JSON input" ao gerar rota, logs mostram processo reiniciando sem traceback | OOM kill em runtime — o build passou, mas carregar o grafo em memória na hora do `/comparar` estourou os 512MB | Ver seção "✅ Solução definitiva" no topo deste arquivo |
| Navegador mostra "Unexpected token '<'... is not valid JSON", logs mostram `WORKER TIMEOUT` preso em `time.sleep` dentro de `_overpass_request` | A API do Overpass não respondeu a tempo de dentro do Render | Ver seção "✅ Solução definitiva" — commitar o grafo elimina essa dependência por completo |
| Build falha com `MemoryError` ou processo morto durante o `buildCommand` | Grafo grande demais até pro ambiente de build | Configure `BBOX_*`/`GRAFO_SIMPLIFICAR` antes do build, não só em runtime |
| Build demora e falha por timeout | Download do OSM lento nesse horário/rede | Considere commitar o grafo pronto (ver topo do arquivo) em vez de depender do download em toda build |
| Primeiro `/comparar` dá erro 502/504 (sem ser os dois erros acima) | `warm_cache.py` não rodou ou falhou silenciosamente no build | Confira os logs do build; teste `python scripts/warm_cache.py` localmente primeiro |
| App sobe mas rota do Google falha | `GOOGLE_API_KEY` não configurada ou Directions API não habilitada | Settings → Environment no dashboard do Render |
| Tempo "estimativa própria" sempre aparece | Routes API não habilitada nessa chave | Habilitar em console.cloud.google.com |
| Trocar variáveis no `render.yaml` não parece ter efeito | Variáveis já existentes no dashboard (setadas manualmente em rodada anterior) não são sobrescritas automaticamente por mudanças no `render.yaml` | Confira e edite os valores diretamente em Environment no dashboard, um por um |
