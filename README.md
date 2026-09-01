# Music Short Factory

Base modular para gerar vídeos curtos verticais sobre músicas sem alterar o
motor a cada episódio. O conteúdo fica em `episodes/`, as opções técnicas em
`config/config.json` e a identidade visual em `config/style.json`.

O preset atual preserva o resultado aprovado: vídeo 9:16 em 720x1280, 30 fps,
movimentos suaves com easing, crop por ponto focal, cortes secos, crossfades
curtos, legendas com palavra ativa e destaques grandes opcionais.

## Requisitos e instalação

- Python 3.10 ou mais recente;
- acesso à internet quando o Edge TTS ou um asset remoto precisar ser baixado;
- um build do FFmpeg com `libx264` e os filtros `perspective`, `xfade`,
  `subtitles`, `overlay` e `loudnorm`. O projeto obtém o executável por meio de
  `imageio-ffmpeg` e faz essa verificação antes do render.
- `ffprobe` no `PATH` (ou indicado por `FFPROBE_BINARY`) quando o episódio usa
  assets de vídeo ou baixa áudio remoto.

Na raiz do projeto:

```bash
python -m pip install -r requirements.txt
```

Opcionalmente, crie e ative um ambiente virtual antes da instalação:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Uso rápido

Crie os templates de um episódio novo:

```bash
python new_episode.py nome_do_video
```

O nome deve usar somente letras minúsculas, números, `_` ou `-`. Por exemplo:

```bash
python new_episode.py my_eyes
```

O comando cria:

```text
episodes/my_eyes/
├── story.json
├── timeline.json
├── assets.json
├── sources.txt
├── post.json
└── assets/
```

Edite os arquivos, adicione as imagens necessárias e então gere o
vídeo:

```bash
python generate.py my_eyes
```

Para gerar o episódio incluído na base:

```bash
python generate.py through_the_wire
```

O arquivo final é gravado em `output/<slug>.mp4`, por exemplo
`output/through_the_wire.mp4`. `new_episode.py` não sobrescreve um episódio que
já existe.

Prepare os metadados e a capa depois do render:

```bash
python prepare_post.py my_eyes
```

Isso valida `post.json`, preserva valores editados manualmente e gera
`output/my_eyes_cover.jpg`. Para revisar uma publicação sem enviar nada:

```bash
python publish.py my_eyes --platform youtube --dry-run
python publish.py my_eyes --platform instagram --dry-run
python publish.py my_eyes --platform tiktok --dry-run
python publish.py my_eyes --platform all --dry-run
```

O dry-run é o comportamento padrão mesmo sem a flag. Somente `--live` autoriza
chamadas reais às APIs oficiais.

## Estrutura do projeto

```text
.
├── engine/
│   ├── assets.py       # obtenção, validação e preparo dos assets visuais
│   ├── audio.py        # escolha da voz, timestamps, cache e duração
│   ├── audio_library.py # leitura comum dos catálogos de áudio
│   ├── captions.py     # legendas e highlights
│   ├── cli.py          # interface de linha de comando
│   ├── config.py       # leitura e validação das configurações globais
│   ├── episode.py      # criação e carregamento de episódios
│   ├── editorial.py    # validação determinística da direção editorial
│   ├── ffmpeg.py       # execução e verificação do FFmpeg
│   ├── media_cache.py  # download e cache comuns para mídia remota
│   ├── models.py       # modelos de dados do domínio
│   ├── motion.py       # push, pull, pans, hold e easing
│   ├── music.py        # resolução da biblioteca de música
│   ├── pipeline.py     # orquestração da geração
│   ├── renderer.py     # render das cenas e composição final
│   ├── sfx.py          # resolução da biblioteca de efeitos
│   ├── timeline.py     # timeline sincronizada e contagem de frames
│   ├── tts.py          # interface e providers de síntese de voz
│   └── utils.py        # utilitários e validações comuns
├── config/
│   ├── config.json     # render, caminhos, áudio, TTS e mixagem
│   └── style.json      # identidade visual do canal
├── episodes/
│   └── through_the_wire/
│       ├── story.json
│       ├── timeline.json
│       ├── assets.json
│       ├── sources.txt
│       ├── post.json
│       └── assets/
├── publishing/
│   ├── base.py          # interface Publisher e resultados
│   ├── credentials.py   # leitura segura de ambiente, sem logar secrets
│   ├── metadata.py      # schema, validação e previews por plataforma
│   ├── cover.py         # capa vertical a partir de asset ou frame
│   ├── youtube.py       # YouTube Data API
│   ├── instagram.py     # Instagram Content Publishing API
│   └── tiktok.py        # TikTok Content Posting API
├── cache/              # cache regenerável, criado em runtime
├── work/               # intermediários regeneráveis, criado em runtime
├── output/             # vídeos finais, criado em runtime
├── tests/
│   └── reference/      # referência visual de regressão
├── generate.py
├── new_episode.py
├── prepare_post.py
├── publish.py
├── .env.example
├── requirements.txt
└── README.md
```

