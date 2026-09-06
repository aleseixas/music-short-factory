# Regra obrigatória — linguagem de alta retenção + ritmo visual contextual

Esta regra complementa `templates/editorial-direction-prompt.md` e deve ser aplicada na autoria de novos episódios enquanto a `main` continuar compatível com os contratos abaixo.

## 1) Objetivo editorial

O objetivo é maximizar retenção, curiosidade, comentários, compartilhamentos e reconhecimento imediato da música sem sacrificar factualidade.

O episódio deve parecer um Short/Reel/TikTok pensado por um editor agressivo em retenção: história forte, hook imediato, progressão constante e visual que realmente ajude a contar o que está sendo narrado.

Sensacionalismo é desejado na forma. A barreira é factual: não invente fatos, não distorça fonte, não transforme teoria em certeza e não prometa algo que o episódio não entrega.

## 2) Linguagem: 80% narrativa + 20% reação/conversa

Mire aproximadamente:

- 80% narrativa clara, eficiente e progressiva;
- 20% reação/conversa.

Use com naturalidade, sem quota mecânica, expressões como:

- `cara`;
- `olha isso`;
- `só que`;
- `e aí`;
- `o mais doido é que`;
- `pensa nisso`;
- `e aqui fica absurdo`;
- `detalhe`;
- `e não para por aí`.

O narrador deve soar como alguém muito envolvido contando para um amigo uma história musical absurda que acabou de descobrir.

Evite linguagem acadêmica, enciclopédica, institucional ou excessivamente neutra quando houver uma forma mais viva de dizer a mesma coisa.

### Hook

A primeira frase deve identificar música + artista e já abrir tensão, conflito, consequência, surpresa ou uma promessa forte.

Prefira premissa inesperada em vez de resumo seco.

Exemplo de direção:

`Beat It, do Michael Jackson, nasceu de uma missão meio absurda: colocar rock pesado dentro de Thriller sem deixar de soar como Michael Jackson.`

O hook deve criar a sensação de que existe uma segunda camada que será revelada nos próximos segundos.

### Desenvolvimento

Transforme informação em progressão narrativa:

`fato → detalhe inesperado → consequência → nova virada`.

Evite sequência de fatos independentes. Use conectores conversados quando ajudarem a empurrar a história adiante.

## 3) Primeiro visual — reconhecimento obrigatório

Nos primeiros **0,0–1,5 segundos**, o primeiro visual deve mostrar claramente uma destas opções:

1. **capa oficial do single, música ou álbum** relacionado ao episódio; ou
2. **artista principal/banda principal claramente reconhecível**.

Essa regra existe para o espectador entender instantaneamente sobre qual música/artista o vídeo fala.

Não abra com B-roll genérico, multidão, instrumento aleatório, rua, estúdio vazio, paisagem, texto abstrato ou outro visual que obrigue o espectador a esperar para reconhecer o assunto.

Quando houver uma boa capa oficial e uma boa imagem/vídeo do artista, escolha o que gerar reconhecimento mais imediato e combinar melhor com o hook.

## 4) Ritmo visual: agressivo, mas não aleatório

Para Shorts/Reels/TikTok na faixa de aproximadamente 60–90 segundos, mire normalmente **20–30 takes principais**. Em um episódio de ~72–82s, prefira normalmente **22–28 takes**, ajustando quando a duração real da narração e a qualidade dos visuais justificarem.

O objetivo é sensação constante de avanço visual, mas **contexto vale mais do que troca vazia de imagem**.

### HARD GATE — imagens estáticas obrigatoriamente entre 2 e 4 segundos

Esta é uma **regra obrigatória de pacing**, não uma recomendação. Ela prevalece sobre qualquer redação genérica ou antiga do projeto que diga `normalmente 2–4s`, que permita imagem estática longa por “força editorial” ou que trate 2–4s apenas como referência.

Para TODO shot cujo asset principal seja uma **imagem estática**:

