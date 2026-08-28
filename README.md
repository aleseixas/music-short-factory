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
  `subtitles` e `overlay`. O projeto obtém o executável por meio de
  `imageio-ffmpeg` e faz essa verificação antes do render.

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
│   ├── assets.py       # obtenção, validação e preparo das imagens
│   ├── audio.py        # escolha da voz, timestamps, cache e duração
│   ├── captions.py     # legendas e highlights
│   ├── cli.py          # interface de linha de comando
│   ├── config.py       # leitura e validação das configurações globais
│   ├── episode.py      # criação e carregamento de episódios
│   ├── ffmpeg.py       # execução e verificação do FFmpeg
│   ├── models.py       # modelos de dados do domínio
│   ├── motion.py       # push, pull, pans, hold e easing
│   ├── pipeline.py     # orquestração da geração
│   ├── renderer.py     # render das cenas e composição final
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
concatenação dos segmentos na ordem declarada. `target_duration_seconds` é o
alvo daquele episódio; o limite aceito ao redor dele é configurado globalmente
em `config/config.json`.

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

### `assets.json`

É o catálogo explícito das imagens do episódio:

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
O preparo limita a quantidade de hashtags e a headline a 42 caracteres e seis
palavras. Entradas manuais não vazias nunca são substituídas. A fonte da capa é
sempre explícita: um `asset_id` existente ou um `video_frame` com
`timestamp_seconds`; não existe substituição aleatória.

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
- tolerância entre a duração alvo e a duração real da narração;
- provider de TTS, fallback, voz, velocidade e política de cache;
- volumes da voz e da trilha, fade, limiter, sample rate e bitrate.

`config/style.json` controla a aparência do canal sem mudanças em Python:

- intensidade e velocidade aparente dos movimentos e easing;
- duração padrão dos crossfades;
- fonte, tamanho, cores, contorno, posição e agrupamento das legendas;
- aparência, posição, duração e fade dos highlights.

Mantenha informações de artistas, faixas e roteiro fora desses arquivos globais.

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
Apagar `cache/` é seguro; a narração será gerada novamente quando necessária.

## Diretórios regeneráveis

- `work/`: imagens preparadas, cenas, overlays, legendas e timeline resolvida;
- `cache/`: áudio e metadados reutilizáveis;
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
