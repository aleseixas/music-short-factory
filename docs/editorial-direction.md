# Direção editorial automática

Este é o contrato de autoria para o GPT/agente externo que prepara episódios do Music Short Factory.

O agente é o **diretor + editor criativo**. Python/FFmpeg são o executor determinístico: validam e executam as decisões explícitas registradas nos arquivos do episódio. O objetivo não é “usar features”; é produzir o melhor short possível com as ferramentas reais disponíveis na `main`.

O prompt operacional copiável fica em [`templates/editorial-direction-prompt.md`](../templates/editorial-direction-prompt.md). A política de áudio externo fica em [`docs/audio-search.md`](audio-search.md), a direção de voz por segmento em [`docs/narration-delivery.md`](narration-delivery.md), e o fluxo de pesquisa/inspeção de imagens e vídeos em [`docs/visual-search.md`](visual-search.md).

## Main é a fonte da verdade

Antes de escrever `story.json` ou `timeline.json`, confirme no código atual os schemas, enums e capacidades de:

- narração, TTS, timings e `delivery` por segmento;
- assets remotos e cache;
- vídeo e trims;
- shot motions e transitions;
- background music;
- SFX;
- visual FX;
- kinetic/text FX;
- highlights;
- overlays;
- renderer e validações.

Se esta documentação divergir da `main`, o código atual prevalece. Nunca invente campo, enum, profile, type, delivery ou asset.

## Editor Mode

Não trate os JSONs como formulários. Pense como editor de vídeo vertical.

A revisão editorial considera conscientemente:

`VOICE DELIVERY | ASSET | TRECHO/TRIM | MOTION | TRANSITION | VISUAL FX | TEXT FX | HIGHLIGHT | OVERLAY | SFX`

Essa matriz é apenas um checklist mental; não é um campo do schema.

Para cada segmento/shot, responda mentalmente:

1. qual informação ou emoção precisa chegar agora?
2. a interpretação da voz deve ser neutra ou algum `delivery` real melhora o beat?
3. o asset/trecho é o melhor disponível?
4. motion melhora o plano ou o vídeo já tem movimento suficiente?
5. a passagem pede cut ou crossfade?
6. visual FX reforça hierarquia ou impacto?
7. kinetic text melhora retenção/compreensão?
8. highlight é uma forma mais limpa de destacar a informação?
9. overlay acrescenta contexto visual concreto?
10. um SFX curado reforça o evento?
11. ou o momento fica melhor deliberadamente limpo?

Não seja conservador por padrão. Se uma feature suportada melhorar claramente retenção, ritmo, clareza, surpresa, compreensão, impacto, emoção ou payoff, prefira usá-la. Ao mesmo tempo, não use ferramentas apenas para aumentar densidade.

Um beat importante pode combinar várias camadas sincronizadas — por exemplo `delivery=reveal` + `punch_zoom` + kinetic text + SFX — quando todas reforçam a mesma virada editorial.

## Passadas obrigatórias de edição

### 1. Direção de narração

Depois de escrever o roteiro, revise cada segmento e escolha conscientemente o `delivery` quando a `main` suportar.

No estado atual, os valores são:

- `neutral`
- `hook`
- `curious`
- `emotional`
- `dramatic`
- `reveal`
- `payoff`

Use-os como intenções editoriais, não como quotas. Não alterne deliveries apenas para criar variedade e não invente valores novos.

A voz precisa continuar natural e inteligível. Um segmento factual pode ficar `neutral`; um hook pode usar `hook`; uma descoberta pode usar `reveal`; um fechamento pode usar `payoff`. A função narrativa do texto é mais importante que o nome do preset.

Consulte [`docs/narration-delivery.md`](narration-delivery.md) para as regras completas.

### 2. Montagem shot por shot

Revise:

- asset;
- trecho/trim;
- foco/crop quando suportado;
- motion;
- transition.

Primeiro procure um visual/trecho melhor antes de tentar salvar um plano fraco só com FX.

### 3. Acabamento shot por shot

Revise:

- visual FX;
- text FX;
- highlight;
- overlay;
- SFX.

### 4. Polimento global

Remova somente escolhas que estejam:

- redundantes;
- conflitantes;
- excessivamente repetitivas;
- prejudicando voz/música;
- duplicando informação sem ganho editorial;
- tornando a interpretação vocal caricata ou instável.

Revise isoladamente hook, mudanças de assunto, reveals, estatísticas, virada narrativa e payoff para garantir que beats fortes não tenham ficado editorialmente secos quando existem ferramentas adequadas.