O diretório `engine/` não contém texto nem regras específicas de artistas. Um
novo tema é criado apenas com dados dentro de `episodes/<slug>/`.

## Conteúdo de um episódio

### `story.json`

Contém somente o roteiro e os metadados do episódio. A narração é formada pela
concatenação dos segmentos na ordem declarada. `target_duration_seconds` é uma
referência editorial; a faixa de aviso ao redor dele é configurada globalmente
em `config/config.json`. Se a narração ficar fora dessa faixa, o pipeline avisa
e usa a duração real sem cortar, acelerar ou gerar a voz novamente.

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

O `slug` deve ser igual ao nome da pasta do episódio. IDs de segmento devem ser
únicos.

### `timeline.json`

Define a edição: qual asset acompanha cada segmento, o movimento, a transição e
o destaque opcional. Deve existir um plano para cada segmento de `story.json`,
na mesma ordem.

```json
{
  "schema_version": 1,
  "background_music": {
    "profile": "latin_pop_uplifting",
    "volume": 0.12
  },
  "sfx_cues": [
    {
      "time_seconds": 0.3,
      "type": "impact",
      "volume": 0.5
    }
  ],
  "visual_fx_cues": [
    {
      "start_seconds": 0,
      "end_seconds": 3,
      "type": "slow_zoom_in",
      "intensity": 0.5
    }
  ],
  "text_fx_cues": [
    {
      "start_seconds": 0.2,
      "end_seconds": 1.8,
      "text": "ISSO MUDOU TUDO",
      "accent_text": "TUDO",
      "animation": "pop_in",
      "position": "center",
      "intensity": 0.5
    }
  ],
  "overlay_cues": [
    {
      "start_seconds": 10.2,
      "end_seconds": 12.0,
      "asset": "billboard_logo",
      "animation": "pop_in",
      "position": "center",
      "scale": 0.38,
      "opacity": 1.0
    }
  ],
  "shots": [
    {
      "id": "shot_hook",
      "segment": "hook",
      "asset": "travis_stage",
      "motion": "push_in",
      "transition_out": "cut",
      "highlight": {
        "text": "PARECE DUAS MÚSICAS EM UMA",
        "start_seconds": 0.2,
        "duration_seconds": 1.6
      }
    }
  ]
}
```

Movimentos aceitos: `push_in`, `pull_out`, `pan_left`, `pan_right` e `hold`.
Transições aceitas: `cut` e `crossfade`. O ponto focal do asset pode ser
sobrescrito em um plano com `"focus": {"x": 0.5, "y": 0.4}`.

Um shot cujo asset termina em `.mp4`, `.mov` ou `.webm` usa vídeo. Ele
aceita `source_start_seconds` opcional (padrão `0`) e `source_end_seconds`
opcional:

```json
{
  "id": "shot_concert",
  "segment": "concert",
  "asset": "concert_clip",
  "source_start_seconds": 12.5,
  "source_end_seconds": 18.0,
  "motion": "hold",
  "transition_out": "crossfade"
}
```

A narração continua definindo a duração do shot. O trecho da fonte precisa ser
suficiente também para o handle de um crossfade de saída; não há loop nem uso do
áudio original. O FFmpeg normaliza FPS e faz scale com aspect ratio preservado,
seguido de crop central para o quadro vertical.

