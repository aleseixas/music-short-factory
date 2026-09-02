# Music Short Factory

Base modular para produzir vídeos curtos verticais sobre músicas para TikTok, Instagram Reels e YouTube Shorts. O conteúdo editorial fica em `episodes/`; Python/FFmpeg executam de forma determinística as decisões registradas nos arquivos do episódio.

A regra principal do projeto é simples:

> **A `main` é a fonte da verdade.**

Schemas, enums, renderer, publishing, catálogos e validações atuais sempre prevalecem sobre documentação ou prompts antigos.

## Filosofia: Editor Mode

O GPT/agente externo não deve tratar `timeline.json` como um formulário. Ele atua como **diretor + editor criativo**, usando o código como uma suíte de edição disponível para produzir o melhor short possível.

Para cada shot, a revisão editorial considera conscientemente:

```text
ASSET | TRECHO/TRIM | MOTION | TRANSITION | VISUAL FX | TEXT FX | HIGHLIGHT | OVERLAY | SFX
```

Essa matriz é apenas um checklist mental; não é um campo do schema.

O objetivo não é usar todas as features em todos os shots. O objetivo é usar conscientemente qualquer recurso suportado que melhore retenção, ritmo, clareza, surpresa, compreensão, impacto ou payoff. Um shot também pode ficar deliberadamente limpo quando isso produzir resultado melhor.

Um beat importante pode combinar várias camadas sincronizadas — por exemplo `punch_zoom` + kinetic text + SFX — quando todas reforçam o mesmo evento editorial.

A autoria segue três passadas principais:

1. **Montagem shot por shot:** asset, trecho/trim, foco/crop, motion e transition.
2. **Acabamento shot por shot:** visual FX, text FX, highlight, overlay e SFX.
3. **Polimento global:** remover somente camadas redundantes, conflitantes, repetitivas ou que prejudiquem voz/música.

O contrato editorial completo está em [`docs/editorial-direction.md`](docs/editorial-direction.md), e o prompt operacional em [`templates/editorial-direction-prompt.md`](templates/editorial-direction-prompt.md).

## Requisitos

- Python 3.10 ou mais recente;
- acesso à internet quando TTS ou mídia remota precisarem ser baixados;
- FFmpeg com os filtros usados pelo projeto, incluindo `xfade`, `subtitles`, `overlay` e `loudnorm`;
- `ffprobe` disponível quando o episódio usa vídeo ou áudio remoto.

Instalação:

```bash
python -m pip install -r requirements.txt
```

## Uso rápido

Crie um episódio:

```bash
python new_episode.py nome_do_video
```

Estrutura esperada:

```text
episodes/<slug>/
├── story.json
├── timeline.json
├── assets.json
├── sources.txt
├── post.json
└── assets/
```

A pasta `assets/` pode ficar vazia quando todos os assets principais forem remotos e o schema atual permitir.

Gere o vídeo:

```bash
python generate.py <slug>
```

Prepare capa e metadados:

```bash
python prepare_post.py <slug>
```

Revise publicação sem enviar:

```bash
python publish.py <slug> --platform all --dry-run
```

Publicação real exige `--live` e credenciais válidas.

## Estrutura principal

```text
.
├── assets/
│   └── audio/
│       ├── music/catalog.json
│       └── sfx/catalog.json
├── config/
│   ├── config.json
│   └── style.json
├── docs/
│   ├── editorial-direction.md
│   └── audio-search.md
├── engine/
│   ├── assets.py
│   ├── audio.py
│   ├── audio_library.py
│   ├── captions.py
│   ├── editorial.py
│   ├── media_cache.py
│   ├── models.py
│   ├── motion.py
│   ├── music.py
│   ├── pipeline.py
│   ├── renderer.py
│   ├── sfx.py
│   ├── text_fx.py
│   ├── timeline.py
│   └── tts.py
├── episodes/
├── publishing/
├── templates/
│   └── editorial-direction-prompt.md
├── cache/
├── work/
├── output/
├── generate.py
├── prepare_post.py
└── publish.py
```

