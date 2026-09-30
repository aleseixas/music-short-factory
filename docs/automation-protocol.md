# Protocolo comum dos agendamentos Music Short Factory

<!-- pipeline-contract: config/pipeline-contract.json -->

Aplica-se somente a `aleseixas/music-short-factory`, branch `main`, canal
`default`, marca Além do Hit. Creator e recovery leem este protocolo, os prompts
do seu papel e o [contrato operacional](pipeline-contract.md) a cada execução.
O código, os schemas, `config/pipeline-contract.json` e os workflows atuais da
`main` prevalecem em contratos técnicos. Preferências explícitas do usuário
continuam obrigatórias; divergência exige diagnóstico, não bypass.

Prompts canônicos:
- [Creator](../templates/music-short-factory-automation-prompt.md)
- [Recovery](../templates/music-short-factory-recovery-prompt.md)
- [Uma entrega por execução](../templates/execution-single-episode-rule.md)
- [Conclusão verificável](../templates/publishing-completion-rule.md)

## Escopo e identidade

Entregue no máximo um episódio por slot, com tema realmente novo. Falha de
candidato pré-publicação não encerra um slot vazio quando há continuação segura.
Não execute, cheque ou altere `content-short-factory` / Além do Óbvio nem nostalgia.

Use `America/Sao_Paulo`. O creator identifica o slot da invocação real; recovery
identifica o slot criador mais recente já vencido. Antes das 08h, o slot mais
recente é o das 16h do dia anterior; não antecipe um slot futuro. Associe slot,
slug, request e evidências existentes. Nunca atribua episódio ao slot apenas
porque é o run mais recente. Slot é checkpoint de observabilidade; não existe
campo nativo `slot_id` no contrato técnico atual. Não invente esse campo nos JSONs.

Se existe trabalho ativo anterior, sua continuidade tem prioridade e bloqueia
outro slug no canal. Um episódio de outro slot não prova entrega do slot atual.
Sem vínculo suficiente, registre `SLOT_UNVERIFIED` e reconcilie; não publique por
suposição. Slot com publisher iniciado/possivelmente iniciado nunca recebe
substituto, mesmo quando o encerramento autoritativo libera o canal para um slot
futuro. Slot já entregue recebe observação/no-op, sem novo episódio.

## Entrada e autoridade

1. Leia a `main` atual e registre `EXECUTION_START_HEAD`. Consulte o contrato,
   workflows reais, coordinator, schemas e templates relevantes.