Os cinco campos editoriais são opcionais. `background_music` resolve um profile
do catálogo e mistura a trilha com a narração; `sfx_cues` posiciona efeitos do
catálogo; `visual_fx_cues` aplica movimentos de câmera; `text_fx_cues`
cria tipografia cinética; e `overlay_cues` insere imagens locais. Sem essas
opções, o render mantém exatamente o fluxo anterior. Tempos usam segundos,
volumes e intensidades ficam entre `0` e `1`, e `profile`/`type` usam
identificadores em letras minúsculas.

#### Efeitos visuais por intervalo

Cada item de `visual_fx_cues` usa `start_seconds` e `end_seconds` como tempos
globais do vídeo. `intensity` é opcional e usa `0.5` por padrão. Ela representa
uma intensidade artística abstrata entre `0` e `1`, não uma porcentagem direta
de zoom. O renderer a converte para estes limites seguros:

O início é alinhado ao primeiro frame cuja timestamp não antecede
`start_seconds`; `end_seconds` permanece exclusivo.

- `slow_zoom_in`: aproximação contínua de 3% a 12%;
- `slow_zoom_out`: recuo contínuo equivalente, começando 3% a 12% aproximado;
- `pan_left`: viewport da direita para a esquerda (bias alto para baixo);
- `pan_right`: viewport da esquerda para a direita (bias baixo para alto);
- `pan_up`: viewport de baixo para cima (bias alto para baixo);
- `pan_down`: viewport de cima para baixo (bias baixo para alto);
- `punch_zoom`: aproximação rápida com pico de 8% a 15% e assentamento de 4% a
  10%; a subida ocupa os primeiros 45% do intervalo.

Os pans usam zoom de segurança entre 4% e 8% e deslocamento normalizado entre
`0.20` e `0.60`, distribuído ao redor do ponto focal. Assim, o crop permanece
dentro da imagem e não expõe bordas vazias.

Uma cue que atravessa boundaries é dividida pelos shots afetados, preservando o
progresso global do movimento. Por exemplo, uma cue de `2s` a `7s` com boundary
em `4s` executa 0–40% no primeiro shot e 40–100% no segundo. Se a cue começar no
ou após o fim do vídeo, ela é validada, ignorada e gera um warning. Uma cue que
termina depois do vídeo é limitada ao último frame.

Nesta primeira etapa, qualquer sobreposição entre cues é rejeitada com erro
claro. Os intervalos são semiabertos (`[start_seconds, end_seconds)`), portanto o
fim de uma cue pode encostar exatamente no início da próxima. Cada shot aceita no
máximo uma cue explícita.

Nos shots afetados, a cue explícita controla a câmera e substitui o campo legado
`motion`. A pose inicial fica congelada antes do intervalo e a pose final depois
dele, inclusive no handle usado pelo `crossfade`. Shots sem cue continuam usando
`motion` (`push_in`, `pull_out`, `pan_left`, `pan_right` ou `hold`) exatamente como
antes.

#### Tipografia cinética por intervalo

`text_fx_cues` é uma camada editorial opcional. Cada cue usa exatamente um dos
dois modos de timing:

- absoluto legado: `start_seconds` inclusivo + `end_seconds` exclusivo;
- relativo ao segmento: `segment` + `offset_seconds` + `duration_seconds`.

No modo relativo, o engine espera a TTS, constrói a timeline real e ancora a cue
no início real do único shot associado ao segmento. O offset deve ser não
negativo, a duração deve ser positiva e a cue inteira precisa caber no segmento;
não há corte silencioso nem uso de `target_duration_seconds`. Campos absolutos e
relativos não podem ser misturados na mesma cue.

```json
{
  "segment": "chart",
  "offset_seconds": 0.4,
  "duration_seconds": 1.8,
  "text": "Nº 1\nNO BRASIL",
  "accent_text": "Nº 1",
  "animation": "scale_bounce",
  "position": "center",
  "intensity": 0.55
}
```

