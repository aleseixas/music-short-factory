# Direção editorial automática

Este é o contrato de autoria para o GPT/agente externo que prepara episódios do Music Short Factory.

O agente é o **diretor + editor criativo**. Python/FFmpeg são o executor determinístico: validam e executam as decisões explícitas registradas nos arquivos do episódio. O objetivo não é “usar features”; é produzir o melhor short possível com as ferramentas reais disponíveis na `main`.

O prompt operacional copiável fica em [`templates/editorial-direction-prompt.md`](../templates/editorial-direction-prompt.md). A política de áudio externo fica em [`docs/audio-search.md`](audio-search.md).

## Main é a fonte da verdade

Antes de escrever `timeline.json`, confirme no código atual os schemas, enums e capacidades de:

- assets remotos e cache;
- vídeo e trims;
- shot motions e transitions;
- background music;
- SFX;
- visual FX;
- kinetic/text FX;
- highlights;
- overlays;
- TTS/timings;
- renderer e validações.

Se esta documentação divergir da `main`, o código atual prevalece. Nunca invente campo, enum, profile, type ou asset.

## Editor Mode

Não trate `timeline.json` como formulário. Para cada shot, pense conscientemente em:

`ASSET | TRECHO/TRIM | MOTION | TRANSITION | VISUAL FX | TEXT FX | HIGHLIGHT | OVERLAY | SFX`

Essa matriz é um checklist mental; não é um campo do schema.

Para cada shot, responda mentalmente:

1. qual informação ou emoção precisa chegar agora?
2. o asset/trecho é o melhor disponível?
3. motion melhora o plano ou o vídeo já tem movimento suficiente?
4. a passagem pede cut ou crossfade?
5. visual FX reforça hierarquia ou impacto?
6. kinetic text melhora retenção/compreensão?
7. highlight é uma forma mais limpa de destacar a informação?
8. overlay acrescenta contexto visual concreto?
9. um SFX curado reforça o evento?
10. ou o shot fica melhor deliberadamente limpo?

Não seja conservador por padrão. Se uma feature suportada melhorar claramente retenção, ritmo, clareza, surpresa, compreensão, impacto ou payoff, prefira usá-la. Ao mesmo tempo, não use camadas apenas para aumentar densidade.

Um beat importante pode combinar várias camadas sincronizadas — por exemplo `punch_zoom` + kinetic text + SFX — quando todas reforçam o mesmo evento editorial.

## Passadas obrigatórias de edição

### 1. Montagem shot por shot

Revise:

- asset;
- trecho/trim;
- foco/crop quando suportado;
- motion;
- transition.

Primeiro procure um visual/trecho melhor antes de tentar salvar um plano fraco só com FX.

### 2. Acabamento shot por shot

Revise:

- visual FX;
- text FX;
- highlight;
- overlay;
- SFX.

### 3. Polimento global

Remova somente camadas que estejam:

- redundantes;
- conflitantes;
- excessivamente repetitivas;
- prejudicando voz/música;
- duplicando informação sem ganho editorial.

Revise isoladamente hook, mudanças de assunto, reveals, estatísticas, virada narrativa e payoff para garantir que beats fortes não tenham ficado editorialmente secos quando existem ferramentas adequadas.

## Ordem geral de autoria

1. pesquisar e escrever a história, registrando fontes;
2. construir a narração;
3. escolher assets e shots;
4. identificar editorial beats importantes;
5. escolher background music;
6. fazer montagem shot por shot;
7. fazer acabamento shot por shot;
8. fazer polimento global;
9. validar schema, catálogos, assets, tempos, trims e conflitos;
10. salvar o episódio.

A edição nasce a partir da história. Não distorça a narrativa apenas para encaixar um efeito.

## Descoberta de recursos permitidos

Antes de escrever cues, leia os arquivos reais do projeto:

- música: `assets/audio/music/catalog.json`;
- SFX: `assets/audio/sfx/catalog.json`;
- overlays: IDs válidos em `episodes/<slug>/assets.json` e arquivos compatíveis quando a `main` exigir local.

### Background music

Quando houver web, a estratégia é external-first conforme `docs/audio-search.md`. Pesquise e compare candidatas plausíveis. Se uma opção externa for aprovada e o schema atual permitir, use profile/chave dedicada conforme o catálogo de música.

Se a busca externa falhar ou não houver opção adequada/compatível, use um profile existente na repo.

### SFX

A estratégia é diferente: `assets/audio/sfx/catalog.json` é a biblioteca curada e a fonte de verdade.

Durante a autoria de episódio:

- use somente `type` existente no catálogo no início da execução;
- não pesquise novos SFX na web;
- não crie novo `type` por episódio;
- não altere o catálogo de SFX;
- não substitua um efeito curado por externo apenas por preferência.

A timeline referencia apenas `type`. URLs/cache são responsabilidade do catálogo/engine.

## Vocabulário atual conhecido

Confirme sempre na `main` antes de usar. No estado atual, a direção inclui:

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

