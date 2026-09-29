# Implementação do pipeline — Content e Music Short Factory

> A [revisão final de 29/09/2026](pipeline-final-review.md) substitui as
> conclusões e resultados abaixo. Coordenação distribuída, fencing, snapshots
> e recovery foram implementados; a validação final concluiu com
> `SAFE_TO_COMMIT: YES`, sem falhas novas frente ao baseline.

Relatório de 27/09/2026. As mudanças estão no workspace dos dois repositórios.
Não houve commit, push, deploy, execução remota de Actions ou publicação real.
Os arquivos locais preexistentes do usuário foram preservados, incluindo
`content-short-factory/pegar.py`, seu arquivo de credenciais e o episódio local
não rastreado em `music-short-factory/episodes/michael_jackson_brasil_hero/`.
Arquivos de credenciais não foram usados para esta validação.

## Causas raiz encontradas

1. **Validação divergente e tardia.** O checker simples e o batch não aplicavam
   exatamente as mesmas regras. Em Music, o batch aceitava reutilização de uma
   fonte de vídeo com trims diferentes, enquanto o checker simples bloqueava.
   Referências, pool, primeiro take e limites da fonte podiam falhar somente na
   Action ou renderização. O Content também não possuía todos os gates de pool.
2. **Diagnóstico incompleto.** O primeiro erro de parsing encerrava checks que
   ainda poderiam avaliar arquivos independentes. Um item não recuperável podia
   impedir o reparo dos demais itens do lote. Reexecuções sem mudança real não
   corrigiam erros determinísticos.
3. **Continuidade inferida pelo agente.** Templates pediam listagens inteiras e
   usavam a queue mais recente para inferir trabalho ativo. Não existia um estado
   pequeno com próximo passo, autoria concluída e permissão de mutação.
4. **Trigger confundido com dispatch.** Duplicate e Media Preflight são acionados
   por push. A ausência de dispatch não constitui bloqueio. Runs precisam de
   identidade e polling, inclusive antes de aparecerem na API.
5. **Proteção de publicação dependente de interpretação.** Era necessário
   inferir se alguma plataforma já poderia ter recebido o conteúdo. Retries,
   reruns e caminhos alternativos precisavam compartilhar uma reserva durável
   anterior à chamada externa.
6. **Seleção e recuperação incompletas.** A abertura em imagem podia impedir a
   inspeção de vídeos; seleção gulosa consumia a única fonte de outro slot.
   Reparos `asset:id` ignoravam pools indexados por `shot.id`, e diagnósticos
   agregados repetiam o mesmo reparo. O gate por metadados não decodificava o
   trecho nem verificava a duração prevista do take.
7. **Drift operacional.** Templates repetiam regras de continuidade, queue,
   retries e unicidade incompatíveis com partes do código/workflows.

## Arquitetura antes e depois

| Antes | Implementado |
| --- | --- |
| Agente reconstrói situação por diretórios/logs | `.pipeline/state.json` aponta o ativo por canal e `.pipeline/episodes/<slug>.json` registra estado/evidência |
| Workflow e templates definem contratos separados | `config/pipeline-contract.json`, controlador e testes de drift confrontam triggers reais |
| Checker simples e batch divergem | `validate_episode_media(...)` é compartilhado pelo checker simples, batch e gate local |
| Primeiro erro oculta verificações independentes | Modelos parciais exclusivamente diagnósticos preservam o erro original e coletam outros erros |
| Reparar/reexecutar uma falha de cada vez | Reparar o lote recuperável; revalidar somente depois de mudança efetiva |
| Request pode anteceder validação local | `pipeline_control.py prepare` só cria `.episode-check` após PASS e grava fingerprint/identidade |
| Acompanhar run recente ou interpretar ausência | Verificar request no commit exato, paginar runs por workflow/SHA/evento e esperar com backoff |
| Agente decide se um retry parece seguro | Reserva por episódio/plataforma, sessão de publicação e compare-and-set remoto antes do envio |

Fluxo normal:

```text
CANDIDATE -> UNIQUE -> AUTHORING -> LOCAL_VALIDATION -> MEDIA_PREFLIGHT
          -> READY_TO_QUEUE -> QUEUED -> PUBLISHING -> PUBLISHED
```

`LOCAL_REPAIR`, `VALIDATION_FAILED`, `REJECTED` e `PUBLISH_UNCERTAIN` representam
recuperação e resultados que não devem ser inferidos pelo agente. Os canais
`default` e `nostalgia` têm continuidade separada. O estado responde diretamente
sobre autoria, preflight, queue, início do publisher, mutação, republicação e
criação de novo episódio.

