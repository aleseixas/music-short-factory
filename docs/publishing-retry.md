# Publicação, diagnóstico e recuperação

<!-- pipeline-contract: config/pipeline-contract.json -->

Siga [automation-protocol.md](automation-protocol.md),
[pipeline-contract.md](pipeline-contract.md) e
[config/pipeline-contract.json](../config/pipeline-contract.json).
O [prompt canônico de recovery](../templates/music-short-factory-recovery-prompt.md)
usa os mecanismos reais da main; não existe daemon de recovery no clone desligado.

## Entrada e fases

Consulte status do canal/slug e reconcilie identidades exatas. Sheet é checkpoint,
não authority. Ausência inicial de coordination pode ser inicializada pelo
duplicate guard/CAS, incluindo migração de candidate_window; não crie JSON manual.
Lease ocupada não pode ser roubada e expiração não libera outro slug.

Antes de autoria: request único de duplicate, reserva CAS e UNIQUE_CANDIDATE.
Depois de autoria: prepare em clone autenticado valida/repara em lote e confirma
request por CAS após PASS; retorna SHA. Não crie .episode-check manual nem faça
segundo commit/push. Recovery usa `recovery-prepare.yml` com `GITHUB_TOKEN`
nativo, preserva prepare/CAS e envia o dispatch de mídia correlacionado.

Preflight resolve visuais, valida, renderiza, prepara capa, faz dry-run e bundle.
Queue CAS só após os gates e contém slug/source_run_id/request_id.
A queue não é um comando para o agente disparar genericamente outro workflow.

## Diagnóstico e limites

Observe request+slug+SHA+workflow. Leia errors[], error_code/error_class,
recoverable/stage/target/detail/commit_sha, media-preflight-diagnostics-<run_id>,
final-result.txt e pipeline-diagnostic quando existirem.
Logs brutos/step/exit code não são pré-requisito nem causa raiz.
Repare todos os itens independentes elegíveis; erro de autoria pede novas fontes.
Não repita erro determinístico sem mudança, não relaxe validators.

Limites atuais distintos: 15 candidatas compartilhadas desde última queue;
até 5 autorados/substitutos por slot entre creator/recovery; até 3 ciclos
materiais por rodada de recovery; prepare até 2 reparos internos (3 validações);
Media Preflight até 10 passes batch internos por run.
Polling não conta como tentativa. Não resete contadores nem gere 3 reruns cegos.
A janela de 15 é autoritativa no CAS; reset pertence ao fluxo da queue.

## Pré-publicação inviável

Substituição só antes de queue/dispatch, com zero publisher/possibilidade de envio,
cancelamento seguro pelo coordinator/CAS e tombstone, seguido de reconcile e
canal livre. Não use transition CANCELLED onde o grafo não oferece transição.
finish(token, CANCELLED) e abandon_expired são APIs existentes com precondições,
não licença para fechar request alheio ou uma queue já entregue.

O slug pedro_sampaio_ricky_martin_pikito_pikito_nfl está abandonado pelo usuário.
Nunca reparar/renderizar para publicação/queue/publicar; finalize somente
quando seguro, preserve tombstone e não ressuscite o histórico.

## Queue selada e dispatch

QUEUED proíbe mutação mesmo sem publisher. Não altere bundle/request/metadata.
Receipts .pipeline/dispatch/<slug>--<request_id>.json preservam owner, lease,
generation, source/run/workflow/SHA. PREPARED expirado permite takeover por CAS.
SENDING/aceito/ambíguo exige procurar/adotar o run exato, nunca repetir POST porque
o run não apareceu. Rejeição explícita só pode retomar após correção pelo protocolo.

`pipeline_control.py reconcile --slug <slug> --repository aleseixas/music-short-factory`
usa o dispatch existente para retomar intenção elegível/observar a enviada.
Respeite settle_seconds=900 e max_run_seconds=86400 atuais.
DISPATCH_UNCERTAIN fecha slug e libera canal; não prova entrega nem permite
substituto do mesmo slot quando existe ambiguidade de envio.

Os workflows ainda reconhecem markers legados .publish-retry/<slug>-retry-1.txt
até retry-3 por compatibilidade. Isso não é recomendação para o agente criá-los.
Creator/recovery não criam markers, não enviam dispatch por ferramenta genérica
e não rerodam publisher. O controlador e o receipt existente são o caminho.

## Tentativa irreversível

A reserva .publication-attempts/<slug>.json precede efeitos externos, inclusive
hosting/upload. Publisher iniciado ou possível implica EVER_PUBLISHED_OR_ATTEMPTED=YES,
REPUBLICATION_ALLOWED=NO e RECOVERY_MUTATION_ALLOWED=NO.
Nunca apague reserva/queue/tombstone, republique ou altere episódio.
Sem segunda sessão, preenchimento de plataforma faltante ou substituto do slot.
O run original conclui suas plataformas sob a mesma sessão; recovery observa.

Após crash a repo consulta a plataforma quando existe identificador e classifica
confirmed_sent/confirmed_not_sent/uncertain. confirmed_not_sent depende de CAS
sobre versão ainda prepared; ausência de recurso depois de sending não prova
não envio. Nenhuma classificação é permissão para o agente criar outra tentativa.
API indisponível/CAS perdido exige observação adicional, sem substituir authority.

## Fechamento verificável

Timeout/run transitório preserva request e EM_ANDAMENTO/NÃO_VERIFICADO.
Queue/PASS não provam publicação. Registre resultado real por plataforma;
parcial/incerto é PARTIAL_NO_TOUCH permanente. TikTok draft não é publicação pública.
Sheet no início e em cada transição, confirmando escrita; contadores/vínculo
persistem entre rodadas. Sempre entregue log conciso com erro e next_action.
Nunca altere schedule/enabled state sem pedido explícito do usuário.
