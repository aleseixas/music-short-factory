# Regra obrigatória — linguagem de alta retenção + ritmo visual contextual

<!-- pipeline-contract: config/pipeline-contract.json -->

Contrato técnico obrigatório: [`docs/pipeline-contract.md`](../docs/pipeline-contract.md), baseado em [`config/pipeline-contract.json`](../config/pipeline-contract.json). Use o estado persistido e os triggers reais antes de decidir continuidade.

Esta regra complementa `templates/editorial-direction-prompt.md`. Continuidade,
limites e execução seguem [docs/automation-protocol.md](../docs/automation-protocol.md)
e os prompts canônicos de creator/recovery. Código e schemas atuais da main
definem os contratos técnicos; não confunda orientação editorial com autoridade.

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

## 3) Primeiro visual — VÍDEO obrigatório + reconhecimento imediato

O primeiro take editorial da história, imediatamente depois da capa técnica de ~0,30s, deve ser **VÍDEO REAL**. Imagem estática, capa de álbum, foto, arte ou frame congelado NÃO podem ser o primeiro take editorial.

Nos primeiros **0,0–1,5 segundos editoriais**, esse vídeo de abertura deve mostrar claramente o **artista principal/banda principal** ou o acontecimento central do hook de forma imediatamente reconhecível. Quando o tema for um evento/show específico, prefira vídeo do próprio evento; quando isso não existir, use vídeo direto e forte do artista, não B-roll genérico.

Essa regra existe para o espectador receber movimento + reconhecimento instantâneo antes de qualquer sequência de imagens.

Não abra com foto, capa estática, B-roll genérico, multidão sem contexto, instrumento aleatório, rua, estúdio vazio, paisagem, texto abstrato ou outro visual que obrigue o espectador a esperar para reconhecer o assunto.

A capa oficial do single/álbum continua podendo aparecer depois da abertura quando ajudar a história, mas **não substitui o vídeo obrigatório do primeiro take editorial**.

O `visual_candidates.json` do slot inicial precisa conter candidatos reais de vídeo suficientes para que o resolver tenha alternativa. Se o primeiro slot não tiver nenhum candidato de vídeo, a autoria está incompleta e não deve seguir para queue.

### CAPA / THUMBNAIL — deve vir do começo do próprio vídeo

A capa de publicação deve ser **derivada de um frame do começo do vídeo renderizado**, e não escolhida como uma imagem externa desconectada da abertura.

Use preferencialmente em `post.json`:

```json
"cover": {
  "source": {
    "type": "video_frame",
    "timestamp_seconds": 0.7
  }
}
```

O timestamp pode variar dentro do começo do vídeo para pegar um frame utilizável, mas deve continuar muito próximo da abertura — normalmente algo em torno de **0,3–1,5s**. A escolha do frame pode ser tratada pelo código/pipeline; não altere a estrutura editorial do primeiro take só para fabricar uma thumbnail.

Não pule para uma cena distante do vídeo só para conseguir uma capa mais bonita. A capa deve parecer um print natural da abertura.

O pipeline atual já gera essa capa e a **insere automaticamente por aproximadamente 0,30s no início do MP4 final** antes do conteúdo normal. Portanto:

- não crie um shot extra na `timeline.json` apenas para simular a capa;
- a capa técnica inserida pelo pipeline é separada dos shots editoriais;
- a exigência de vídeo no primeiro take editorial existe independentemente da capa técnica;
- depois desses ~0,30s, a montagem começa obrigatoriamente com o primeiro shot editorial em vídeo;
- a mesma capa gerada é usada como thumbnail quando a plataforma/API suportar.

O headline da capa continua vindo de `post.json`, mas o **fundo visual deve ser o frame inicial do próprio vídeo** sempre que o fluxo suportar `cover.source.type = video_frame`.

## 4) Ritmo visual: agressivo, mas não aleatório

Para Shorts/Reels/TikTok na faixa de aproximadamente 60–90 segundos, mire normalmente **20–30 takes principais**. Em um episódio de ~72–82s, prefira normalmente **22–28 takes**, ajustando quando a duração real da narração e a qualidade dos visuais justificarem.