- **HOOK:** alta densidade e impacto, mas legível;
- **CONTEXT:** normalmente edição mais limpa, podendo usar motion/zoom lento;
- **BUILDUP:** pode usar movimento, riser ou tensão quando houver type adequado;
- **REVEAL/TURNING_POINT:** bom candidato a impact, punch zoom e texto curto;
- **STATISTIC:** número forte como text/highlight, podendo receber SFX;
- **NAME_OR_ENTITY:** destaque curto; overlay quando realmente identificar melhor;
- **LOCATION:** visual contextual real quando acrescentar informação;
- **PAYOFF:** conclusão forte e clara, sem avalanche automática de camadas.

## Ritmo e retenção

Para linguagem nativa de TikTok, Reels e Shorts:

- impacto visual perceptível nos primeiros ~0,5s;
- hook completo até ~2s;
- promessa narrativa até ~5s;
- mudança visual/editorial perceptível aproximadamente a cada 4–6s quando fizer sentido;
- evite composições estáticas por ~3–4s quando existe alternativa melhor;
- prefira trocar asset/trecho antes de apenas adicionar efeito;
- não reutilize o mesmo asset principal consecutivamente sem motivo narrativo;
- mantenha payoff visual/editorial forte nos últimos 5–8s.

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

Kinetic text não é legenda duplicada. Use para:

- hooks;
- contraste;
- palavras-chave;
- nomes;
- números/estatísticas;
- frases muito curtas memoráveis.

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

Não existe obrigação de `1 SFX por shot` nem meta rígida. Ao mesmo tempo, não economize por medo de quantidade. O objetivo é usar a biblioteca curada para tornar beats importantes mais perceptíveis e satisfatórios sem prejudicar voz/música.

Sincronize a entrada do SFX com o evento que ele reforça. Varie types quando houver alternativas melhores. Não use efeitos aleatórios como preenchimento.

O warning acima de 25 é alerta de excesso, nunca meta.

Tipos `meme_br_` são intervenções editoriais completas. Use com parcimônia, reproduza integralmente (`source_start_seconds=0` e sem `duration_seconds`) e evite sobreposição com outro meme/SFX falado sem motivo editorial claro.

## Camadas coordenadas

Cues de camadas diferentes podem compartilhar timestamps próximos quando formam um único editorial beat.

Exemplo conceitual para uma estatística importante:

- `punch_zoom` começa pouco antes do número aparecer;
- kinetic text apresenta o número;
- um SFX curto entra junto do impacto;
- opcionalmente um overlay contextual entra no mesmo beat.

Isso é uma decisão editorial única. Não espalhe as camadas de forma aleatória.

## Densidade e warnings

Não use quantidade mínima rígida para nenhuma camada. A densidade deve emergir da história, dos assets e dos beats.

Warnings de excesso devem provocar revisão, não transformar estilo em hard error. Preserve contraste entre trechos mais densos e trechos limpos.

## Assets, vídeos e trims

Vídeo real é prioridade quando houver material bom e reutilizável.

- tente variedade real de assets;
- não conte cortes diferentes do mesmo arquivo como vídeos distintos;
- não use filler;
- prefira movimento perceptível nos primeiros 1–2s do trecho usado;
- quando duração real puder ser verificada, deixe margem suficiente após `source_start_seconds`;
- se a autoria não puder verificar duração/metadata, não finja que verificou.

Mídia remota deve seguir o formato atual da `main`, com URL direta quando aplicável. Não faça commit de cache ou mídia pesada desnecessária.

## Áudio e mix

A voz domina o mix. Background deve ficar baixo/moderado e SFX devem reforçar sem competir com narração.

Ducking, loudness e normalização são responsabilidade do engine quando implementados globalmente; não invente campos por episódio para compensar.

## Erros bloqueantes e warnings

Schema e renderer são autoridade para erros.

Corrija antes do render quando houver, entre outros:

- profile/type inexistente;
- asset inválido;
- enum/range inválido;
- trim fora da fonte;
- cue fora da duração válida;
- conflito estrutural;
- mais visual FX por shot do que o renderer aceita.

Questões de densidade, repetição e estilo devem ser tratadas como revisão editorial quando o código não as define como hard error.

## Checklist final do editor

Antes do commit, revise cada shot usando:

`ASSET | TRIM | MOTION | TRANSITION | VISUAL FX | TEXT FX | HIGHLIGHT | OVERLAY | SFX`

Nenhuma ferramenta precisa aparecer em todo shot. Porém, uma oportunidade editorial evidente não deve ficar vazia apenas por conservadorismo.

Depois faça uma revisão global para garantir:

- história clara;
- hook forte;
- variedade visual;
- ritmo adequado;
- texto não duplicado;
- SFX sincronizado;
- mix legível;
- payoff forte;
- nenhum campo/capacidade inventado.

O objetivo final é simples: **usar conscientemente todo o potencial real do editor para maximizar retenção, clareza, ritmo e impacto em TikTok, Reels e Shorts — sem adicionar nada que não melhore o vídeo.**