`cache/`, `work/` e `output/` são regeneráveis e não devem ser usados como fonte permanente de assets.

## `story.json`

Contém roteiro e metadados narrativos do episódio. A narração é formada pela concatenação dos segmentos na ordem declarada.

Exemplo mínimo:

```json
{
  "schema_version": 1,
  "title": "MY EYES — Travis Scott",
  "slug": "my_eyes",
  "target_duration_seconds": 75,
  "segments": [
    {
      "id": "hook",
      "text": "Texto da abertura."
    }
  ]
}
```

O `slug` deve corresponder ao nome da pasta. IDs de segmento devem ser únicos.

## `timeline.json`

Define a edição do episódio. No contrato atual, cada segmento recebe seu shot correspondente e pode ser complementado pelas camadas editoriais suportadas.

Principais recursos atuais:

- background music por profile;
- SFX por `type`;
- vídeo com `source_start_seconds` / `source_end_seconds`;
- motions `push_in`, `pull_out`, `pan_left`, `pan_right`, `hold`;
- transitions `cut` e `crossfade`;
- visual FX `slow_zoom_in`, `slow_zoom_out`, `pan_left`, `pan_right`, `pan_up`, `pan_down`, `punch_zoom`;
- kinetic text `pop_in`, `scale_bounce`, `slide_up`, `fade_pop`;
- highlights ligados ao shot;
- overlays animados quando o asset e o schema atual permitirem.

Exemplo simplificado:

```json
{
  "schema_version": 1,
  "background_music": {
    "profile": "profile_existente",
    "volume": 0.10
  },
  "sfx_cues": [
    {
      "time_seconds": 0.25,
      "type": "type_existente_no_catalogo",
      "volume": 0.20
    }
  ],
  "visual_fx_cues": [
    {
      "start_seconds": 0.10,
      "end_seconds": 0.55,
      "type": "punch_zoom",
      "intensity": 0.55
    }
  ],
  "text_fx_cues": [
    {
      "segment": "hook",
      "offset_seconds": 0.12,
      "duration_seconds": 1.30,
      "text": "ISSO MUDOU\nTUDO",
      "accent_text": "TUDO",
      "animation": "scale_bounce",
      "position": "center",
      "intensity": 0.55
    }
  ],
  "shots": [
    {
      "id": "shot_hook",
      "segment": "hook",
      "asset": "main_video",
      "source_start_seconds": 0,
      "source_end_seconds": 12,
      "motion": "hold",
      "transition_out": "cut"
    }
  ]
}
```

Nunca invente enums ou campos porque parecem editorialmente úteis. Consulte a `main` antes de usar uma capacidade nova.

## Vídeo, imagens e cache remoto

Assets principais são declarados em `assets.json` com ID, arquivo, URL quando remota, crédito, licença e foco quando aplicável.

Exemplo:

```json
{
  "id": "concert_clip",
  "file": "concert_clip.webm",
  "url": "https://host.exemplo/arquivo.webm",
  "credit": "Autor",
  "license": "Licença",
  "focus": {"x": 0.5, "y": 0.5}
}
```

O projeto prioriza arquivos locais quando existem e usa cache para mídia remota quando necessário. URLs remotas precisam ser compatíveis com o downloader e com o tipo de mídia esperado.

Para vídeos, o trecho escolhido precisa ser suficiente para a duração real do shot e para handles de crossfade quando aplicáveis. O renderer não deve usar loop para esconder um trecho insuficiente.

## Background music: external-first

Background music e SFX seguem políticas diferentes.

Para **background music**, quando houver acesso web na etapa de autoria, a estratégia é:

```text
pesquisa externa -> avaliação editorial/técnica -> profile externo aprovado
                                      ↓ se não houver opção adequada
                                 fallback da repo
```

