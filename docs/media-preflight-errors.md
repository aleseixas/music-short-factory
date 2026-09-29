# Taxonomia dos erros de mídia

Esta tabela acompanha os códigos de `check_episode_media.py`,
`check_episode_media_batch.py` e os validators chamados por eles. As regras
executáveis continuam no código. Uma categoria de prevenção não concede
autorização de publicação ou de mutação depois que o publisher começou.

| Código/família | Classificação | Prevenção e recuperação antes de publicar |
| --- | --- | --- |
| `VISUAL_ASSET_HTTP_403`, `VISUAL_ASSET_HTTP_404` | preventable during asset selection; auto-repairable | Baixar e validar antes de aceitar a URL; substituir todos os assets afetados por candidatos acessíveis e distintos. A URL pode perder acesso depois da validação. |
| `VISUAL_ASSET_INVALID` | preventable during asset selection; auto-repairable | `AssetManager.ensure` verifica presença, caminho permitido, imagem decodificável ou stream real de vídeo. Substituir arquivos ausentes, HTML disfarçado de mídia e metadata inválida. |
| `MEDIA_PROBE_OR_CODEC_ERROR` | preventable during asset selection; local preflight; auto-repairable | Verificar FFprobe/FFmpeg, container, streams e decodificação antes da seleção; só substituir/reparar com mídia validada. Binário ausente exige correção do ambiente. |
| `VIDEO_SOURCE_WINDOW_INVALID` | preventable during timeline construction; local preflight; auto-repairable quando há alternativa | Verificar duração, início/fim e freeze frame contra os limites reais da fonte usando a mesma regra do renderer. Corrigir o intervalo ou substituir a fonte antes de criar o request. |
| `SHOT_REFERENCES_MISSING_ASSET` | preventable during timeline construction; auto-repairable | Validar cada `asset_id` contra o mapa de assets antes de aceitar o slot; reselecionar quando houver pool válido. Não inventar referência ou material ausente. |
| `INTRA_EPISODE_VISUAL_REUSE` | preventable during asset selection; preventable during timeline construction; auto-repairable | Normalizar aliases de fonte e reservar cada visual durante seleção. O gate compartilhado preserva a regra estrita: nenhum shot principal reutiliza imagem ou fonte de vídeo, mesmo com outro trim. |
| `FIRST_EDITORIAL_VISUAL_NOT_VIDEO` | preventable during asset selection; preventable during timeline construction; auto-repairable | Nos projetos que exigem este gate, selecionar e resolver vídeo real para o primeiro take antes de fechar a timeline. Uma imagem não é fallback válido para esse slot. |
| `VISUAL_CANDIDATE_POOL_MISSING`, `VISUAL_CANDIDATE_POOL_INVALID` | preventable during asset selection; local preflight | Validar schema, slots, candidatos, localizadores e diversidade real do pool. Não há reparo automático honesto sem candidatos utilizáveis; completar a autoria quando faltar material. |
| `BACKGROUND_MUSIC_INVALID` | preventable during asset selection; local preflight; auto-repairable | Resolver catálogo/profile e mídia antes de aceitar a trilha; escolher alternativa existente e decodificável. |
| `BACKGROUND_MUSIC_REUSE` | preventable during asset selection; auto-repairable | Aplicar a mesma janela de frescor usada pelo preflight e escolher uma faixa realmente diferente no mesmo lote de reparo. |
| `EPISODE_SCHEMA_OR_TIMELINE_INVALID` | preventable during timeline construction; local preflight | Os loaders de story/assets/timeline validam IDs, tipos, paths, URLs, foco, enums, segmentos, trims, freeze frame, cues, overlays, texto e transição final. Corrigir os campos indicados; não truncar limites ou desativar gates para obter PASS. |
| `REMOTE_MEDIA_RATE_LIMITED`, `REMOTE_MEDIA_PROVIDER_ERROR` | transient external | HTTP 429/5xx exige backoff/recuperação do provedor; não executar retry cego de erro determinístico. Pode ocorrer após uma seleção válida. |
| `REMOTE_MEDIA_TEMPORARY_FAILURE` | transient external | Falha de conexão, timeout ou interrupção do provedor exige diagnóstico e espera limitada. Não substituir dados válidos artificialmente nem repetir publicação por causa de uma resposta de rede ambígua. |
| `MEDIA_TOOLCHAIN_UNAVAILABLE` | non-recoverable pelo reparador de assets | FFmpeg/FFprobe ou outra dependência ausente exige corrigir o ambiente. Repetir seleção ou trocar o tema não instala a ferramenta nem produz PASS. |
| `UNCLASSIFIED_ENGINE_ERROR` | non-recoverable automaticamente | Preservar traceback/detail, identificar a causa e corrigir o código/ambiente ou episódio explicitamente; não converter erro desconhecido em PASS nem repetir sem diagnóstico. |

Os loaders podem emitir mensagens específicas em vez de um código próprio. Esses
erros são normalizados no diagnóstico, preservando `detail` e `target`:

- `engine/episode.py`: story inválido, segmentos vazios/duplicados, texto ausente,
  duração não positiva/finita e diretório fora do projeto — construção/local preflight.
- `engine/assets.py`: IDs/arquivos/foco/URLs inválidos, path fora do episódio,
  streams ausentes, trim fora da fonte, overlay inválido — seleção/construção/local preflight.
- `engine/timeline.py`: ordem e cobertura de segmentos, asset desconhecido,
  enums, números não finitos, duração/trim, foco, background, SFX, visual/text FX,
  limites de cues e overlays — construção/local preflight. Dependências de timestamps
  reais ou duração narrada também precisam da validação posterior ao TTS/render.
- `engine/visual_candidates.py`, quando presente: schema, slots/IDs, candidatos
  vazios, localizadores YouTube inconsistentes, URLs/formatos inválidos — seleção.
- `engine/visual_repetition.py`: fingerprint inválido, mídia vazia, intervalos fora
  da fonte, frames ausentes e metadados de uso inválidos — seleção/local preflight.
- `scripts/publish_ready_bundle.py`: arquivos finais ausentes, probes inválidos,
  metadata/plataformas inconsistentes, manifesto/hash/proveniência inválidos —
  local preflight do resultado final; nenhuma publicação com bundle divergente.

Reparo em lote deve processar todos os itens independentes recuperáveis mesmo
quando outro item do lote exigir autoria ou uma mudança externa. Depois, execute
uma validação completa. Erro de parsing que impede construir um objeto pode
impedir checks dependentes daquele objeto; isso não deve ocultar arquivos ou
assets independentes que ainda possam ser inspecionados.

Ainda são externos ou só observáveis mais tarde: revogação de acesso, quotas,
autenticação, expiração de URL, falha de rede/runner, limites de APIs, falha do TTS,
variações da duração narrada e resposta ambígua de uma plataforma após o envio.
Antes do publisher, repare/revalide. Depois do início do publisher, preserve a
reserva e proíba republicação e mutação, conforme [o contrato](pipeline-contract.md).