No caminho normal, a consulta lê o índice e o registro do slug. Migração de estado
ausente/inconsistente usa o índice Git completo e consultas específicas, incluindo
arquivos locais relevantes. Ela não interpreta uma listagem remota truncada como
completa. O histórico legado pode exigir reconciliação conservadora; um conflito
real continua impedindo a criação de um terceiro episódio.

A seleção agora preserva uma distribuição possível de fontes únicas entre os
slots restantes, permite reconstruir referências ausentes e força vídeo na
abertura. O gate verifica `required_seconds`, speed, freeze e crossfade com
`assess_trim`, além dos limites reais do renderer. FFmpeg decodifica o trecho
previsto; sucesso sem nenhum frame também é rejeitado. A duração final narrada
continua sendo validada depois do TTS.

O preflight externo preserva resolução final, render, capa, dry-run e validação
do bundle. O publisher usa o artefato aprovado, sem renderizar novamente.
A queue carrega slug, run de origem e identidade da solicitação. O dispatch
explícito do publisher continua necessário para a queue criada com `GITHUB_TOKEN`,
porque esse push não dispara outra Action. Esse comportamento está descrito na
[documentação de triggers do GitHub](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow).

O dispatch possui recibo durável anterior ao POST em `.pipeline/dispatch/`.
Queue persistida sem intenção pode retomar o envio do pedido; intenção aceita ou
ambígua só é observada por identidade, nunca reenviada automaticamente. Uma
rejeição explícita permite retomar depois de corrigida a causa. O workflow grava
falha do preflight para o mesmo request/run, sem reverter queue/publisher.
Duplicate lê a continuidade da `main` atual, mas extrai a solicitação do SHA
exato do evento, evitando usar um ledger antigo em execuções enfileiradas.

## Erros prevenidos ou interceptados antes do request externo

| Erro/família | Classificação | Resultado implementado |
| --- | --- | --- |
| HTTP 403/404, arquivo ausente, HTML no lugar de mídia, metadata inválida | preventable during asset selection; auto-repairable quando há alternativa | Candidato precisa ser acessível e validado; reparo substitui alvos identificados e verifica a alternativa |
| Container/codec/stream inválido | preventable during asset selection; local preflight | O mesmo `AssetManager` valida o probe e decodifica o trecho real com FFmpeg antes do request |
| `VIDEO_SOURCE_WINDOW_INVALID`, duração/trim/freeze frame fora da fonte | preventable during timeline construction; local preflight; auto-repairable quando há alternativa | Limites do renderer e duração prevista, speed, freeze e crossfade são verificados no gate local |
| Referência quebrada de asset/shot | preventable during timeline construction; auto-repairable quando o pool permite | Referências são checadas e alvos podem ser reselecionados no mesmo lote |
| Visual/fonte repetida | preventable during asset selection; preventable during timeline construction | Uma única regra estrita: nenhum shot principal reutiliza imagem ou fonte de vídeo, mesmo com outro trim |
| Primeiro take em imagem, pool ausente/inválido/insuficiente | preventable during asset selection; local preflight | Pool exige cobertura por fontes distintas e abertura em vídeo; o reparador não inventa candidatos ausentes |
| Background inválido ou repetido | preventable during asset selection; local preflight; auto-repairable | Resolução e frescor usam os checks reais; reparo busca faixa utilizável e diferente |
| Schema, IDs, enums, foco, segmentos, cues e overlays inválidos | preventable during timeline construction; local preflight | Parsers reais geram o diagnóstico; erros independentes são coletados sem transformar modelo parcial em PASS |

Isso reduz falhas determinísticas da primeira Action. Não garante que uma URL
continue acessível nem que todo episódio possa ser corrigido automaticamente.
`recoverable=true` não significa que exista material alternativo disponível.

## Erros externos ou que exigem intervenção

- `REMOTE_MEDIA_RATE_LIMITED`, `REMOTE_MEDIA_PROVIDER_ERROR` e
  `REMOTE_MEDIA_TEMPORARY_FAILURE`: HTTP 429/5xx, conexão/timeout ou indisponibilidade
  podem ocorrer depois de uma validação bem-sucedida. São externos transitórios;
  não autorizam recriar episódios ou repetir publicação.
- `MEDIA_TOOLCHAIN_UNAVAILABLE`: falta de FFmpeg/FFprobe/dependência exige corrigir
  o ambiente; não é reparo de asset.
- `UNCLASSIFIED_ENGINE_ERROR`: exige diagnóstico concreto; não recebe PASS nem
  retry cego por ser desconhecido.
- Autenticação, quotas, revogação de acesso, runner indisponível, TTS e restrições
  das plataformas continuam externos. Checks que dependem da duração narrada e
  dos bytes finais continuam no render/preflight final.
- Resposta ambígua depois de envio resulta em observação conservadora. O episódio
  permanece bloqueado para republicação e mutação.

A [taxonomia dos validators](media-preflight-errors.md) e o
[contrato operacional](pipeline-contract.md) complementam este relatório.