Cada item também requer `text` e `animation`; aceita texto multilinha,
`accent_text` (que deve aparecer em `text`), `position: "center"` e `intensity`
entre `0` e `1` (padrão `0.5`). As animações disponíveis são `pop_in`,
`scale_bounce`, `slide_up` e `fade_pop`.

As cues não podem se sobrepor. A camada fica no centro seguro do quadro, os
highlights continuam no topo e as legendas palavra a palavra são aplicadas por
último no terço inferior. Ela não altera a duração, FPS, resolução ou áudio e pode
coexistir com `visual_fx_cues`, música e SFX.

#### Overlays gráficos por intervalo

`overlay_cues` insere uma imagem local temporária sobre o vídeo. `asset` precisa
ser um ID já declarado em `assets.json`; caminhos arbitrários não são aceitos e
arquivos ausentes falham antes da geração de voz e do render. Para overlays, o
pipeline aceita PNG (inclusive transparência), JPG/JPEG e WEBP e não baixa o
arquivo mesmo quando o asset possui `url`.

Cada cue usa tempos globais semiabertos, `animation`, `position`, `scale` opcional
entre `0.10` e `0.80` (padrão `0.38`) e `opacity` opcional entre `0` e `1` (padrão
`1`). As animações são `pop_in`, `scale_bounce`, `slide_up`, `slide_left`,
`slide_right` e `fade_in`; todas usam easing suave e fade-out curto. As posições
são `center`, `upper_center`, `lower_center`, `left` e `right`.

A imagem usa contain, preserva seu aspect ratio e é limitada a uma área segura:
24% do topo ficam reservados para highlights, 22% da base para subtitles e 7,5%
de cada lateral. O pico de `scale_bounce` e o deslocamento curto dos slides também
ficam dentro desses limites. Nesta primeira versão, cues simultâneas são rejeitadas
com erro claro; intervalos que apenas se encostam são permitidos.

A ordem final é: imagem do shot e `visual_fx_cues`, overlay gráfico, `text_fx_cues`,
highlight e subtitles. Música e SFX permanecem no estágio posterior de áudio. Cada
ramo gráfico é recortado ao próprio intervalo no FFmpeg, sem render frame a frame
em Python e sem alterar FPS, resolução ou duração.

#### Direção editorial automática

O renderer não escolhe efeitos. O contrato para o GPT/agente externo, a ordem de
autoria, os editorial beats, budgets, conflitos e um exemplo completo ficam em
[`docs/editorial-direction.md`](docs/editorial-direction.md). Um prompt copiável
fica em
[`templates/editorial-direction-prompt.md`](templates/editorial-direction-prompt.md).
A tarefa agendada do ChatGPT também pode consultar áudio externo diretamente pela
web seguindo [`docs/audio-search.md`](docs/audio-search.md), sempre com fallback
para os catálogos locais e sem introduzir chamadas de busca no renderer.

Depois que a duração real é conhecida, `engine/editorial.py` rejeita conflitos
que quebrariam o schema/renderer e emite warnings determinísticos para excesso,
clusters, repetição, kinetic text longo, punch zoom consecutivo e duplicação de
texto entre highlight e `text_fx`. Episódios antigos e episódios sem efeitos
continuam válidos; não existe chamada local a LLM.

### `assets.json`

É o catálogo explícito dos assets visuais do episódio:

```json
{
  "schema_version": 1,
  "assets": [
    {
      "id": "travis_stage",
      "file": "travis_stage.jpg",
      "url": "https://exemplo.com/travis_stage.jpg",
      "credit": "Nome do autor",
      "license": "Licença e condições de uso",
      "focus": {
        "x": 0.5,
        "y": 0.4
      }
    }
  ]
}
```

Se `episodes/<slug>/assets/<file>` já existir, ele é reutilizado. Se estiver
ausente, somente a URL declarada é usada para o download. O arquivo é validado
como imagem antes de entrar no render; uma falha interrompe a geração com uma
mensagem clara. O motor nunca escolhe uma imagem aleatória como substituição.

Para um vídeo principal, declare o mesmo formato de asset. O arquivo local tem
prioridade; a URL só é usada quando ele estiver ausente:

