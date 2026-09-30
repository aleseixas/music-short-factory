# Regra obrigatória — conclusão real da publicação

<!-- pipeline-contract: config/pipeline-contract.json -->

Siga o [protocolo dos agendamentos](../docs/automation-protocol.md), o
[contrato operacional](../docs/pipeline-contract.md) e
[config/pipeline-contract.json](../config/pipeline-contract.json).

## Autoridade e validação

Consulte `pipeline_control.py status --channel default` e `status --slug <slug>`.
Leia autoridade remota, request/generation/version, next_action, mutation_allowed,
recovery_mutation_allowed, republication_allowed e can_create_new_episode.
Índice/Sheet/local tokens são caches. Retome o slug ativo e respeite o slot.

Autoria coordenada e prepare exigem clone autenticado. Execute:
`python scripts/pipeline_control.py prepare <slug> --request-id <id>`.
O validator batch coleta/repara/revalida. Só PASS confirma request/metadata/bytes
por CAS e retorna commit_sha/next_action=wait_for_correlated_run.
Não crie .episode-check manual nem faça segundo commit/push do request.
Ausência de runtime/autenticação é blocker explícito, não permissão para bypass.

## Acompanhamento

Duplicate usa push. Media Preflight aceita push legado ou o dispatch explícito
de autoria/recovery, desde que slug, request_id e SHA preparado coincidam.
Acompanhe request_id + slug + SHA do request + workflow, nunca último run ou
HEAD posterior da coordenação. Para push de vários commits use before-sha real.
Poll/backoff observa queued/pending/waiting/requested/in_progress; timeout preserva
request/run e permite retomar a consulta, sem nova tentativa.

Media Preflight resolve visuais, revalida, renderiza, prepara capa/metadata,
faz dry-run e aprova bundle publish-ready. Queue selada por CAS tem três linhas:
slug, source_run_id, request_id. O job usa dispatch persistente e publisher
consome os mesmos bytes. Queue não é sucesso final e não é escrita pelo agente.

## Diagnóstico e recovery

Leia errors[], error_code, error_class, recoverable, stage, slug, request_id,
target, detail, commit_sha e diagnósticos/artefatos do run exato.
Erro genérico/exit code/falta de log não é causa raiz.
Repare todos os erros independentes elegíveis e revalide após mudança material.
Não reduza gates. Consulte [publishing-retry.md](../docs/publishing-retry.md).
Prepare: até 2 reparos internos. CI: até 10 passes internos. Recovery: até 3
ciclos reais por rodada, sem contar observação, respeitando limites acumulados.

READY_TO_QUEUE pertence ao workflow. QUEUED já proíbe mutar/reautorizar mídia;
observe/reconcilie receipt existente. PREPARED pode retomar só pelo protocolo;
SENDING/ambíguo exige observar mesmo run, nunca outro POST/rerun.

## Publisher iniciado

Tentativa é persistida antes de efeitos externos. Publisher iniciado ou possível,
parcial ou falho após início, implica:

```text
EVER_PUBLISHED_OR_ATTEMPTED=YES
REPUBLICATION_ALLOWED=NO
RECOVERY_MUTATION_ALLOWED=NO
```

Não remova reserva/tombstone, altere episódio, crie queue/retry, rerode publisher
ou complete plataforma faltante. Não crie substituto do mesmo slot.
O run original continua suas plataformas na mesma sessão; apenas observe.
Reconcile classifica confirmação/incerteza e fecha estado sem autorizar reenvio.

## Resultado obrigatório

Separe DUPLICATE, AUTHORSHIP, LOCAL PASS, MEDIA PREFLIGHT, RENDER, BUNDLE,
QUEUE, DISPATCH, PUBLISH ACTION, YOUTUBE, INSTAGRAM, FACEBOOK e TIKTOK.
PUBLICADO exige run terminal e evidências reais das plataformas configuradas.
EM_ANDAMENTO/NÃO_VERIFICADO preservam observação; parcial/incerto após tentativa
é PARTIAL_NO_TOUCH. DRAFT_ENVIADO não equivale a TikTok público confirmado.

Após TODA rodada entregue log conciso com data/hora local, repo/HEAD, slot,
candidatos/slug, coordinator/reconcile, request/generation/version, ações reais,
gates e plataformas, contadores compartilhados/autorados/ciclos/passes,
run/job/SHA, Sheet confirmado, flags irreversíveis, final_status,
erro concreto e next_action. Use N/A/NÃO_VERIFICADO onde faltar evidência.
Nunca declare escrita do Sheet ou sucesso sem confirmação.
Nunca pause/desative/delete/reagende automações durante a execução.