- a duração final do shot deve ficar **entre 2,0s e 4,0s**;
- **nunca** mantenha uma imagem estática por mais de 4,0s;
- **nunca** use uma imagem estática por menos de 2,0s apenas para acelerar artificialmente o vídeo;
- zoom, crop, pan, `push_in`, `pull_out`, text FX, highlight, overlay, transição ou SFX sobre a mesma imagem **não reiniciam a contagem** e não autorizam ultrapassar 4,0s;
- se a fala associada a uma imagem ficaria acima de 4s, **divida a narração em segmentos menores antes de finalizar `story.json`/`timeline.json` e use outro visual principal no segmento seguinte**;
- no contrato atual `1 segment = 1 shot`, a segmentação do roteiro deve ser planejada para tornar essa regra possível; não aceite um segmento longo com imagem e espere que motion/FX resolvam o pacing;
- se houver dúvida entre prolongar a imagem e trocar para outro asset semanticamente correto, **troque a imagem**.

O primeiro visual continua precisando aparecer imediatamente nos primeiros 0,0–1,5s; se ele for imagem estática, pode começar em 0,0s e permanecer até completar a janela obrigatória de 2–4s.

### Vídeos — podem respirar mais

Vídeo **não usa o hard cap de 4s das imagens**. Pode permanecer por mais tempo quando houver movimento útil, contexto real e relação clara com a narração.

- 4–6s continua sendo uma faixa comum para vídeos fortes;
- vídeos podem passar de 6s quando a ação/performance/entrevista/bastidor/demonstração realmente sustentar o plano e a narração continuar semanticamente alinhada;
- não alongue vídeo só por ser vídeo;
- vídeo genérico, pouco relacionado ou semanticamente fraco deve ser curto, rebaixado ou substituído;
- um vídeo longo deve estar mostrando algo que vale acompanhar: performance específica, entrevista, bastidor, ação, reação, trecho de evento, demonstração, contexto histórico ou outra informação visual relevante.

Hooks, reveals, montagens, reações e viradas podem usar cortes mais rápidos quando isso aumentar retenção, mas **uma imagem estática individual continua sujeita ao mínimo obrigatório de 2,0s**.

`motion`, zoom, crop, speed, transição, text FX, highlight, overlay ou SFX sobre o mesmo asset NÃO contam como troca de take.

## 5) Pool visual: 100–120 candidatos e relevância temática obrigatória

Não pesquise apenas a quantidade de assets que entrará no render.

Para cada take/slot importante, mire em média **4–5 candidatos reais**, normalmente 3–6 conforme disponibilidade.

Para episódios com 20–30 takes finais:

- o **alvo editorial padrão é 100–120 candidatos visuais totais**;
- **80 candidatos é o mínimo aceitável** quando a disponibilidade real limitar a busca;
- em temas ricos visualmente, pode ultrapassar 120 quando isso melhorar de verdade a seleção;
- a seleção final só acontece depois de comparar candidatos do mesmo tipo por slot.

Não confunda `assets finais usados no render` com `candidatos pesquisados`.

### Regra crítica: o pool deve contar a história

O pool visual NÃO pode ser inflado com material genérico apenas para bater quantidade.

Cada candidato precisa ter relação clara com pelo menos um elemento relevante do episódio, como:

- a própria música;
- o artista ou banda;
- capa do single/álbum;
- videoclipe ou performance da música;
- pessoa citada na narração;
- produtor, compositor, músico ou colaborador citado;
- bastidor de gravação;
- entrevista relacionada;
- época ou evento específico;
- objeto, instrumento ou lugar citado;
- prêmio, chart, show, turnê ou acontecimento mencionado;
- conceito visual que represente diretamente o que está sendo explicado naquele take.

Antes de buscar cada slot, pergunte editorialmente:

**`O que está sendo dito neste exato momento e qual visual prova, mostra ou reforça essa ideia?`**

Só depois considere estética.

### Rebaixamento obrigatório de visuais sem contexto

Rebaixe fortemente candidatos que sejam:

