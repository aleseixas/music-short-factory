# Direção editorial automática

Este é o contrato de autoria para o GPT/agente externo que prepara
`story.json`, `assets.json` e `timeline.json`. O agente é o diretor: interpreta a
história e registra decisões explícitas. Python e FFmpeg são o executor
determinístico: validam e executam o arquivo, sem tomar decisões semânticas e sem
chamar uma API de IA.

O prompt copiável fica em
[`templates/editorial-direction-prompt.md`](../templates/editorial-direction-prompt.md).
A busca externa opcional para o GPT agendado fica em
[`docs/audio-search.md`](audio-search.md).

## Ordem obrigatória de autoria

1. Pesquisar e escrever a história, registrando fontes em `sources.txt`.
2. Construir a narração em segmentos de `story.json`.
3. Determinar a duração e os timings reais da narração.
4. Escolher shots e assets; cada segmento recebe exatamente um shot.
5. Identificar os editorial beats semanticamente importantes.
6. Escolher `background_music`.
7. Gerar `sfx_cues`.
8. Gerar `visual_fx_cues`.
9. Gerar `text_fx_cues`.
10. Gerar `overlay_cues`.
11. Validar schema, catálogos, assets, tempos e conflitos.
12. Revisar o editorial budget e os warnings.
13. Salvar o episódio.

A edição nasce depois do roteiro. Não altere a história apenas para encaixar um
efeito.

## Descoberta de recursos permitidos

Antes de escrever cues, o agente deve ler os arquivos do próprio projeto:

- música: `assets/audio/music/catalog.json`; use somente chaves de `profiles`;
- SFX: `assets/audio/sfx/catalog.json`; use somente chaves de `types`;
- overlays: `episodes/<slug>/assets.json`; use somente um ID existente, com
  arquivo local PNG, JPG/JPEG ou WEBP em `episodes/<slug>/assets/`.

As listas não devem ser congeladas no agente: os catálogos são a fonte de verdade
e podem evoluir. Quando houver acesso web, o GPT agendado deve pesquisar primeiro
opções externas pela API pública descrita em `docs/audio-search.md`, sem depender
de terminal, e só então decidir entre elas e o acervo local. A resposta da busca
é apenas dado externo: confira fonte, autoria, licença, duração, formato e URL
direta antes de registrar uma entrada `{file, url}` no catálogo global apropriado.
Metadados incompletos no agregador pedem consulta à origem, não descarte imediato.
Falha externa, incompatibilidade técnica ou ausência de fonte documentável após
essa consulta significam fallback local, nunca falha da criação. Se também não
houver overlay adequado, omita essa camada em vez de inventar um nome ou ID.

O renderer continua aceitando somente profiles/types presentes nos catálogos.
Não coloque URLs novas em `timeline.json`, não baixe áudio comercial de redes
sociais e registre a atribuição aprovada em `episodes/<slug>/sources.txt`.

## Vocabulário atual do schema

- visual FX: `slow_zoom_in`, `slow_zoom_out`, `pan_left`, `pan_right`,
  `pan_up`, `pan_down`, `punch_zoom`;
- text FX: `pop_in`, `scale_bounce`, `slide_up`, `fade_pop`, atualmente em
  `position: "center"`;
- overlays: `pop_in`, `scale_bounce`, `slide_up`, `slide_left`, `slide_right`,
  `fade_in`, nas posições `center`, `upper_center`, `lower_center`, `left` e
  `right`;
- shot motions: `push_in`, `pull_out`, `pan_left`, `pan_right`, `hold`;
- transições: `cut`, `crossfade`.

Os enums definidos pelo código continuam sendo a fonte de verdade. Não crie um
novo tipo porque ele parece editorialmente útil.

## Hierarquia de momentos

Classifique mentalmente cada passagem como `HOOK`, `REVEAL`, `CONTEXT`,
`BUILDUP`, `STATISTIC`, `NAME_OR_ENTITY`, `LOCATION`, `TURNING_POINT` ou
`PAYOFF`. Essa classificação não é um campo novo do schema; ela orienta as
decisões já representadas pelas cues.