```json
{
  "id": "concert_clip",
  "file": "concert_clip.mp4",
  "url": "https://cdn.exemplo.com/concert_clip.mp4",
  "credit": "Autor",
  "license": "Licença",
  "focus": {"x": 0.5, "y": 0.5}
}
```

O tipo é inferido pela extensão. MP4, MOV e WEBM são aceitos. Quando o arquivo
do episódio não existe, uma URL HTTP(S) direta com a mesma extensão é baixada
para `cache/video/<slug>/<file>`; um arquivo não vazio já presente nesse cache
evita novo download. Antes do render, `ffprobe` exige stream de vídeo, duração,
resolução e FPS válidos tanto para arquivos locais quanto remotos. Vídeos
continuam proibidos em `overlay_cues` nesta etapa.

### `sources.txt`

Registre as fontes factuais do roteiro, as páginas de origem das imagens, os
créditos e as licenças. O arquivo é obrigatório para manter cada episódio
auditável, mas seu conteúdo não é renderizado.

### `post.json`

Contém somente os metadados editáveis de publicação e a escolha da capa. O
arquivo não é lido pelo renderer e pode ser alterado sem gerar o vídeo outra
vez.

```json
{
  "schema_version": 1,
  "cover": {
    "headline": "O BEAT MUDA TUDO",
    "source": { "type": "asset", "asset_id": "main_image" }
  },
  "youtube": {
    "title": "A FRASE DE IMPACTO — Nome oficial da música",
    "description": "Descrição curta e fiel ao vídeo.",
    "hashtags": ["Musica", "Shorts"],
    "privacy_status": "private"
  },
  "instagram": {
    "caption": "Legenda própria para Reels.",
    "hashtags": ["Musica", "Reels"],
    "share_to_feed": true,
    "thumb_offset_ms": 1000
  },
  "tiktok": {
    "caption": "Legenda curta para TikTok.",
    "hashtags": ["Musica"],
    "privacy_level": "SELF_ONLY",
    "video_cover_timestamp_ms": 1000
  }
}
```

Salve hashtags sem `#`; o caractere é acrescentado somente ao payload final.
As quantidades recomendadas de hashtags e o tamanho recomendado da headline
(42 caracteres e seis palavras) geram warnings editoriais, sem interromper o
preparo ou a publicação. Limites obrigatórios de texto são bloqueados somente
por meio do publisher da plataforma correspondente. Entradas manuais não vazias
nunca são substituídas. A fonte da capa é sempre explícita: um `asset_id` existente
ou um `video_frame` com `timestamp_seconds`; não existe substituição aleatória.
No YouTube, a quantidade de hashtags continua livre enquanto as tags respeitarem
o limite técnico agregado de 500 caracteres.

## Publicação e credenciais

