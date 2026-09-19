# Artist vibe em episódios HERO

A artist vibe é um conjunto opcional de defaults editoriais em [`config/artist_vibes.json`](../config/artist_vibes.json), resolvido por [`engine/artist_vibe.py`](../engine/artist_vibe.py). O mesmo objeto percorre busca, ranking, pacing, texto e capa. O renderer continua determinístico; não existe chamada nova de LLM ou serviço de análise visual.

Os profiles incluídos são Taylor Swift, Elton John, Travis Scott e Luan Santana. Eles representam direções editoriais reutilizáveis e ajustáveis, não regras permanentes sobre um artista.

## Ativação e compatibilidade

Em `story.json`:

```json
{
  "artist": "Taylor Swift",
  "artist_vibe": "taylor_swift",
  "segments": [
    {"id": "hook", "text": "Texto do hook.", "visual_role": "hook"},
    {"id": "memory", "text": "Texto íntimo.", "visual_role": "intimacy"},
    {"id": "payoff", "text": "Texto do payoff.", "visual_role": "payoff"}
  ]
}
```

O trecho mostra somente os campos novos; os campos obrigatórios normais de story permanecem necessários. Sem `artist_vibe` nem `visual_direction`, o episódio segue o comportamento anterior. `artist_vibe: false` desativa a camada. `artist_vibe: "auto"` procura aliases conhecidos em `artist`, ou no título se `artist` estiver ausente; artista desconhecido ou ambíguo não recebe um profile adivinhado. Um profile explícito inválido produz erro de validação.

`visual_direction` permite sobrescrever partes do profile, ou definir uma direção própria sem profile. Objetos são mesclados por chave; listas são substituídas. Por exemplo:

```json
{
  "artist_vibe": "taylor_swift",
  "visual_direction": {
    "pacing": {"payoff_seconds": 6.5},
    "emotional_arc": {
      "payoff": {
        "mood": ["epic", "sentimental"],
        "search_terms": ["All Too Well crowd singing emotional wide"]
      }
    }
  }
}
```

`visual_role` aceita `hook`, `context`, `intimacy`, `build` e `payoff`. Sem papel explícito, o primeiro segmento vira hook, o último payoff e os intermediários context. O papel visual é independente de `delivery` de voz.

## Contrato da direção

| Campo | Uso |
| --- | --- |
| `mood` | Intenção emocional para a seleção. |
| `pacing` | Duração alvo por papel, mínimo e máximo dos planos. |
| `visual_motifs` | Memória, carta, eras, ligação com fãs etc. |
| `preferred_shot_types` | Tipos de planos a procurar e valorizar. |
| `emotional_arc` | Mood, planos e termos de busca por papel. |
| `what_to_avoid` | Termos negativos para reduzir escolhas incompatíveis. |
| `search_terms` | Expansão quando o papel não tem termos próprios. |
| `text_guidance` | Briefing de escrita dos textos; não reescreve a copy sozinho. |
| `editing` | Motion, transição, animação/intensidade do texto e crossfade. |
| `cover` | Cor do texto, destaque, fundo e família tipográfica. |

## Busca e seleção

`search_visual.py` aceita `--episode`, `--segment`, `--visual-role` e `--narration`. A consulta factual original é preservada e ganha algumas expansões com a direção e o papel. Termos de `what_to_avoid` não são adicionados como buscas positivas.

Cada candidato pode acrescentar `visual_metadata`:

```json
{
  "visual_metadata": {
    "mood": ["intimate", "emotional"],
    "motifs": ["warm light", "memory"],
    "shot_types": ["acoustic performance"],
    "story_roles": ["hook"],
    "emotional_strength": 88,
    "cinematic_value": 82,
    "storytelling_value": 86,
    "narration_fit": 90,
    "review_notes": "Foto inspecionada: artista reconhecível ao violão e contraluz quente."
  }
}
```

As cinco dimensões são `vibe_match` (30%), `emotional_strength` (20%), `cinematic_value` (15%), `storytelling_value` (15%) e `narration_fit` (20%). Scores explícitos usam 0–100; emoção, cinema e storytelling dependem de avaliação editorial documentada. Vibe e adequação à narração também podem usar correspondência lexical de nomes, descrições e tags, identificada como evidência heurística. Uma query usada na busca não serve como prova do conteúdo encontrado.

Dimensões desconhecidas permanecem `null` e não recebem qualidade inventada. A influência total é limitada a ±24 pontos sobre a avaliação técnica, com penalidade por termos a evitar. Nos resolvedores, a hierarquia factual usa `semantic_fit`: anote todo o conjunto comparável para que uma plateia genérica com boa estética não substitua um registro exato pela nota de vibe. Candidatos legados sem `semantic_fit` mantêm a pontuação técnica; não há garantia da mesma hierarquia em um conjunto parcialmente anotado. Duração, formato, inspeção e controles de repetição continuam valendo. O ranking também atua antes do limite de candidatos inspecionados.