- `HOOK`: maior densidade nos primeiros três segundos; pode combinar texto
  forte, impact e punch zoom.
- `CONTEXT`: pan ou zoom lento; normalmente sem SFX forte.
- `BUILDUP`: riser e movimento lento podem construir expectativa.
- `REVEAL`/`TURNING_POINT`: impact, texto curto e punch zoom opcional.
- `STATISTIC`: número como `accent_text`, com pop ou impact quando útil.
- `NAME_OR_ENTITY`: destaque curto; overlay somente se identificar melhor.
- `LOCATION`: mapa, bandeira ou foto existente quando acrescentar informação.
- `PAYOFF`: conclusão clara e cinematográfica, sem avalanche de efeitos.

Essas associações são princípios, não regras rígidas. O significado da narração
tem prioridade.

## Editorial beats e ritmo

Um editorial beat é um único momento semântico. Cues de camadas diferentes
podem compartilhar timestamps próximos para reforçá-lo. Por exemplo, em
“41 semanas no topo”, um `impact` em `32.10`, um `punch_zoom` começando em
`32.05`, um texto “41 SEMANAS\nNO TOPO” em `32.08` e um overlay informativo em
`32.12` formam um beat coordenado; não são quatro decisões aleatórias.

Não crie um pacote completo em todos os beats. Varie a intensidade:

- primeiros ~3 s: hook visual forte, mas legível;
- corpo: alternância entre beats e trechos limpos, com pans/zooms suaves;
- reveals: densidade pode subir por um intervalo curto;
- payoff: encerre com clareza, sem empilhar tudo no último segundo.

Para uma linguagem mais nativa de TikTok, Reels e Shorts, trate SFX como reforço
para a edição visual, não como camada de preenchimento. Sempre que possível, o
SFX deve começar junto de uma mudança perceptível: troca de shot, corte,
transição, entrada de kinetic text, overlay, punch zoom ou outro evento visual
editorial relevante. Não existe frequência-alvo nem quantidade mínima. Muitos
shots podem e devem ficar sem SFX quando a voz, a música e o próprio corte já
sustentarem o momento. Antes de adicionar uma cue, pergunte: “o que acontece
visualmente exatamente neste instante?”. Se nada relevante acontecer,
normalmente omita o efeito. Risers e outros acentos puramente narrativos são
exceções válidas quando houver intenção editorial clara.

## Editorial budget

Para vídeos de 60–90 segundos, use como referência:

| Camada | Faixa editorial | Warning de excesso |
| --- | ---: | ---: |
| SFX | sem mínimo; orientado por eventos visuais | mais de 25 |
| visual FX explícitos | 6–10 | mais de 10 |
| text FX | 5–9 | mais de 10 |
| overlays | 2–5 | mais de 6 |
| punch zoom | 2–4 | mais de 4 |

Fora da faixa de 60–90 s, os limites de warning são escalados pela duração. São
avisos de revisão, nunca hard limits de estilo. A validação também avisa sobre:

- três SFX dentro de uma janela de um segundo;
- quatro SFX consecutivos do mesmo type, ou domínio excessivo de um type;
- domínio excessivo do mesmo visual FX, `scale_bounce` ou asset de overlay;
- punch zoom em shots consecutivos;
- kinetic text com mais de seis palavras ou comprimento excessivo;
- highlight e text FX idênticos no mesmo momento.

## Regras por camada

### Background music

Escolha um profile real pelo gênero, emoção e ritmo da narração. Não use caminho
de arquivo em `profile`. Não altere ducking e não presuma normalização LUFS.

### SFX