O objetivo é sensação constante de avanço visual, mas **contexto vale mais do que troca vazia de imagem**.

Para novos episódios, use `"smart_visual_pacing": {"enabled": true}` no topo de
`timeline.json` quando quiser o ajuste conservador de fronteiras antes do render.
O recurso preserva assets, ordem, áudio e duração total; ele não substitui o
planejamento de segmentos/shots nem autoriza depender do pipeline para corrigir
uma montagem semanticamente fraca. Campo ausente ou desativado mantém o pacing
legado.

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

Imagens estáticas podem entrar normalmente depois do primeiro take editorial, respeitando a janela obrigatória de 2–4s. O primeiro take editorial é exceção por direção oposta: ele precisa ser vídeo real.

**Exceção técnica:** a capa/thumbnail inserida automaticamente por `engine/cover_intro.py` por aproximadamente 0,30s no começo do MP4 **não é um shot da timeline** e não está sujeita ao mínimo de 2,0s das imagens editoriais.

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

`visual_candidates.json` é **obrigatório** para novos episódios. Não ter pool visual não é um fallback válido e não autoriza manter silenciosamente apenas os assets-base. O media preflight deve bloquear a queue com `VISUAL_CANDIDATE_POOL_MISSING` ou `VISUAL_CANDIDATE_POOL_INVALID` quando esse contrato não for cumprido.

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

Como referência editorial, mire aproximadamente **60% dos takes finais com vídeo**
e **40% com imagem**, com seleção separada por tipo e coerência semântica.
Em ~24 takes, cerca de 14–15 vídeos e 9–10 imagens são referência, não hard gate.

Não escolha vídeo inferior apenas para cumprir proporção. Se imagens mais contextuais contarem melhor determinado trecho, use imagens.

O **primeiro take editorial é sempre vídeo**, independentemente dessa proporção global. Todo episódio deve ter pelo menos um take em que o artista principal seja claramente reconhecível — idealmente já na abertura.

Nunca reutilize a mesma imagem nem o mesmo vídeo-fonte em dois shots, mesmo com trim/crop/FX diferentes.

## 7) Relação com texto na tela

A maior densidade de cortes não autoriza poluição textual.

Siga `templates/caption-layout-rule.md`: legenda falada tem prioridade; text FX/highlight/overlay devem respeitar as zonas e sair do caminho quando disputarem leitura.

Não use texto editorial para compensar um visual fraco ou sem contexto.

## 8) Fechamento — CTA contextual sem end card visual

O fechamento segue `templates/end-card-cta-rule.md`, que apesar do nome histórico do arquivo agora define **CTA contextual sem end card visual**.

A arte `assets/branding/end_card_template.jpg` está **desativada para novos episódios** e não deve ser usada, copiada para a pasta do episódio nem substituída por outra arte genérica.

Fluxo esperado:

`hook reconhecível em vídeo → história visual contextual → payoff → CTA contextual curto sobre o último visual da própria história`

O payoff deve vir antes do CTA.

O último visual deve continuar sendo um asset real e relevante do episódio — idealmente algo forte ligado à música, ao artista ou ao payoff. O CTA pode ser falado e, quando houver espaço visual claro, reforçado com `text_fx` curto.

Escolha apenas UMA ação principal no CTA, priorizando comentário ou compartilhamento. Não encerre com pedido triplo genérico.

## 9) Checklist editorial antes da queue

Antes de finalizar, confirme obrigatoriamente:

- **primeiro take editorial depois da capa técnica = VÍDEO REAL**;
- vídeo inicial mostra artista/banda principal ou o acontecimento central do hook com reconhecimento imediato;
- slot inicial do `visual_candidates.json` possui candidatos reais de vídeo;
- `visual_candidates.json` existe, é válido e foi realmente usado na seleção visual;
- **capa/thumbnail usa `cover.source.type = video_frame` e vem de um frame do começo do próprio vídeo**;
- **a capa técnica será inserida automaticamente por ~0,30s no começo do MP4; não foi criado shot extra só para isso**;
- 20–30 takes quando a duração do episódio comportar esse ritmo;
- **toda imagem estática de timeline dura entre 2,0s e 4,0s, sem exceção editorial acima de 4s**;
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