`visual_candidates.json` pode indicar `visual_role` e `narration_text` no slot. Os resolvedores associam o ID do slot ao asset/segmento pela timeline; um campo `segment` no slot não é usado como override por eles. Resultados e relatórios incluem as dimensões, a origem da evidência, os matches, o papel e o ajuste, permitindo revisar por que um candidato foi valorizado.

## Edição e capa

Com direção ativa, os campos omitidos de shots e textos herdam os defaults. Uma transição ou motion escrito no episódio continua valendo. Os textos mantêm a copy autoral e recebem tipografia, paleta, animação e intensidade coerentes. A duração de crossfade acompanha a direção. `smart_visual_pacing` usa o papel do segmento para favorecer respiração na intimidade e no payoff, em vez de aplicar o mesmo tempo a todos os planos.

Direção ativa também preserva por padrão os trims autorais de vídeo: a análise técnica não deve trocar um instante semanticamente escolhido por outro mais agitado. Uma opção explícita na timeline prevalece, inclusive `smart_visual_pacing.enabled: false` e `preserve_authored_video_trims: false`.

A capa compartilha a paleta/fonte e usa a fonte explícita ou padrão do post. A escolha autoral do asset é preservada; não há um ranking novo de assets exclusivo para a capa. Quando a capa usa o vídeo do hook sem timestamp explícito, herda o início do trim selecionado. A seleção automática de frame dentro de um take mede qualidade técnica, não reconhece expressão ou conteúdo emocional quadro a quadro. O frame deve vir de um take previamente adequado. O breve intro de capa e o modo de abertura com vídeo real mantêm seus contratos existentes.

## Profiles incluídos

O catálogo inicial traz presets para Taylor Swift, Elton John, Travis Scott e Luan Santana. Eles servem como defaults editoriais revisáveis; um episódio pode sobrescrever apenas os campos necessários em `visual_direction`, sem duplicar o profile inteiro. Os presets não incluem nem exigem assets de episódios específicos.

## Comandos

Na raiz do projeto:

```powershell
python -m engine.artist_vibe episodes/meu_slug/story.json
python search_visual.py "artista musica" --episode meu_slug --segment hook
python search_visual.py "artista musica" --episode meu_slug --segment payoff --kind video --external
python resolve_visual_candidates_web.py meu_slug
python -m engine meu_slug
python -m unittest tests.test_artist_vibe tests.test_artist_vibe_selection tests.test_artist_editing
```

A leitura da direção e a busca sem `--external` são inspeções locais. `--external` consulta providers. O resolvedor baixa e inspeciona candidatos e pode atualizar os assets do episódio; execute-o quando quiser aplicar a seleção revista. `python -m engine` gera o episódio com o TTS e export configurados. Nenhum comando acima publica o episódio.

## Mapa dos arquivos desta implementação

| Responsabilidade | Arquivos |
| --- | --- |
| Profiles e contrato do episódio | `config/artist_vibes.json`, `engine/artist_vibe.py`, `engine/models.py`, `engine/episode.py` |
| Busca e ranking | `engine/visual_search_web.py`, `engine/visual_vibe_scoring.py`, `resolve_visual_candidates.py`, `resolve_visual_candidates_web.py` |
| Edição e estilo | `engine/timeline.py`, `engine/smart_visual_pacing.py`, `engine/artist_style.py`, `engine/pipeline.py` |
| Capa | `publishing/cover.py` |
| Testes novos | `tests/test_artist_vibe.py`, `tests/test_artist_vibe_selection.py`, `tests/test_artist_editing.py` |
| Documentação | `docs/artist-vibe.md` |

## Limites atuais

- O ranking usa metadata e notas editoriais, sem visão computacional semântica. Inspeção humana dos takes continua necessária. Tags falsas produzem escolhas ruins.
- Georgia/Arial usam fontes serif/sans instaladas com fallback por plataforma. Um nome arbitrário não instala uma fonte nem garante a mesma aparência em máquinas diferentes.
- Pacing, texto e transições são defaults ajustáveis. Não existe criação automática de novas frases, correção de cor por artista, detecção de fandom, música por emoção ou novo gerador de takes.
- Referências a carta/diário são direção. O sistema não cria cenas íntimas inexistentes nem identifica pessoas na imagem por conta própria.
- A camada não muda providers de voz, autenticação, licenças ou publicação. Vídeos de hook e payoff continuam exigindo seleção de trechos, proveniência e revisão efetiva.