## Ordem geral de autoria

1. pesquisar e escrever a história, registrando fontes;
2. construir a narração;
3. escolher `delivery` por segmento quando melhorar a interpretação;
4. identificar editorial beats importantes;
5. pesquisar, comparar, inspecionar e ranquear candidatos visuais;
6. escolher assets, shots e trims;
7. escolher background music;
8. fazer montagem shot por shot;
9. fazer acabamento shot por shot;
10. fazer polimento global;
11. validar schema, deliveries, catálogos, assets, tempos, trims e conflitos;
12. salvar o episódio.

A edição nasce a partir da história. Não distorça a narrativa apenas para encaixar um efeito ou preset de voz.

## Narração e delivery

`delivery` fica no segmento de `story.json`, não em `timeline.json`.

Exemplo:

```json
{
  "id": "hook",
  "text": "Essa música quase nunca chegou ao público.",
  "delivery": "hook"
}
```

Quando o episódio usa delivery segmentado, o engine pode sintetizar os segmentos individualmente e concatenar a narração preservando os timings globais. Delivery pode alterar a duração real da fala; portanto, nunca estime shots a partir de uma duração imaginada do preset. Use os timings e a duração reais produzidos pelo pipeline.

Áudio customizado tem prioridade no fluxo atual. Se a narração final vier de áudio customizado, não afirme que os deliveries dos segmentos foram aplicados.

O schema é independente do provider: um provider pode traduzir a intenção apenas para os controles que suporta. Não descreva `delivery` como garantia de uma emoção humana específica.

## Descoberta de recursos permitidos

Antes de escrever cues, leia os arquivos reais do projeto:

- deliveries: enums reais na `main`;
- música: `assets/audio/music/catalog.json`;
- SFX: `assets/audio/sfx/catalog.json`;
- imagens/vídeos externos: `docs/visual-search.md` e os providers públicos descritos ali;
- overlays: IDs válidos em `episodes/<slug>/assets.json` e arquivos compatíveis quando a `main` exigir local.

### Imagens e vídeos

Na etapa de autoria, siga `pesquisar → comparar → inspecionar → ranquear →
escolher`. Use mais de uma consulta quando necessário, deduplique candidatos e
compare Wikimedia Commons (imagem/vídeo) e Openverse Images (imagem). Quando um
vídeo relevante com movimento perceptível melhorar o plano, prefira-o a uma
imagem. Se providers ou inspeção falharem, continue com outro resultado ou com
um asset local/relevante disponível; busca externa nunca roda no renderer.

### Background music

Quando houver web, a estratégia é external-first conforme `docs/audio-search.md`. Pesquise e compare candidatas plausíveis. Se uma opção externa for aprovada e o schema atual permitir, use profile/chave dedicada conforme o catálogo de música.

Se a busca externa falhar ou não houver opção adequada/compatível, use um profile existente na repo.

### SFX

`assets/audio/sfx/catalog.json` é a biblioteca curada e a fonte de verdade.

Durante a autoria de episódio:

- use somente `type` existente no catálogo no início da execução;
- não pesquise novos SFX na web;
- não crie novo `type` por episódio;
- não altere o catálogo de SFX;
- não substitua um efeito curado por externo apenas por preferência.

A timeline referencia apenas `type`. URLs/cache são responsabilidade do catálogo/engine.

## Vocabulário atual conhecido

Confirme sempre na `main` antes de usar.

### Voice delivery

- `neutral`
- `hook`
- `curious`
- `emotional`
- `dramatic`
- `reveal`
- `payoff`

### Shot motions

- `push_in`
- `pull_out`
- `pan_left`
- `pan_right`
- `hold`

### Transitions

- `cut`
- `crossfade`

### Visual FX

- `slow_zoom_in`
- `slow_zoom_out`
- `pan_left`
- `pan_right`
- `pan_up`
- `pan_down`
- `punch_zoom`

### Text FX

- `pop_in`
- `scale_bounce`
- `slide_up`
- `fade_pop`

### Overlays

Animações atuais incluem:

- `pop_in`
- `scale_bounce`
- `slide_up`
- `slide_left`
- `slide_right`
- `fade_in`

Posições atuais incluem `center`, `upper_center`, `lower_center`, `left` e `right`.

Os enums do código são sempre a autoridade.

## Hierarquia de editorial beats

Classifique mentalmente momentos como:

`HOOK`, `REVEAL`, `CONTEXT`, `BUILDUP`, `STATISTIC`, `NAME_OR_ENTITY`, `LOCATION`, `TURNING_POINT`, `PAYOFF`.

Essa classificação não é campo novo do schema.

Princípios:

- **HOOK:** alta densidade e impacto; `delivery=hook` é candidato natural quando combina com o texto;
- **CONTEXT:** edição e voz normalmente mais limpas; `neutral` ou `curious` podem funcionar;
- **BUILDUP:** tensão crescente; `curious` ou `dramatic` podem reforçar sem exagero;
- **REVEAL/TURNING_POINT:** candidato a `reveal`/`dramatic`, impact, punch zoom e texto curto;
- **STATISTIC:** número forte como text/highlight, podendo receber SFX; delivery depende do significado;
- **NAME_OR_ENTITY:** destaque curto; overlay quando realmente identificar melhor;
- **LOCATION:** visual contextual real quando acrescentar informação;
- **PAYOFF:** conclusão forte e clara; `payoff` pode reforçar o fechamento sem avalanche de camadas;
- **MOMENTO SENSÍVEL:** `emotional` pode funcionar quando o texto pede intimidade ou reflexão.

## Ritmo e retenção

Para linguagem nativa de TikTok, Reels e Shorts:

- impacto perceptível nos primeiros ~0,5s;
- hook completo até ~2s;
- promessa narrativa até ~5s;
- mudança visual/editorial perceptível aproximadamente a cada 4–6s quando fizer sentido;
- evite composições estáticas por ~3–4s quando existe alternativa melhor;
- prefira trocar asset/trecho antes de apenas adicionar efeito;
- não reutilize o mesmo asset principal consecutivamente sem motivo narrativo;
- mantenha payoff visual/editorial forte nos últimos 5–8s;
- use variação de delivery para reforçar a curva narrativa, não para criar oscilação artificial a cada frase.

Esses tempos são guias, não uma grade mecânica.

## Regras por camada

### Motion

Não use `hold` por hábito. Em imagens, motion discreto costuma melhorar profundidade/ritmo. Em vídeo já dinâmico, `hold` pode ser a melhor escolha.

### Transitions

Escolha conscientemente:

- `cut`: energia, impacto, ritmo e mudança clara;
- `crossfade`: passagem suave, emocional ou contemplativa quando fizer sentido.

Não use um único tipo por inércia.

### Visual FX

Visual FX marcam hierarquia, não substituem montagem.

- zoom/pan lento: construção e atenção;
- `punch_zoom`: hook, surpresa, reveal, estatística, reação ou payoff.

Respeite o limite real do renderer. Enquanto a `main` aceitar no máximo um `visual_fx_cue` por shot resolvido, nunca coloque dois no mesmo shot.

### Kinetic text

Kinetic text não é legenda duplicada. Use para hooks, contraste, palavras-chave, nomes, números/estatísticas e frases muito curtas memoráveis.

Prefira 2–6 palavras ou uma estatística curta. Use `accent_text` somente quando ele aparecer literalmente em `text`.

Quando a `main` suportar timing relativo, prefira `segment` + `offset_seconds` + `duration_seconds` para texto semanticamente ligado à fala. Não misture timing relativo e absoluto na mesma cue.

### Highlight

Use para reforço curto ligado ao shot quando for mais limpo do que kinetic text. Não duplique a mesma informação simultaneamente em highlight e text FX.

### Overlay

Overlay deve explicar, identificar ou acrescentar contexto visual concreto. Use somente asset válido e respeite os requisitos atuais de formato/localidade, escala, opacidade, posição e animação.

Não use overlay só para “encher” a tela.

### SFX

SFX devem acompanhar e enriquecer a edição quando fizer sentido.

Em **cada shot**, avalie se existe evento visual/narrativo claro: troca/corte, transition, punch zoom, kinetic text, highlight, overlay, reveal, estatística, mudança de assunto, reação, surpresa, comparação, nome/entidade, virada ou payoff.

Quando houver um `type` adequado no catálogo, prefira reforçar o momento.

Não existe obrigação de `1 SFX por shot` nem meta rígida. Ao mesmo tempo, não economize por medo de quantidade. O objetivo é tornar beats importantes mais perceptíveis sem prejudicar voz/música.

Sincronize a entrada do SFX com o evento que ele reforça. Varie types quando houver alternativas melhores. Não use efeitos aleatórios como preenchimento.