A busca pode usar Openverse e outras fontes/metadados permitidos conforme [`docs/audio-search.md`](docs/audio-search.md). Serviços como Apple, TikTok, YouTube e Spotify podem servir como referência editorial/metadado; previews comerciais não são fonte automática de arquivo para o renderer.

Quando uma trilha externa é aprovada e a `main` suporta o fluxo, o catálogo de música pode receber um profile dedicado com uma entrada `{file, url}`. `timeline.json` continua apontando somente para o nome do profile.

## SFX: somente biblioteca curada

Para **SFX**, a estratégia é diferente e deliberadamente fechada durante a criação do episódio:

```text
assets/audio/sfx/catalog.json -> escolher type existente -> timeline -> cache/render
```

Regras:

- `assets/audio/sfx/catalog.json` é a fonte de verdade;
- o agente usa somente `type` que já exista no catálogo no início da execução;
- não pesquisa novos SFX na web durante a criação do episódio;
- não cria novos `type` por episódio;
- não substitui um SFX curado por outro externo por preferência;
- `timeline.json` referencia somente o `type`, nunca uma URL;
- o catálogo pode conter arquivos locais ou entradas remotas `{file, url}`;
- quando uma entrada remota é usada, o engine baixa para cache e valida o áudio.

A biblioteca curada pode usar URLs diretas de mídia. O render não precisa abrir página HTML de catálogo para descobrir o arquivo quando a entrada já contém URL direta.

SFX devem acompanhar eventos editoriais reais. Em cada shot, avalie se hook, corte, transição, punch zoom, kinetic text, highlight, overlay, reveal, estatística, mudança de assunto, reação, virada ou payoff merecem reforço sonoro.

Não existe regra de `1 SFX por shot` nem quantidade mínima rígida. Também não se deve economizar por hábito: se um beat importante fica melhor com um SFX adequado, prefira usar. O warning acima de 25 é alerta de excesso, não meta.

Types com prefixo `meme_br_` são intervenções completas: quando a regra atual do engine se aplicar, devem tocar integralmente, sem trim editorial do conteúdo falado.

## Visual FX, kinetic text, highlights e overlays

Essas camadas são ferramentas editoriais, não decoração automática.

- **Visual FX:** marque hierarquia e impacto. `punch_zoom` é reservado para momentos fortes; zooms e pans lentos podem sustentar construção/contexto.
- **Kinetic text:** não duplica legenda. Use para hook, contraste, palavra-chave, nome ou estatística curta.
- **Highlight:** reforço curto ligado ao shot; escolha entre highlight e text FX quando ambos diriam exatamente a mesma coisa.
- **Overlay:** acrescenta contexto visual concreto; não use imagem aleatória apenas para aumentar densidade.
- **Motion:** não deixe `hold` por hábito, mas também não adicione movimento artificial a um vídeo que já está visualmente forte.
- **Transition:** escolha `cut` ou `crossfade` conscientemente conforme energia, continuidade e emoção.

Respeite os limites atuais do renderer, inclusive o limite de visual FX por shot quando aplicável.

## Áudio final

O pipeline resolve narração/TTS, background music e SFX, aplica o mix configurado e executa a normalização final de loudness no renderer. Voz deve permanecer dominante; música e SFX reforçam a edição sem prejudicar inteligibilidade.

Caches de áudio ficam separados por finalidade e podem ser apagados; serão reconstruídos quando necessários.

## `sources.txt`

Registre as fontes factuais realmente usadas, páginas de origem dos visuais, créditos/licenças e a fonte de background music externa quando aplicável.

SFX já curados no catálogo não exigem nova pesquisa externa por episódio.

## `post.json` e publicação pública

`post.json` contém capa e metadados por plataforma. Episódios produzidos pela automação são destinados ao **público geral**.

Quando o schema/publisher atual permitir, use:

```json
{
  "schema_version": 1,
  "cover": {
    "headline": "O BEAT MUDA TUDO",
    "source": {"type": "asset", "asset_id": "main_image"}
  },
  "youtube": {
    "title": "Título do Short",
    "description": "Descrição curta e fiel ao vídeo.",
    "hashtags": ["Musica", "Shorts"],
    "privacy_status": "public",
    "category_id": "10"
  },
  "instagram": {
    "caption": "Legenda para Reels.",
    "hashtags": ["Musica", "Reels"],
    "share_to_feed": true,
    "thumb_offset_ms": 1000
  },
  "tiktok": {
    "caption": "Legenda para TikTok.",
    "hashtags": ["Musica"],
    "privacy_level": "PUBLIC_TO_EVERYONE",
    "video_cover_timestamp_ms": 1000,
    "disable_comment": false,
    "disable_duet": false,
    "disable_stitch": false,
    "brand_content_toggle": false,
    "brand_organic_toggle": false,
    "is_aigc": false
  }
}
```

YouTube aceita `private`, `unlisted` e `public` no schema atual, mas a automação editorial deve configurar `public` para episódios destinados ao público geral.

TikTok aceita os níveis definidos pelo publisher atual, incluindo `PUBLIC_TO_EVERYONE`; a automação deve solicitar publicação pública quando isso for suportado pela conta/app. Limitações impostas pela própria plataforma, auditoria ou autorização da conta continuam sendo tratadas pelo publisher e não devem ser mascaradas como sucesso.

Salve hashtags sem `#`; o payload final acrescenta o caractere quando necessário. Quantidades recomendadas podem gerar warnings editoriais sem se tornarem hard errors quando o publisher não exige isso.

## Queue e automação

O episódio deve estar completo e validado antes de criar:

```text
.publish-queue/<slug>.txt
```

A queue é criada por último. A automação não deve executar `publish.py` diretamente nem recriar queue automaticamente quando houver risco de publicação duplicada.

Durante criação normal de episódio, o agente não deve alterar `engine/`, `publishing/`, `config/`, `style.json`, `.github/`, episódios anteriores, queues existentes ou `assets/audio/sfx/catalog.json`.

## Credenciais

Copie `.env.example` para `.env` e configure somente as integrações utilizadas. `.env` permanece fora do Git.

Exemplos de grupos de credenciais:

```text
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_ACCESS_TOKEN=
YOUTUBE_REFRESH_TOKEN=

TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_ACCESS_TOKEN=
TIKTOK_REFRESH_TOKEN=

META_APP_ID=
META_APP_SECRET=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_ACCOUNT_ID=
```

O projeto não simula sucesso. Sem autorização válida, uma publicação live deve falhar de forma explícita ou ser reportada como não verificada/bloqueada conforme o fluxo que a chamou.

## Validação

O pipeline valida, entre outros pontos:

- schema/versionamento dos JSONs;
- slug e estrutura do episódio;
- relação entre segmentos e shots;
- assets e mídia remota;
- motions/transitions;
- trims de vídeo;
- profiles de música e types de SFX;
- trims de SFX;
- visual FX, text FX, highlights e overlays;
- duração/timings;
- compatibilidade com FFmpeg/ffprobe;
- conflitos editoriais que são hard errors no renderer.

Warnings de densidade/repetição devem provocar revisão editorial, mas não devem ser transformados em hard errors sem suporte do código.

## Testes

Execute a suíte:

```bash
python -m unittest discover -s tests -v
```

## Documentação especializada

- [`docs/editorial-direction.md`](docs/editorial-direction.md): contrato de direção/Editor Mode.
- [`templates/editorial-direction-prompt.md`](templates/editorial-direction-prompt.md): prompt usado como referência operacional.
- [`docs/audio-search.md`](docs/audio-search.md): política de busca externa para background music e política fechada de SFX.

Quando qualquer texto acima divergir do código atual, **a `main` continua sendo a fonte da verdade**.