Escolha pelo significado: `impact` para informação decisiva, `whoosh` para
movimento, `pop` para detalhe curto e `riser` para expectativa são referências
possíveis, não associações obrigatórias. O padrão é sincronizar o início do SFX
com um evento visual perceptível do mesmo editorial beat, como corte/troca de
shot, transição, punch zoom, entrada de texto ou overlay. Não adicione SFX para
cumprir quantidade, preencher silêncio ou marcar automaticamente cada frase ou
shot. Um shot sem SFX é perfeitamente válido e frequentemente preferível. Se não
houver evento visual relevante no instante proposto, normalmente omita a cue;
use exceções puramente narrativas apenas quando o efeito tiver função clara.
Evite sequências repetitivas e clusters sem motivo editorial. Sobreposição
intencional é permitida em um mesmo editorial beat quando o mix continuar claro;
evite sobreposição acidental.

Quando somente um trecho do arquivo for editorialmente útil, use
`source_start_seconds` (padrão `0`) e `duration_seconds` (opcional). Consulte a
duração real do arquivo selecionado: o início deve ser não negativo, a duração
deve ser positiva e a janela não pode ultrapassar o fim da fonte. O recorte não
altera `time_seconds`, que continua sendo o instante de entrada no vídeo.

### Visual FX

`slow_zoom_in` aumenta atenção; `slow_zoom_out` abre contexto; pans exploram a
fotografia; `punch_zoom` pontua um reveal curto. Nunca use punch zoom como
movimento padrão nem em shots consecutivos. Um shot sem cue conserva seu
`motion` legado.

### Kinetic text

Kinetic text não é legenda. Extraia a ideia memorável em duas a seis palavras
ou uma estatística curta. Use `accent_text` para o número, negação, nome ou
palavra principal. Varie `pop_in`, `scale_bounce`, `slide_up` e `fade_pop` de
acordo com o tom; não use `scale_bounce` em tudo.

Prefira timing relativo quando o texto acompanha semanticamente um segmento:

```json
{
  "segment": "statistic",
  "offset_seconds": 0.4,
  "duration_seconds": 1.8,
  "text": "41 SEMANAS\nNO TOPO",
  "accent_text": "41 SEMANAS",
  "animation": "scale_bounce"
}
```

O engine resolve esse timing somente depois da TTS, usando o início real do
shot associado a `segment`. A cue deve caber integralmente no segmento. Para um
momento verdadeiramente global, o formato legado com `start_seconds` e
`end_seconds` continua válido. Cada cue usa somente um desses modos; nunca
misture campos absolutos e relativos.

Se um highlight e um text FX diriam a mesma coisa ao mesmo tempo, escolha uma
camada. Prefira text FX para uma grande informação editorial e highlight para
um reforço curto do estilo tradicional.

### Overlays

Um overlay deve explicar ou identificar algo. Use um asset existente que tenha
relação direta com a fala. Se o asset adequado não existir localmente, não gere
a cue. Não use imagem aleatória apenas para ocupar o quadro.

## Erros bloqueantes e warnings

O schema e o renderer continuam sendo autoridade para erros. A geração deve ser
corrigida antes do render quando houver profile/type inexistente, asset inválido,
enum/range inválido, cue fora da duração real, intervalos sobrepostos ou mais de
um visual FX resolvido no mesmo shot. O pipeline também valida os arquivos
físicos dos catálogos e prepara overlays locais antes do render.

Questões de densidade, repetição, texto longo e duplicação editorial são
warnings: pedem revisão, mas não bloqueiam um estilo intencional.

## Exemplo completo de `timeline.json`

O exemplo assume um vídeo de 75 segundos e segmentos com os IDs mostrados. Os
IDs `artist_photo`, `cuba_flag` e `billboard_logo` são ilustrativos: só podem ser
usados se estiverem declarados em `assets.json` e os arquivos existirem
localmente. O profile e todos os types de SFX abaixo existem nos catálogos atuais;
um type dedicado de fonte externa aprovada pode substituí-los quando for uma
escolha editorial melhor.