O warning acima de 25 é alerta de excesso, nunca meta.

Tipos `meme_br_` são intervenções editoriais completas. Use com parcimônia, reproduza integralmente (`source_start_seconds=0` e sem `duration_seconds`) e evite sobreposição com outro meme/SFX falado sem motivo editorial claro.

## Camadas coordenadas

Ferramentas diferentes podem reforçar o mesmo editorial beat.

Exemplo conceitual para um reveal:

- o segmento usa `delivery=reveal` quando isso melhora a interpretação;
- `punch_zoom` começa próximo da descoberta;
- kinetic text apresenta a informação-chave;
- um SFX curto entra junto do impacto;
- opcionalmente um overlay contextual entra no mesmo beat.

Isso é uma decisão editorial única. Não espalhe as camadas de forma aleatória.

## Densidade e warnings

Não use quantidade mínima rígida para nenhuma camada ou delivery. A densidade deve emergir da história, dos assets e dos beats.

Warnings de excesso devem provocar revisão, não transformar estilo em hard error. Preserve contraste entre trechos mais densos e trechos limpos.

## Assets, vídeos e trims

Vídeo real é prioridade quando houver material bom e reutilizável.

- antes de escolher, faça múltiplas consultas e compare candidatos conforme `docs/visual-search.md`;
- trate `opening_motion_score`, `motion_score`, `practically_static` e `visual_score` como sinais técnicos, nunca como decisão semântica;
- não persista scores, ranking ou consultas em `assets.json`/`timeline.json`;
- tente variedade real de assets;
- não conte cortes diferentes do mesmo arquivo como vídeos distintos;
- não use filler;
- prefira movimento perceptível nos primeiros 1–2s do trecho usado;
- evite `.mp4`, `.mov` ou `.webm` praticamente estático quando houver alternativa melhor;
- quando duração real puder ser verificada, deixe margem suficiente após `source_start_seconds`;
- se a autoria não puder verificar duração/metadata, não finja que verificou.

Mídia remota deve seguir o formato atual da `main`, com URL direta quando aplicável. Não faça commit de cache ou mídia pesada desnecessária.

O score técnico ajuda a ordenar a inspeção, mas não reconhece relevância para a
fala. Escolha primeiro um asset semanticamente correto; só então use resolução,
aspect ratio, movimento e segurança do trim para desempatar. Se a tarefa tiver
somente GitHub + web, use metadados dos endpoints públicos e seja explícita sobre
a ausência de análise FFmpeg local.

## Áudio e mix

A voz domina o mix. Background deve ficar baixo/moderado e SFX devem reforçar sem competir com narração.

Delivery altera a interpretação da voz e pode também alterar sua duração. Não tente compensar manualmente isso inventando velocidade, pitch ou volume por episódio fora do schema. O provider e o engine traduzem os presets suportados.

Ducking, loudness e normalização são responsabilidade do engine quando implementados globalmente; não invente campos por episódio para compensar.

## Erros bloqueantes e warnings

Schema e renderer são autoridade para erros.

Corrija antes do render quando houver, entre outros:

- `delivery` inexistente;
- profile/type inexistente;
- asset inválido;
- enum/range inválido;
- trim fora da fonte;
- cue fora da duração válida;
- conflito estrutural;
- mais visual FX por shot do que o renderer aceita.

Questões de densidade, repetição, interpretação e estilo devem ser tratadas como revisão editorial quando o código não as define como hard error.

## Checklist final do editor

Antes do commit, revise cada segmento/shot usando:

`VOICE DELIVERY | ASSET | TRIM | MOTION | TRANSITION | VISUAL FX | TEXT FX | HIGHLIGHT | OVERLAY | SFX`

Nenhuma ferramenta precisa aparecer em todo momento. Porém, uma oportunidade editorial evidente não deve ficar vazia apenas por conservadorismo.

Depois faça uma revisão global para garantir:

- história clara e factual;
- hook forte;
- delivery coerente com a intenção de cada segmento;
- nenhuma mudança vocal aleatória ou caricata;
- variedade visual;
- ritmo adequado;
- texto não duplicado;
- SFX sincronizado;
- voz dominante e mix legível;
- payoff forte;
- nenhum campo/capacidade inventado.

O objetivo final é simples: **usar conscientemente todo o potencial real do editor — inclusive a interpretação da voz — para maximizar retenção, clareza, ritmo, emoção e impacto em TikTok, Reels e Shorts, sem adicionar nada que não melhore o vídeo.**