Entrada do duplicate via GitHub; a continuação em autoria/prepare exige clone
autenticado e mecanismos coordenados, conforme o protocolo comum:

1. consulte `pipeline_control.py status --channel default`, authority/CAS e slot;
   só com canal livre/slot elegível escolha candidata inédita e defina song/artist/slug;
2. envie exatamente UM request novo `.duplicate-check/<slug>--<request_id>.json`
   em commit próprio. O guard reserva/conta por CAS antes de decidir duplicidade:

```json
{
  "song": "Nome exato da música",
  "artist": "Nome do artista",
  "slug": "slug_normalizado",
  "request_id": "pedido_unico"
}
```

3. o commit/push desse request dispara `.github/workflows/duplicate-preflight.yml`; `workflow_dispatch` não é necessário;
4. localize `Duplicate candidate preflight` pelo request_id, slug, commit SHA e workflow; leia o resultado estruturado da Action;
5. resultados editoriais e decisões de coordenação são distintos:
   - `PREFLIGHT_RESULT=UNIQUE_CANDIDATE`: a candidata passou; somente então a autoria em `episodes/<slug>/` pode começar;
   - `PREFLIGHT_RESULT=DUPLICATE_CANDIDATE`: descarte só essa candidata e avance após fechamento CAS/canal livre, sem autoria/queue;
   - `RESUME_EXISTING_EPISODE`: siga o slug ativo;
   - `CONTINUITY_CONFLICT`: reconcilie identidades, sem forçar reserva;
   - `CANDIDATE_LIMIT_REACHED`: respeite a janela compartilhada, sem reset manual;
6. corrija requests malformados e investigue falhas concretas; ausência temporária de run/marker exige polling com backoff. Não autorize autoria por suposição nem bloqueie por status transitório;
7. um `GitHub.search` vazio, uma listagem aparentemente vazia ou ausência do slug exato NÃO substituem o preflight técnico;
8. o arquivo `.duplicate-check/*.json` é apenas um registro de consulta e NÃO conta como episódio criado nem como `.publish-queue`;
9. não reutilize request de candidata diferente; use identidade única e preserve a da reserva em andamento;
10. HARD GATE: sem evidência real de `PREFLIGHT_RESULT=UNIQUE_CANDIDATE`, é proibido escrever qualquer arquivo em `episodes/<slug>/`.

Se o resultado for `DUPLICATE_CANDIDATE`, isso NÃO encerra a execução: continue para a próxima candidata do pool até encontrar uma inédita que passe pelos demais gates ou até esgotar o pool real.

## 11) HARD GATE técnico — validação local antes do request externo

Siga o [protocolo comum](../docs/automation-protocol.md). Autoria coordenada e
prepare exigem clone autenticado. Depois da autoria, execute
`python scripts/pipeline_control.py prepare <slug> --request-id <id>`.
Somente PASS confirma request/metadata/bytes pelo CAS e retorna commit_sha;
não crie .episode-check manual nem faça segundo commit/push do request.
Acompanhe request_id + slug + SHA devolvido + workflow com polling/backoff.
Run transitório/timeout preserva identidade e não autoriza rerun.

Preflight faz validação batch, resolução, render, capa, dry-run e bundle.
O job sela queue por CAS com slug/source_run_id/request_id. QUEUED já bloqueia
mutação; observe/reconcilie o receipt de dispatch existente, sem novo POST.
Após publisher iniciado/possível: nenhuma nova sessão, retry/rerun, plataforma
faltante ou substituto do slot. O run original pode concluir suas plataformas.

Prepare tem até 2 reparos internos; CI até 10 passes internos atualmente.
Recovery até 3 ciclos reais por rodada; 15 candidatas na janela CAS desde a
última queue e até 5 autorados por slot, somando creator/recovery.
Esses contadores são distintos; não resete nem reduza gates.