2. Leia e registre checkpoint inicial no [Short Factory Recovery State](https://docs.google.com/spreadsheets/d/1U_wncV0GVzAVBaBEF5sBQZSJwrhKX_TPjQQ8puM26JY/edit),
   abas `RecoveryState` e `Protocol`. Ignore `project=EXAMPLE_ONLY`; use
   `project=music-short-factory`.
3. Em clone autenticado da repo canônica, consulte:

   ```bash
   python scripts/pipeline_control.py contract --workflow duplicate
   python scripts/pipeline_control.py contract --workflow media
   python scripts/pipeline_control.py contract --workflow publish
   python scripts/pipeline_control.py status --channel default
   ```

4. Se há slug ativo, consulte também seu estado exato e reconcilie quando necessário:

   ```bash
   python scripts/pipeline_control.py status --slug <slug>
   python scripts/pipeline_control.py reconcile --slug <slug> --repository aleseixas/music-short-factory
   ```

A autoridade é `.pipeline/coordination/default.json` no ref canônico, com
`request_id`, `owner_id`, `generation`, `version`, lease e revisão.
`.pipeline/state.json`, ledger local, tokens e Sheet são caches/checkpoints.
Considere `active`, `stage`, `next_action`, `mutation_allowed`,
`recovery_mutation_allowed`, `republication_allowed`,
`can_create_new_episode` e conflitos junto da autoridade remota.

Ausência inicial do arquivo de coordenação não é, sozinha, blocker:
`SharedCoordinator._read` representa-a como `IDLE`; o duplicate guard faz a
reserva e inicialização por CAS, incluindo migração de candidate_window quando
necessária. Use esse fluxo existente. Não escreva um JSON de autoridade à mão,
não simule posse e não trate ausência de pastas/queues como autorização.

Reconcile pode baixar os arquivos autoritativos e preservar conflitos locais.
Não execute download/reconcile sobre rascunho em edição sem antes preservar o
rascunho; nunca recoloque um cache antigo por cima de uma revisão nova.
Lease ocupada exige esperar/observar; expiração não libera outro slug.
Falha CAS/fence/cache exige releitura/reconcile antes da próxima escrita.

GitHub conectado permite leitura e os requests de entrada documentados. Autoria,
reparo e `prepare` continuam exigindo um clone autenticado, com dependências,
FFmpeg/ffprobe e credencial aceita pelos comandos coordenados. A conexão MCP não
prova que um terminal tenha essa credencial e nunca autoriza criar
`.episode-check`, queue ou authority manualmente.

Para recovery pré-publicação existe também o caminho dedicado
`.recovery-request/*.json` -> `recovery-prepare.yml`: o request carrega o mesmo
slug, o request anterior, um request de mídia novo, repair_cycle 1..3 e os seis
arquivos editoriais completos. O workflow reconcilia a autoridade, rejeita
publisher/queue/attempt, aplica os bytes somente no clone e chama o mesmo
`prepare` fenced/CAS. Ele só é autorizado com o secret
`PIPELINE_GITHUB_TOKEN`, distinto do token padrão do Actions. O commit do CAS
feito com esse token deve gerar o push real de Media Preflight; o workflow não
faz dispatch/rerun manual. Sem esse secret/runtime, registre blocker concreto e
não faça bypass pelo MCP.

## Candidata e autoria

Pesquise nome da música, artista, tema e variações no histórico compartilhado.
Leia evidências exatas encontradas, tombstones e publication attempts. Pesquisa
de duplicidade editorial pode percorrer o histórico; continuidade normal usa
paths exatos. Resultado vazio/incompleto nunca prova ineditismo.

Somente com canal livre, slot elegível e candidata nova, envie exatamente um
request novo `.duplicate-check/<slug>--<request_id>.json`, contendo
`song`, `artist`, `slug` e `request_id`. Esse request inicial é a entrada
documentada do workflow; não equivale a authority/queue/episódio. Use um commit
com somente um request de duplicate novo. Push aciona `duplicate-preflight.yml`.
A Action reserva/conta a candidata por CAS antes do gate de duplicidade.
Não chame `start`/novo slug por fora para escapar do contador compartilhado.

Acompanhe `slug + request_id + SHA + workflow`, lendo resultado estruturado:
- `UNIQUE_CANDIDATE`: só então autorar, depois de atualizar o clone e reconciliar
  o slug/request reservado; respeite a lease e os mecanismos fenced.
- `DUPLICATE_CANDIDATE`: candidata rejeitada, sem autoria; próximo tema somente
  após confirmação de fechamento e canal livre.
- `RESUME_EXISTING_EPISODE`: retome o slug indicado, sem outro candidato.
- `CONTINUITY_CONFLICT`: reconcile as identidades exatas; não force a reserva.
- `CANDIDATE_LIMIT_REACHED`: respeite o limite persistido, sem reset manual.
Falha de infraestrutura não é `UNIQUE_CANDIDATE`.

Conserve a identidade da candidata reservada. Uma nova validação após reparo
material pode obter novo request pelo mecanismo de mudança de request da repo;
nunca troque o request de execução em andamento ou de queue selada.

Autore rascunhos do slug reservado, confirmando bytes pela transação coordenada,
sem commits genéricos de arquivos operacionais. Produza `story.json`,
`timeline.json`, `assets.json`, `visual_candidates.json`, `sources.txt`
e `post.json` no schema atual. Leia os templates editoriais/retention/visual,
catálogos, fontes e regras de capa/CTA/legenda. Não altere engine, publishing,
config, workflows, catálogo SFX, episódios anteriores ou filas.

Artistas reconhecidos no Brasil; história verificável, hook/open loop e payoff.
Use vidIQ para popularidade/concorrência quando acessível; sem acesso, não invente
métricas e registre a curadoria com evidências disponíveis. Primeiro take
editorial sempre vídeo real reconhecível, com capa do começo do próprio vídeo
conforme a main. Mire aproximadamente 60% vídeo/40% imagem, selecionando cada
tipo separadamente por fit semântico, sem enfraquecer o visual para cumprir quota.
Não abra com imagem; a capa técnica de ~0,30s é gerada pelo pipeline.
URLs precisam ser locators concretos suportados, nunca resultados de busca,
redirects sociais não suportados ou HTML disfarçado de mídia. Não repita visuais.
Português brasileiro, hashtags lowercase. Respeite pool, pacing e validators.

## Prepare, preflight e queue

Depois da autoria e do gate final de autoridade/ineditismo:

```bash
python scripts/pipeline_control.py prepare <slug> --request-id <request_id>
```

O prepare valida em lote, aplica reparos suportados e revalida; PASS confirma
metadata, bytes e request pelo CAS, devolvendo `commit_sha` e
`next_action=wait_for_correlated_run`. Não faça segundo commit/push do request.
Em FAIL leia `errors[]` e faça reparo material antes de nova validação.
Não crie `.episode-check` manual nem marque PASS.

Acompanhe o SHA retornado, não o HEAD posterior das transições:

```bash
python scripts/pipeline_control.py wait <slug> --request-id <request_id> --commit-sha <request_sha> --workflow media --repository aleseixas/music-short-factory --timeout 45
```

Para push com vários commits, use também `--before-sha` do push real.
Run não visível e queued/pending/waiting/requested/in_progress são transitórios.
Use observação/backoff e preserve identidade para a rodada seguinte no timeout.
Não reenfileire nem rerode por timeout.

Media Preflight resolve/seleciona os visuais finais, valida, renderiza, prepara
capa/metadata, faz dry-run das plataformas e aprova bundle `publish-ready-<slug>`.
Só o job de queue sela por CAS `.publish-queue/<slug>.txt`, com três linhas:
slug, source_run_id, request_id. Só então o mecanismo persistente de dispatch
solicita o publisher. Não crie queue/retry à mão, não chame publisher live pelo
agente nem force dispatch/rerun via ferramenta genérica.

`READY_TO_QUEUE` é ponto do workflow, não permissão para o agente construir
queue. `QUEUED` já proíbe mutação mesmo antes de publisher; observe ou reconcilie
o dispatch existente. O publisher consome os mesmos bytes aprovados sem rerender.

## Recuperação e limites

Separe os contadores:
- Até 15 candidatas na `candidate_window` compartilhada desde a última queue;
  o duplicate guard controla reserva/contagem e só o fluxo da queue reseta.
  Consultas e retomada do mesmo request não consomem nova candidata.
- No máximo 5 candidatos autorados/substitutos por slot, acumulando creator e
  recovery nos checkpoints; aplique limite menor da main se houver.
- Até 3 ciclos reais de reparo seguro por rodada de recovery, com causa concreta,
  correção material e revalidação. Leitura/polling não conta como tentativa.
- Prepare tem até 2 reparos internos (3 validações); Media Preflight atualmente
  tem até 10 passadas batch internas por run. Não confunda esses limites com três
  reruns de Actions/publicação, não reduza os gates e releia o código atual.

Run em andamento é observado. Erro determinístico exige mudança; transitório
admite backoff conforme diagnóstico. Colete todos os erros independentes antes
de reparar. Leia error_code/error_class/recoverable/stage/target/detail/commit_sha,
diagnósticos e artefatos do run exato. Erro genérico ou falta de log não encerra
o diagnóstico; nunca repita a mesma seleção inválida sem correção.

Candidata inviável pode ser cancelada somente antes da entrega para queue,
sem publisher/dispatch possível e após provar segurança e posse atuais.
O CLI `transition ... CANCELLED` não é caminho genérico: o grafo técnico não
oferece essa transição a partir de autoria/validation.
O coordinator oferece `finish(token, "CANCELLED")`; com lease expirada,
`abandon_expired` verifica identidade/generation e precondições. Use essas APIs
existentes apenas com evidência e token atuais, sem criar helper/bypass novo.
Reconcile o encerramento/tombstone e releia o canal antes do próximo candidato.
Sem fechamento confirmado, mantenha o slug e reporte o blocker.

`pedro_sampaio_ricky_martin_pikito_pikito_nfl` foi abandonado pelo usuário:
nunca reparar, renderizar para publicação, reenfileirar ou publicar.
Se ainda ativo, finalize pelo mecanismo seguro acima; se fechado, apenas observe
o tombstone. Preserve evidência; não apague histórico ou ressuscite o slug.

## Dispatch e publisher

`.pipeline/dispatch/<slug>--<request_id>.json` é receipt persistente:
PREPARED expirado admite takeover por CAS; SENDING/aceito/ambíguo exige adotar
e observar o mesmo run. Ausência de run não autoriza novo POST.
`reconcile --slug ... --repository ...` usa o protocolo existente para retomar
dispatch elegível; não envie dispatch fora dele. Respeite prazos atuais
(settle_seconds=900; max_run_seconds=86400) e encerramento DISPATCH_UNCERTAIN.
Encerramento libera o canal; nunca reabre o slug nem prova entrega do slot.

Antes de efeitos externos a tentativa é persistida. Publisher iniciado, parcial,
falho depois de iniciar ou possivelmente iniciado implica:
`EVER_PUBLISHED_OR_ATTEMPTED=YES`, `REPUBLICATION_ALLOWED=NO`,
`RECOVERY_MUTATION_ALLOWED=NO`. Nunca retry/rerun/queue/reset, alteração de
mídia/metadata, preenchimento de plataforma faltante nem substituto do mesmo slot.
O run original pode concluir suas plataformas sob a mesma sessão; creator/recovery
apenas observam, sem iniciar outra sessão ou interromper a publicação original.
Uma flag permissiva de cache/Sheet nunca reabre autoridade fechada.

Após crash, reconcile consulta evidências disponíveis e classifica
confirmed_sent/confirmed_not_sent/uncertain conforme a repo. Isso não autoriza
nova tentativa do agente. Fechamento incerto preserva tombstone e anti-duplicação.

## Sheet e relatório

Atualize a mesma linha lógica de episode_id estável no início e em cada transição:
slot/checkpoint, slug/request, generation, SHA/run/job, gates, erros, next_action,
tentativas, plataformas e locks. Preserve contadores e vínculos de slot em
campos/colunas realmente existentes; não invente schema do Sheet.
Para ausência de episódio registre checkpoint da execução conforme o Protocol.
Erro do Sheet deve aparecer no log; nunca declare escrita sem confirmação.
O Sheet não concede posse/publicação nem sobrescreve CAS.

Não conclua sucesso com candidato, commit, PASS ou queue. Registre resultado
verificável: PUBLICADO, PARTIAL_NO_TOUCH, EM_ANDAMENTO, NÃO_VERIFICADO,
SLOT_JA_ENTREGUE, SLOT_TENTADO_NO_TOUCH, BLOQUEADO ou LIMITES_ESGOTADOS.
Diferencie plataformas reais; DRAFT_ENVIADO no TikTok não significa postagem
pública confirmada. Falha de candidata não é terminal do slot enquanto restar
continuação autorizada.

Após toda rodada entregue log conciso com data/hora local, repo/HEAD, papel,
slot, candidato inicial/final, coordinator/reconcile, request/generation/version,
duplicate/autoria/local PASS/media/render/bundle/queue/dispatch, plataformas,
3 contadores (candidate_window, autorados, ciclos de reparo), passes internos,
run/job/SHA, Sheet confirmado, flags irreversíveis, erro concreto e next_action.
Use N/A ou NÃO_VERIFICADO quando faltarem evidências, sem inventar resultados.

Nunca pause, desative, exclua, reagende ou altere schedule/enabled state de qualquer
agendamento durante a execução. Apenas pedido explícito do usuário em chat
autoriza isso. Falha técnica não altera o agendamento.