## Antiduplicação implementada

`publishing/attempts.py` exige estado validado/QUEUED antes da primeira publicação
ao vivo e registra `.publication-attempts/<slug>.json` antes de chamar o publisher.
Em Actions e no CLI local conectado, a reserva usa o Contents API com SHA para
compare-and-set. A origem GitHub é verificada; ausência de armazenamento central
ou de autenticação impede publicação ao vivo. Falha,
conflito ou timeout da reserva não permitem enviar. O SHA na atualização segue o
[contrato do GitHub Contents API](https://docs.github.com/en/rest/repos/contents).

Cada plataforma pode ser tentada uma única vez na mesma sessão; outra sessão ou
rerun não pode repetir o episódio. O pipeline normal pode continuar para as
plataformas ainda não tentadas da sessão autorizada. O resultado final preserva
a reserva, tanto em `PUBLISHED` quanto em `PUBLISH_UNCERTAIN`.

Assim que houver tentativa ou possibilidade de envio:

```text
EVER_PUBLISHED_OR_ATTEMPTED=YES
REPUBLICATION_ALLOWED=NO
recovery_mutation_allowed=NO
```

Os entry points de autoria, render, metadata, resolução e reparo verificam o
bloqueio de mutação; checkouts conectados consultam o marker remoto exato antes
de alterar conteúdo e conservam localmente a evidência observada. O status offline
é somente uma leitura da última evidência sincronizada. Workflows normal e de
artefato existente compartilham
concorrência e a mesma reserva. O mecanismo escolhe não reenviar quando o resultado
é incerto, mesmo que isso exija investigação manual da plataforma.

## Arquivos alterados ou adicionados

Os grupos abaixo descrevem arquivos da implementação; mudanças preexistentes do
usuário não estão incluídas.

| Grupo | Arquivos |
| --- | --- |
| Estado, contrato e orquestração | `config/pipeline-contract.json`, `engine/pipeline_state.py`, `engine/pipeline_runtime.py`, `scripts/pipeline_control.py`, `scripts/pipeline_workflow.py`, `scripts/dispatch_publication.py` |
| Validação compartilhada e seleção | `check_episode_media.py`, `check_episode_media_batch.py`, `engine/media_validation.py`, `engine/assets.py`, `engine/timeline.py`, `engine/visual_candidates.py`, `resolve_visual_candidates.py`; `engine/youtube.py` adicionado no Content para identidades canônicas |
| Reparos | `scripts/repair_media_preflight_batch.py`, `scripts/repair_visual_asset.py`, `scripts/repair_background_music.py` |
| Segurança de mutação/publicação | `publishing/attempts.py`, `publish.py`, `engine/pipeline.py`, `new_episode.py`, `prepare_post.py`, `scripts/publish_ready_bundle.py` |
| Workflows e diagnóstico | `.github/workflows/duplicate-preflight.yml`, `episode-media-preflight.yml`, `publish-episode.yml`, `publish-existing-artifact.yml`, `scripts/workflow_diagnostic.py`; também `duplicate-preflight-nostalgia.yml` no Music |
| Documentação comum | `README.md`, `docs/pipeline-contract.md`, `docs/media-preflight-errors.md`, `docs/publishing-retry.md`, `docs/visual-uniqueness.md`, este relatório |
| Templates comuns | `templates/editorial-direction-prompt.md`, `templates/publishing-completion-rule.md`, `templates/short-form-style-rule.md` |
| Templates específicos | Content: `templates/content-topic-rule.md`; Music: `templates/execution-single-episode-rule.md`, `templates/music-universe-topic-rule.md`, `templates/nostalgia-famous-artists-rule.md` |
| Testes novos | `tests/test_local_media_gate.py`, `tests/test_pipeline_runtime.py`, `tests/test_pipeline_contract_drift.py`, `tests/test_publication_attempts.py`, `tests/test_publication_dispatch.py`, `tests/test_media_repair_scopes.py` |
| Testes atualizados e exclusões | `tests/test_instagram_retry.py`, `tests/test_publishing.py`, `tests/test_visual_candidate_resolution.py`, `.gitignore` |

## Cobertura dos requisitos e resultados

| Requisito | Cobertura |
| --- | --- |
| Erro corrigido antes de `.episode-check`; múltiplos reparos em lote | `test_pipeline_runtime.py`: `test_repair_multiple_errors_before_request_exists`; `test_local_media_gate.py`: reparos independentes mesmo com item não recuperável |
| Gate local e externo compartilham regras | `test_local_media_gate.py`: checker simples chama batch; `test_pipeline_runtime.py`: validator padrão local é o externo |
| Push reconhecido sem dispatch | `test_pipeline_runtime.py`: `test_push_without_dispatch_is_ready`; testes de contrato confrontam todos os eventos dos workflows |
| Action demora a aparecer e estados transitórios | `test_delayed_run_and_all_pending_statuses_are_polled`, `test_timeout_reports_pending_without_redispatch` |
| Duas execuções seguem seus próprios runs | `test_two_requests_track_own_runs_even_when_latest_differs`, `test_request_must_match_exact_commit_not_latest` |
| Milhares de episódios/listagens truncadas | Testes de status sem listagem, reconciliação de 5.000 episódios arquivados, índice completo e paginação de runs |
| Retomar ativo/reconciliar inconsistência | Testes de ativo real, canais independentes, queue legada, recibo local de PASS, request ausente e publisher interrompido |
| Publisher iniciado impede republicação | `test_publication_attempts.py`: plataforma/sessão únicas, conflito CAS, timeout sem retry, checkout desatualizado e resultado terminal que mantém a reserva |
| Erro determinístico sem retry cego | `test_deterministic_failure_no_retry_without_change`, `test_report_only_change_never_triggers_another_validation` |
| Não enfraquecer gate com diagnóstico parcial | `test_partial_diagnostics_can_never_turn_original_failure_into_pass` e verificações de pool/abertura/limites da fonte |

Resultados observados nesta implementação:

| Verificação | Content | Music |
| --- | --- | --- |
| Suíte completa executada | **567 passaram, 9 falharam, 1 ignorado**, 583,71 s | **582 passaram, 16 falharam, 1 ignorado**, 585,16 s |
| Investigação da baseline `HEAD` em arquivo isolado | As 9 falhas foram reproduzidas sem as mudanças | 13 falhas reproduzidas sem as mudanças; 3 regressões no helper de identidade foram corrigidas |
| Rechecagem das 3 regressões corrigidas | Não se aplica | 27 testes relacionados passaram |
| Última suíte focalizada de pipeline/segurança | **101 passaram**, 18,35 s | **102 passaram**, 18,43 s |
| Integração de workflows, assets e seleção visual | **47 passaram**, 1,43 s | **48 passaram**, 1,43 s |
| Rechecagem ampliada de publicação | **120 passaram, 1 falhou**, 9,27 s | **117 passaram, 1 falhou**, 9,27 s; mesma falha TikTok da baseline |
| Workflows com actionlint 1.7.12 | Sem erros | Sem erros |
| `git diff --check` | Sem erros | Sem erros |

**A suíte completa não está verde.** Não foi repetida integralmente depois da
correção das três regressões Music; elas foram revalidadas no conjunto relacionado
e no conjunto focalizado. As falhas preexistentes reproduzidas abrangem SFX,
TikTok, mídia remota e smart pacing; no Music também áudio editorial, duplicidade,
orientação editorial e cookie probe. Não foram ocultadas, desabilitadas ou
reclassificadas como sucesso para apresentar este resultado.

O conjunto focalizado final incluiu os nove arquivos de testes de estado/contrato,
tentativas/dispatch, gate local, escopos de reparo, resolução visual, Instagram e
bundle. As novas regressões exercitam reparos reais em arquivos com inspeção de
mídia simulada; a decodificação usa um vídeo sintético real e conteúdo corrompido.
Os logs locais estão em `output/pipeline-validation/` (ignorados pelo Git).

`actionlint` validou os workflows; ShellCheck e Pyflakes não estavam instalados,
portanto essas análises auxiliares não foram executadas. Os testes de APIs,
concorrência e polling usam simulações; nenhuma plataforma recebeu uploads.

## Riscos e limites restantes

1. A implantação ainda exige revisão/commit/push e observação de um run real com
   permissões e segredos do ambiente. O workspace não prova entrega remota.
2. O primeiro índice legado requer histórico Git suficiente; ambiguidade de
   tentativa antiga ou índice indisponível é tratada conservadoramente. O caminho
   normal já indexado não depende de varrer diretórios históricos.
3. Um pool sem fontes válidas precisa de autoria. Reparos não inventam mídia e
   não prometem resolver todos os erros de schema/editoriais automaticamente.
4. O PASS local não substitui o gate do arquivo final nem controla disponibilidade
   futura de provedor, quotas, autenticação ou duração produzida pelo TTS.
5. A reserva durável prioriza evitar duplicação. Um timeout durante sua gravação
   pode exigir reconciliação mesmo se nenhuma API de publicação chegou a ser
   chamada; isso não libera um retry cego. O mesmo vale para um dispatch ambíguo.
   Escritas concorrentes no Git podem exigir reconciliação de conflito, sem
   contornar a reserva de publicação. Os guards não impedem edição manual fora
   dos entry points do projeto.
6. As falhas preexistentes identificadas na suíte completa continuam pendentes.
   O escopo verificado com sucesso nesta entrega é o conjunto focalizado acima.