- genéricos;
- abstratos;
- decorativos;
- apenas vagamente ligados ao artista;
- performances aleatórias que não ajudam a história;
- multidões/palcos/estúdios sem ligação com o trecho narrado;
- vídeos visualmente bonitos, mas semanticamente vazios;
- imagens de qualidade técnica alta que poderiam servir para qualquer música.

Em caso de dúvida, prefira **um asset mais contextual e menos bonito** a um asset mais bonito que não tenha conexão clara com a história.

Qualidade visual é importante, mas a ordem editorial é:

1. **relevância semântica para o take**;
2. **reconhecimento e clareza**;
3. **qualidade técnica/visual**;
4. **movimento/estética**.

O pool deve ser WEB-FIRST e seguir `docs/visual-search.md`.

Vídeo compete com vídeo e imagem compete com imagem. Reserve o vencedor de cada slot e faça deduplicação global antes da queue.

## 6) Proporção de vídeo e imagem

Como referência editorial, tente fechar aproximadamente **60–80% dos takes finais com vídeo** e **20–40% com imagem**, mas somente quando os vídeos realmente agregarem contexto.

Em ~24 takes, algo como **15–19 vídeos e 5–9 imagens** é uma referência, não hard gate.

Não escolha vídeo inferior apenas para cumprir proporção. Se imagens mais contextuais contarem melhor determinado trecho, use imagens.

Todo episódio deve ter pelo menos um take em que o artista principal seja claramente reconhecível — além da regra específica do primeiro visual.

Nunca reutilize a mesma imagem nem o mesmo vídeo-fonte em dois shots, mesmo com trim/crop/FX diferentes.

## 7) Relação com texto na tela

A maior densidade de cortes não autoriza poluição textual.

Siga `templates/caption-layout-rule.md`: legenda falada tem prioridade; text FX/highlight/overlay devem respeitar as zonas e sair do caminho quando disputarem leitura.

Não use texto editorial para compensar um visual fraco ou sem contexto.

## 8) Fechamento — CTA contextual sem end card visual

O fechamento segue `templates/end-card-cta-rule.md`, que apesar do nome histórico do arquivo agora define **CTA contextual sem end card visual**.

A arte `assets/branding/end_card_template.jpg` está **desativada para novos episódios** e não deve ser usada, copiada para a pasta do episódio nem substituída por outra arte genérica.

Fluxo esperado:

`hook reconhecível → história visual contextual → payoff → CTA contextual curto sobre o último visual da própria história`

O payoff deve vir antes do CTA.

O último visual deve continuar sendo um asset real e relevante do episódio — idealmente algo forte ligado à música, ao artista ou ao payoff. O CTA pode ser falado e, quando houver espaço visual claro, reforçado com `text_fx` curto.

Escolha apenas UMA ação principal no CTA, priorizando comentário ou compartilhamento. Não encerre com pedido triplo genérico.

## 9) Checklist editorial antes da queue

Antes de finalizar, confirme obrigatoriamente:

- primeiro visual = capa oficial ou artista principal claramente reconhecível;
- 20–30 takes quando a duração do episódio comportar esse ritmo;
- **toda imagem estática dura entre 2,0s e 4,0s, sem exceção editorial acima de 4s**;
- quando uma imagem exigiria >4s, o roteiro foi dividido em segmentos menores e houve troca real do visual principal;
- vídeos podem durar mais que imagens e podem ultrapassar ~6s somente quando movimento/contexto real sustentarem o plano;
- pool normalmente entre 100–120 candidatos;
- pool não foi inflado com visuais genéricos;
- cada slot possui candidatos relacionados ao que está sendo narrado;
- visuais genéricos/sem contexto foram rebaixados;
- maioria dos takes finais é vídeo quando houver vídeos contextuais suficientes;
- zero reuso de conteúdo visual;
- artista principal aparece claramente;
- legenda/texto editorial não competem;
- `assets/branding/end_card_template.jpg` NÃO foi usado;
- não foi criada end card genérica substituta;
- CTA final é contextual, curto e pede uma única ação;
- último visual continua pertencendo à história e reforça o encerramento.

## 10) HARD GATE técnico — duplicate preflight por GitHub Action