Copie `.env.example` para `.env` e preencha somente as integrações utilizadas:

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
META_GRAPH_API_VERSION=
INSTAGRAM_API_HOST=graph.facebook.com
```

`.env` está no `.gitignore`, tokens nunca são impressos e credenciais ausentes
não afetam `generate.py`, `prepare_post.py` ou qualquer dry-run. Upload real usa
opt-in explícito:

```bash
python publish.py my_eyes --platform youtube --live
```

Client ID e secret identificam o aplicativo, mas não autorizam uma conta
sozinhos. Faça o consentimento OAuth na plataforma, obtenha um access token ou
refresh token com os escopos indicados e coloque-o somente em `.env`. Os
adapters renovam tokens de YouTube e TikTok quando um refresh token é fornecido;
eles não inventam autorização nem simulam uma sessão aprovada.

- YouTube usa OAuth de usuário, upload resumível, status, privacidade e
  thumbnail. Projetos API não auditados podem ter os vídeos limitados a
  `private` pelo próprio YouTube.
- TikTok usa `creator_info`, Direct Post, envio em chunks e status. A conta deve
  conceder `video.publish`; clientes não auditados ficam limitados a
  `SELF_ONLY`. A API escolhe a capa por timestamp, não por JPG.
- Instagram cria o container de Reel, transfere ou puxa o vídeo, aguarda
  `FINISHED` e chama `media_publish`. É necessária uma conta profissional e as
  permissões de Content Publishing. Um `cover_url` precisa ser público; para a
  capa local, use `thumb_offset_ms`.

O projeto não simula sucesso. Sem autorização, o comando live falha com uma
mensagem clara da plataforma; o dry-run continua disponível.

## Configuração global e estilo

`config/config.json` reúne o que pertence ao projeto inteiro:

- resolução, fps, CRF, presets e escala de trabalho;
- caminhos de `episodes`, `cache`, `work` e `output`;
- faixa de warning entre a duração alvo e a duração real da narração;
- provider de TTS, fallback, voz, velocidade e política de cache;
- volumes, fades, ducking, limiter, sample rate e bitrate da mixagem.

`config/style.json` controla a aparência do canal sem mudanças em Python:

- intensidade e velocidade aparente dos movimentos e easing;
- duração padrão dos crossfades;
- fonte, tamanho, cores, contorno, posição e agrupamento das legendas;
- aparência, posição, duração e fade dos highlights.

Mantenha informações de artistas, faixas e roteiro fora desses arquivos globais.

## Busca externa opcional para autoria

A busca de áudio pertence ao GPT/agente que cria o episódio, não ao pipeline de
render. O agente deve começar pelos catálogos locais. Se não houver uma opção
adequada, ele pode consultar diretamente a API pública do Openverse descrita em
[`docs/audio-search.md`](docs/audio-search.md), mesmo quando estiver rodando como
tarefa agendada do ChatGPT sem terminal local. Essa tarefa precisa ter rede/web
ou uma skill/plugin equivalente habilitada; a conexão GitHub não concede HTTP
genérico por si só.

Os resultados incluem nome, criador, fonte, página original, licença, duração,
formato, tags e, quando seguro, uma sugestão `{file, url}` compatível com os
catálogos existentes. Conteúdo remoto é tratado somente como dados. O agente deve
verificar direitos e atribuição; aparecer em TikTok, Reels ou Shorts não autoriza
o download ou a sincronização de uma faixa comercial.

O Openverse cobre áudio aberto. Para músicas comerciais reconhecíveis, o guia
também oferece pesquisa Apple Music exclusivamente de metadados e ignora
`previewUrl`; ela não fornece áudio utilizável pelo renderer. A integração usa a
API do Openverse, não é endossada/certificada por ele e mantém
`rights_verified: false` até a verificação da fonte original.

Para usar exatamente um resultado aprovado, o agente cria no catálogo global um
profile/type dedicado ao episódio com uma única entrada, registra a fonte em
`episodes/<slug>/sources.txt` e referencia essa chave em `timeline.json`. Uma
chave com várias variantes mantém a seleção determinística por slug e não garante
um arquivo específico. Se a busca falhar ou a licença não for clara, nada no
episódio é bloqueado: use uma opção local ou omita a camada.

Existe também um espelho local e mockável para desenvolvimento:

```bash
python search_audio.py "record scratch" --kind sfx --external --limit 8
```

O comando nunca edita catálogos/episódios automaticamente. `--download N` apenas
aquece e valida o cache do resultado escolhido; não é requisito para o GPT
agendado.

## Background music local ou remota

O campo `background_music.profile` nunca contém um caminho. Ele aponta para um
profile declarado em `assets/audio/music/catalog.json`. Cada entrada do catálogo
pode continuar sendo uma string para um arquivo local ou usar um objeto com
`file` e `url`.

Por exemplo, coloque uma trilha royalty-free em:

```text
assets/audio/music/latin_pop_uplifting/track-01.wav
```

Depois registre-a no catálogo:

```json
{
  "schema_version": 1,
  "profiles": {
    "latin_pop_uplifting": [
      "latin_pop_uplifting/track-01.wav",
      {
        "file": "latin_pop_uplifting/track-02.mp3",
        "url": "https://cdn.exemplo.com/track-02.mp3"
      }
    ]
  }
}
```

Um profile pode listar vários arquivos. A escolha é determinística para cada
slug de episódio. Para uma entrada em objeto, o arquivo em
`assets/audio/music/<file>` sempre tem prioridade. Se ele não existir, a URL
HTTP(S) direta é baixada para `cache/music/<file>`. Um cache não vazio é
reutilizado; o áudio baixado tem sua duração validada antes do render. Profiles
ausentes, entradas sem arquivo local nem URL, downloads vazios e mídia inválida
interrompem o render com erro claro.

No mix por episódio, `background_music.volume` é o volume base. A configuração
global aplica fade-in de `0.75s`, fade-out de `1.25s` e ducking por sidechain
com threshold `0.02`, ratio `8:1`, attack de `20ms` e release de `350ms`. A
trilha entra em loop ou é cortada para terminar junto com o último frame do
vídeo. A configuração global legada `mix.music_file` permanece disponível e
inalterada para episódios sem profile.

## SFX local ou remoto

O campo `sfx_cues[].type` aponta para um tipo declarado em
`assets/audio/sfx/catalog.json`. Coloque cada efeito royalty-free dentro de
`assets/audio/sfx/`; por exemplo:

```text
assets/audio/sfx/whoosh/whoosh-01.wav
```

Registre o caminho relativo no catálogo. Assim como em background music, cada
entrada pode ser a string legada ou um objeto com `file` e `url`:

```json
{
  "schema_version": 1,
  "types": {
    "impact": [
      "impact/impact-01.wav",
      {
        "file": "impact/impact-02.wav",
        "url": "https://cdn.exemplo.com/impact-02.wav"
      }
    ],
    "whoosh": [
      "whoosh/whoosh-01.wav"
    ],
    "pop": [
      "pop/pop-01.wav"
    ],
    "riser": [
      "riser/riser-01.wav"
    ],
    "camera_shutter": [
      "camera_shutter/camera-01.wav"
    ]
  }
}
```

Os types disponíveis são exatamente as chaves atuais do catálogo, que deve ser
relido sempre que a biblioteca mudar. São aceitos os mesmos formatos da
biblioteca de música: AAC, FLAC, M4A, MP3, OGG, Opus e WAV. O arquivo em
`assets/audio/sfx/<file>` tem prioridade e, quando ausente, a URL HTTP(S) direta
é baixada para `cache/sfx/<file>` e validada. URLs usadas como string no lugar de
`file`, caminhos absolutos, `..` e escapes por symlink continuam rejeitados. Se
houver mais de um arquivo no mesmo type, a escolha é reproduzível e varia
deterministicamente conforme o slug, o type e a posição da cue entre as cues
daquele type.

Exemplo mínimo no `timeline.json`:

```json
{
  "schema_version": 1,
  "sfx_cues": [
    {
      "time_seconds": 12.4,
      "type": "cinematic_piano",
      "volume": 0.10,
      "source_start_seconds": 1.5,
      "duration_seconds": 2.5
    }
  ],
  "shots": [
    {
      "id": "shot_hook",
      "segment": "hook",
      "asset": "main_image",
      "motion": "hold",
      "transition_out": "cut"
    }
  ]
}
```

`time_seconds` posiciona o início do efeito com precisão de amostra no mix do
FFmpeg. `source_start_seconds` é opcional e usa `0` por padrão;
`duration_seconds` também é opcional e limita quanto do arquivo deve tocar. O
início precisa ser não negativo, a duração precisa ser positiva e a janela
solicitada não pode ultrapassar a duração real da variante escolhida no catálogo.
O recorte é aplicado com `atrim`, sem modificar o arquivo original.

Efeitos podem se sobrepor. A parte que ultrapassar o último frame é cortada e
nunca prolonga o vídeo; uma cue no ou depois do fim é ignorada com um warning
claro. Sem os dois campos de recorte, o grafo e o comportamento permanecem os
mesmos dos episódios antigos.

A ordem do áudio é: a voz controla o ducking da música; depois voz, música já
atenuada e todos os SFX entram no mix final; por último é aplicado o limiter.
Os SFX nunca são usados como sinal de controle do ducking. Sem música, o mix é
voz + SFX; sem SFX, os caminhos existentes de voz e de voz + música permanecem
inalterados.

Depois desse mix completo, o renderer executa o `loudnorm` do FFmpeg em dois
passes, com alvo de `-15 LUFS`, true peak máximo de `-1 dBTP` e LRA de `11 LU`.
O filtro reserva `0,5 dB` de margem antes do AAC para que o MP4 codificado
respeite esse teto. O primeiro passe apenas mede o áudio final; o segundo aplica
as medições antes
da codificação AAC. Ducking, volumes relativos e dinâmica permanecem a montante
da normalização, nenhum arquivo-fonte é modificado e uma falha em qualquer passe
interrompe o render com erro claro sem promover a saída parcial.

## Voz, SRT, TTS e cache

Para usar uma narração própria, coloque os arquivos no episódio:

```text
episodes/nome_do_video/assets/custom_voice.mp3
episodes/nome_do_video/assets/custom_voice.srt
```

O SRT precisa corresponder ao texto completo de `story.json`. Com o par MP3 +
SRT, o motor usa a narração e os timestamps personalizados para sincronizar
timeline, legendas e palavra ativa.

A seleção segue esta ordem:

1. `custom_voice.mp3` com `custom_voice.srt`;
2. provider definido em `tts.provider`;
3. provider opcional definido em `tts.fallback_provider`.

Existe também um modo de compatibilidade para MP3 sem SRT. Quando
`tts.allow_estimated_custom_timings` está habilitado, o áudio personalizado é
usado com tempos estimados e o motor mostra um aviso. Desabilite essa opção para
seguir ao provider configurado quando não houver SRT. A base vem com essa opção
desabilitada, preservando a prioridade literal MP3+SRT → provider → fallback.

O provider incluído é o Edge TTS. A interface `TTSProvider` mantém síntese e
renderer desacoplados para que outro provider possa ser registrado sem alterar
a lógica de renderização.

Áudios gerados ficam em `cache/audio/<slug>/`. O cache só é reaproveitado quando
roteiro, provider, opções da voz, áudio e timestamps continuam compatíveis.
Assets remotos ficam separados em `cache/video/<slug>/`, `cache/music/` e
`cache/sfx/`, preservando o caminho relativo declarado em `file`. As três
categorias usam o mesmo mecanismo de download atômico: a URL precisa apontar
diretamente para um arquivo da mesma extensão, respostas vazias são rejeitadas e
um cache válido não é baixado novamente.

Apagar `cache/` é seguro; a narração será gerada novamente e as mídias remotas
serão baixadas outra vez quando necessárias.

## Diretórios regeneráveis

- `work/`: imagens preparadas, cenas, overlays, legendas e timeline resolvida;
- `cache/`: narração, vídeos, músicas, SFX e metadados reutilizáveis;
- `output/`: vídeos finais.

Os três diretórios são criados automaticamente. Podem ser apagados sem quebrar
o projeto; apagar `output/` também remove, naturalmente, os vídeos já gerados.
Não coloque assets-fonte dentro deles.

## Validação e erros

Antes e durante a geração, o pipeline valida os JSONs e suas versões, arquivos
obrigatórios, slug, segmentos, ordem da timeline, referências de assets,
movimentos, transições, pontos focais, resolução 9:16, suporte do FFmpeg,
integridade das imagens, existência do áudio e sua duração. A timeline é
resolvida em frames inteiros para preservar a contagem total e evitar perda de
frames.

Quando algo estiver inválido, o comando termina com `ERRO:` e identifica o
arquivo, plano, asset ou configuração responsável.

## Testes e regressão visual

Execute toda a suíte a partir da raiz:

```bash
python -m unittest discover -s tests -v
```

Os testes cobrem carregamento e criação de episódios, validação dos JSONs,
assets ausentes, timeline, duração, seleção de TTS, movimentos, legendas e
contagem de frames.

Quando presente, `tests/reference/through_the_wire_v7.mp4` é a referência visual
aprovada. Preserve esse arquivo ao limpar outputs e use-o para verificar que
mudanças estruturais não alteraram o estilo do render.