```json
{
  "schema_version": 1,
  "background_music": {
    "profile": "latin_pop_uplifting",
    "volume": 0.12
  },
  "sfx_cues": [
    {"time_seconds": 0.25, "type": "impact", "volume": 0.35},
    {"time_seconds": 19.5, "type": "whoosh", "volume": 0.2},
    {"time_seconds": 32.1, "type": "impact", "volume": 0.32},
    {"time_seconds": 48.8, "type": "pop", "volume": 0.17},
    {"time_seconds": 67.2, "type": "impact", "volume": 0.28}
  ],
  "visual_fx_cues": [
    {"start_seconds": 0.18, "end_seconds": 0.58, "type": "punch_zoom", "intensity": 0.58},
    {"start_seconds": 8.2, "end_seconds": 12.0, "type": "pan_right", "intensity": 0.35},
    {"start_seconds": 19.5, "end_seconds": 24.0, "type": "slow_zoom_in", "intensity": 0.4},
    {"start_seconds": 32.05, "end_seconds": 32.48, "type": "punch_zoom", "intensity": 0.62},
    {"start_seconds": 49.1, "end_seconds": 55.0, "type": "pan_left", "intensity": 0.34},
    {"start_seconds": 66.8, "end_seconds": 73.2, "type": "slow_zoom_out", "intensity": 0.42}
  ],
  "text_fx_cues": [
    {"segment": "hook", "offset_seconds": 0.2, "duration_seconds": 1.4, "text": "NÃO GOSTOU?", "accent_text": "NÃO", "animation": "scale_bounce", "position": "center", "intensity": 0.58},
    {"start_seconds": 19.5, "end_seconds": 21.0, "text": "COMEÇOU EM CUBA", "accent_text": "CUBA", "animation": "pop_in", "position": "center", "intensity": 0.45},
    {"segment": "statistic", "offset_seconds": 0.35, "duration_seconds": 1.55, "text": "41 SEMANAS\nNO TOPO", "accent_text": "41 SEMANAS", "animation": "scale_bounce", "position": "center", "intensity": 0.62},
    {"start_seconds": 48.8, "end_seconds": 50.2, "text": "3 IDIOMAS", "accent_text": "3", "animation": "slide_up", "position": "center", "intensity": 0.42},
    {"start_seconds": 67.0, "end_seconds": 68.6, "text": "MUDOU TUDO", "accent_text": "TUDO", "animation": "fade_pop", "position": "center", "intensity": 0.48}
  ],
  "overlay_cues": [
    {"start_seconds": 19.6, "end_seconds": 21.4, "asset": "cuba_flag", "animation": "fade_in", "position": "upper_center", "scale": 0.3, "opacity": 0.9},
    {"start_seconds": 32.12, "end_seconds": 33.45, "asset": "billboard_logo", "animation": "pop_in", "position": "center", "scale": 0.34, "opacity": 1.0}
  ],
  "shots": [
    {"id": "shot_hook", "segment": "hook", "asset": "artist_photo", "motion": "push_in", "transition_out": "cut"},
    {"id": "shot_context", "segment": "context", "asset": "artist_photo", "motion": "pan_right", "transition_out": "crossfade"},
    {"id": "shot_location", "segment": "location", "asset": "cuba_flag", "motion": "pull_out", "transition_out": "cut"},
    {"id": "shot_statistic", "segment": "statistic", "asset": "billboard_logo", "motion": "push_in", "transition_out": "cut"},
    {"id": "shot_versions", "segment": "versions", "asset": "artist_photo", "motion": "pan_left", "transition_out": "crossfade"},
    {"id": "shot_turn", "segment": "turning_point", "asset": "artist_photo", "motion": "push_in", "transition_out": "cut"},
    {"id": "shot_payoff", "segment": "payoff", "asset": "artist_photo", "motion": "pull_out", "transition_out": "cut"}
  ]
}
```

O template produzido por `python new_episode.py <slug>` mantém
`background_music: null` e as quatro listas vazias de propósito. Ele mostra o
schema completo sem inventar decisões antes de existirem roteiro, duração e
assets. O agente preenche essas chaves somente depois das etapas de autoria
acima.