Antes da PRIMEIRA escrita em `episodes/<slug>/`, a candidata deve passar também pelo preflight técnico de duplicidade da `main`.

Fluxo obrigatório para o agente que opera via GitHub, sem terminal local:

1. depois da checagem determinística de `episodes/`, `.publish-queue/` e `.publish-retry/`, escolha uma candidata real e defina `song`, `artist` e `slug`;
2. crie exatamente UM arquivo novo `.duplicate-check/<slug>-<nonce>.json` com este formato:

```json
{
  "song": "Nome exato da música",
  "artist": "Nome do artista",
  "slug": "slug_normalizado"
}
```

3. essa escrita deve disparar `.github/workflows/duplicate-preflight.yml`;
4. localize a execução `Duplicate candidate preflight` associada ao commit que criou o arquivo e leia o job/log da Action;
5. só existem dois resultados editoriais válidos:
   - `PREFLIGHT_RESULT=UNIQUE_CANDIDATE`: a candidata passou; somente então a autoria em `episodes/<slug>/` pode começar;
   - `PREFLIGHT_RESULT=DUPLICATE_CANDIDATE`: descarte SOMENTE essa candidata e avance para a próxima música do pool, sem criar `episodes/<slug>/` nem `.publish-queue/<slug>.txt`;
6. se a Action falhar por infraestrutura, request malformado ou não produzir um dos dois markers acima, considere o preflight `BLOQUEADO` para aquela candidata e NÃO autorize a autoria com base em suposição;
7. um `GitHub.search` vazio, uma listagem aparentemente vazia ou ausência do slug exato NÃO substituem o preflight técnico;
8. o arquivo `.duplicate-check/*.json` é apenas um registro de consulta e NÃO conta como episódio criado nem como `.publish-queue`;
9. não reutilize o mesmo nome de request; use um `<nonce>` curto e único para cada candidata consultada;
10. HARD GATE: sem evidência real de `PREFLIGHT_RESULT=UNIQUE_CANDIDATE`, é proibido escrever qualquer arquivo em `episodes/<slug>/`.

Se o resultado for `DUPLICATE_CANDIDATE`, isso NÃO encerra a execução: continue para a próxima candidata do pool até encontrar uma inédita que passe pelos demais gates ou até esgotar o pool real.

## 11) HARD GATE técnico — media preflight antes da publish queue

Depois que o episódio estiver completamente autorado e imediatamente ANTES de criar `.publish-queue/<slug>.txt`, execute obrigatoriamente o preflight técnico real de mídia.

Fluxo obrigatório:

1. crie exatamente UM arquivo novo `.episode-check/<slug>-<nonce>.json` com:

```json
{
  "slug": "slug_normalizado"
}
```

2. essa escrita dispara `.github/workflows/episode-media-preflight.yml`;
3. localize a Action `Episode media preflight` associada ao commit exato do request e leia o job/log;
4. o preflight usa `check_episode_media.py`, que carrega o episódio com os parsers reais do engine, baixa/valida todos os assets com o mesmo `AssetManager` usado no render e resolve o background music com o mesmo `resolve_background_music` usado no pipeline;
5. só `MEDIA_PREFLIGHT_RESULT=PASS` autoriza criar `.publish-queue/<slug>.txt`;
6. HTTP 403, 404, 429, 5xx/525, payload inválido, imagem que o Pillow não reconhece, vídeo inválido no ffprobe, profile de música sem faixa resolvível ou qualquer outra falha de mídia = NÃO criar queue ainda;
7. quando o preflight falhar, corrija apenas os assets/background do mesmo episódio e rode um NOVO `.episode-check/<slug>-<nonce>.json`; isso não conta como queue nem retry de publicação;
8. nunca crie `.publish-queue` por suposição, mesmo que as URLs pareçam válidas no navegador;
9. após um PASS, não altere `assets.json`, `timeline.json` ou o background antes da queue; se alterar, rode o media preflight novamente;
10. objetivo: erros de download/mídia devem ser descobertos antes da primeira tentativa de publicação, preservando queue/retry para falhas reais posteriores.