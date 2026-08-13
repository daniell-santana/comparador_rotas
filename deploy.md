# Deploy no Render

Este guia parte do zero: repositório local, GitHub, Render. Se o serviço já está no ar e quebrando, vá direto para a seção "Solução definitiva".

Passo a passo:

1. No seu `.env` local, defina as mesmas variáveis que o `render.yaml` usa:

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

   Isso baixa o grafo pela sua rede e salva em `cache/grafo_producao.graphml`. O script mostra o tamanho do arquivo e a memória RSS usada. Confira se o RSS fica abaixo de uns 350MB antes de seguir (o motivo desse número está na seção "Antes de fazer deploy").

3. Commite esse arquivo de propósito (o `.gitignore` já tem uma exceção só pra ele):

   ```bash
   git add cache/grafo_producao.graphml
   git add .
   git commit -m "Grafo de producao pre-gerado, sem depender do Overpass em runtime"
   git push
   ```

4. No dashboard do Render, confira em Environment se `GRAPH_CACHE_PATH` está definido como `cache/grafo_producao.graphml`. O `render.yaml` já vem assim, mas se você editou variáveis manualmente numa rodada anterior, elas não se atualizam sozinhas quando o `render.yaml` muda. Confira valor por valor, não assuma que está certo.

5. Manual Deploy, Clear build cache & deploy.

6. Nos logs do build, o esperado é `[routing_engine] Carregando grafo do cache: cache/grafo_producao.graphml`, sem nenhuma menção a "Baixando". Se aparecer "Baixando" mesmo assim, o arquivo não chegou no repositório (confira com `git log --stat`) ou o `GRAPH_CACHE_PATH` no dashboard não bate com o nome do arquivo commitado.

Um arquivo de uma área de uns 12km² simplificada fica na casa de poucos MB a algumas dezenas de MB, dentro do limite de 100MB por arquivo do GitHub. Se você aumentar bastante a bbox e passar disso, o Git recusa o push. Nesse caso, considere Git LFS ou reduza a área de novo.

## Se preferir não commitar o grafo: desbloqueio via variáveis

Sintomas possíveis:

- "Unexpected end of JSON input" no navegador, logs mostrando o processo reiniciando sem traceback. Falta de memória (o sistema mata o processo na hora, sem chance de logar nada).
- "Unexpected token '<'... is not valid JSON" no navegador, logs mostrando WORKER TIMEOUT preso em `time.sleep` na lógica de retry do Overpass. A API do Overpass não respondeu a tempo (esse sintoma some de vez com a solução acima, já que sem chamada ao Overpass em produção não tem como acontecer).

Correção gratuita, sem commitar o grafo:

0. Antes de mexer em qualquer variável, abra a aba Metrics do serviço no dashboard do Render. Ela mostra o uso de memória ao longo do tempo. Se a linha subir e encostar no teto bem na hora em que você tentou gerar uma rota, é confirmação direta de falta de memória.

1. Em Environment, confira ou adicione estas variáveis (os valores no `render.yaml` já vêm assim por padrão). Cobrem uma área pequena, menos de 1% do município, de propósito, porque as próprias bibliotecas do projeto (osmnx principalmente) já consomem uns 220MB de RAM antes de qualquer grafo:

   ```
   BBOX_NORTE=-23.535
   BBOX_SUL=-23.565
   BBOX_LESTE=-46.615
   BBOX_OESTE=-46.650
   GRAFO_SIMPLIFICAR=true
   ```

2. Force um novo deploy com Manual Deploy, Clear build cache & deploy. Um restart simples não basta, precisa rodar o build de novo.

3. Acompanhe os logs do build. Deve aparecer "Baixando grafo do OpenStreetMap para a bbox...", não "...inteiro". Se aparecer "inteiro" mesmo assim, as variáveis não foram salvas antes do deploy.


## Antes de fazer deploy: dimensione o grafo

Confirmado em produção, agosto de 2026: os planos Free e Starter do Render têm exatamente a mesma RAM, 512MB. A diferença entre eles é CPU e o Free dormir após 15 minutos sem uso. Só o plano Standard (2GB, uns $25/mês) dá mais memória de verdade, e é desproporcional pro tamanho desse projeto. Por isso a recomendação é reduzir o grafo, não pagar mais.

Achado medido localmente: as bibliotecas do motor de roteirização (numpy, networkx, osmnx, OR-Tools) consomem uns 220MB de RAM só sendo importadas, antes de qualquer grafo carregado. O osmnx sozinho responde por boa parte disso, porque traz geopandas, shapely e pyproj junto. Some uns 30 a 60MB de Flask e gunicorn, e a folga real pro grafo em si, dentro de 512MB, fica em torno de 200 a 250MB. Não dá pra reduzir isso sem tirar o osmnx do projeto, o que é inviável. O que dá pra controlar é o tamanho do grafo.

Por padrão o motor baixa o grafo sem simplificação, decisão de propósito pra nunca desenhar linha reta onde não existe rua. O efeito colateral é que o grafo fica maior em memória do que a versão simplificada padrão do osmnx. Baixar São Paulo inteira sem simplificar não cabe em 512MB.

Três variáveis reduzem o uso de memória, combináveis entre si:

| Variável | O que faz | Efeito na RAM |
|---|---|---|
| `BBOX_NORTE/SUL/LESTE/OESTE` | Baixa só um retângulo geográfico em vez do município inteiro | Grande, é a alavanca principal |
| `GRAFO_SIMPLIFICAR=true` | Funde vértices intermediários das vias | Médio |
| Reduzir raio de dispersão ou número de clientes na demo | Não afeta o grafo baixado, só a malha consultada por simulação | Pequeno, precisa ficar coerente com a bbox escolhida |

O `render.yaml` já vem com uma bbox pequena por padrão, ao redor do depósito da demo, menos de 1% do município. É um ponto de partida seguro. O raio de dispersão no formulário também foi reduzido (padrão 1,5km, máximo 3km) pra nenhum cliente cair fora dessa área. Se aumentar a bbox, pode aumentar esse máximo em `frontend/index.html`, no campo `id="raioKm"`.

### Protocolo pra crescer a área sem chutar

Não tente adivinhar um tamanho seguro e fazer deploy direto. Cada tentativa errada custa minutos de build. Faça assim:

1. Localmente, defina as variáveis BBOX (e GRAFO_SIMPLIFICAR se quiser) no `.env`.
2. Rode `python scripts/warm_cache.py`. Ele reporta o tamanho do arquivo em disco e a memória RSS real do processo depois de carregar o grafo, em Linux e Mac. No Windows, acompanhe pelo Gerenciador de Tarefas.
3. Se o RSS ficar abaixo de uns 350MB, essa bbox é segura pra deploy.
4. Pra uma área maior, aumente a bbox aos poucos e repita o passo 2. Nunca pule direto pra "cidade inteira" de novo.
5. Só depois de confirmar localmente, atualize as mesmas variáveis no dashboard do Render e faça Clear build cache & deploy.

Se quiser a malha viária inteira sem essa concessão, migre pro plano standard no `render.yaml` e remova as variáveis BBOX e GRAFO_SIMPLIFICAR.

Se um cliente simulado cair fora da bbox configurada, a rota pra ele falha. Prefira uma bbox um pouco folgada em vez de justa.

## 1. Preparar o repositório local

Se o projeto ainda não é um repositório Git local, no terminal do VS Code, na pasta do projeto:

```bash
cd caminho/para/comparador_rotas
git init
git branch -M main
```

Se já é um repositório Git, pule pra próxima seção.

## 2. Conectar ao GitHub

Conecte o remoto local ao repositório já criado:

```bash
git remote add origin https://github.com/daniell-santana/comparador_rotas.git
```

Se já existir um remote chamado origin apontando pra outro lugar:

```bash
git remote set-url origin https://github.com/daniell-santana/comparador_rotas.git
```

## 3. Conferir o que vai ser enviado

Antes do primeiro commit, confirme que segredos e arquivos gerados não vão junto:

```bash
git status
```

Você não deve ver `.env`, `cache/*.graphml` nem `logs/` na lista. Se aparecerem, confira se o `.gitignore` foi mesmo salvo na raiz do projeto.

## 4. Primeiro commit e push

```bash
git add .
git commit -m "Comparador de rotas: VRP proprio vs Google Directions"
git push -u origin main
```

Se o GitHub pedir autenticação, desde 2021 não aceita mais senha da conta por HTTPS, só token de acesso pessoal (Settings, Developer settings, Personal access tokens no github.com) ou login via GitHub CLI (`gh auth login`). O VS Code também autentica direto, se aparecer um popup pedindo login do GitHub, aceite.

## 5. Criar o serviço no Render

1. Entre em dashboard.render.com e faça login, dá pra usar a conta do GitHub direto.
2. Clique em New, Blueprint.
3. Selecione o repositório comparador_rotas. O Render lê o `render.yaml` sozinho e propõe o serviço já configurado.
4. Quando pedir o valor de `GOOGLE_API_KEY`, cole sua chave. Nunca cole a chave em nenhum arquivo do repositório.
5. Confirme e deixe o build rodar. Demora mais que o normal porque `warm_cache.py` processa o grafo nessa etapa.
6. Quando terminar, a URL fica algo como `https://comparador-rotas.onrender.com`.

## 6. Habilitar as APIs do Google

A chave `GOOGLE_API_KEY` precisa ter duas APIs habilitadas no mesmo projeto do Google Cloud Console (console.cloud.google.com, APIs e serviços, Biblioteca):

- Directions API, usada pra rota de referência do Google.
- Routes API, usada pra tempos reais de viagem e pro reparo automático de geometria. Sem ela habilitada, o app não quebra, mas cai pra estimativas próprias e avisa isso na interface.

As duas são pagas acima de uma cota gratuita mensal generosa. Confira os limites atuais em mapsplatform.google.com/pricing antes de deixar o app público.

## 7. Depois do primeiro deploy

Cada novo `git push` na branch main dispara um novo deploy automaticamente.

O grafo é rebaixado a cada novo deploy, a menos que você configure um disco persistente (ver comentário no `render.yaml`). Isso é esperado.

No plano free, o serviço dorme depois de uns 15 minutos sem requisições, e a próxima leva uns 60 segundos pra acordar. Normal, não é bug.

Pra ver logs em tempo real, aba Logs do serviço no dashboard do Render.

