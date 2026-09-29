# Publicação, diagnóstico e recuperação

<!-- pipeline-contract: config/pipeline-contract.json -->

O [contrato operacional](pipeline-contract.md) e
[`config/pipeline-contract.json`](../config/pipeline-contract.json) definem triggers,
estados e próximos passos. Consulte `python scripts/pipeline_control.py status
--slug <slug>` antes de recuperar. Não reconstrua continuidade por listagens
completas ou pelo último run disponível.

## Antes da publicação

`prepare` valida localmente, coleta erros independentes e aplica reparos em lote
antes de confirmar `.episode-check` pelo CAS remoto. Acompanhe o SHA devolvido:
`request_id + slug + commit SHA + workflow`. Ausência de `workflow_dispatch`,
Action ainda não visível e status transitório não significam bloqueio.

A Action de Media Preflight resolve visuais, renderiza, prepara metadata/capa,
faz dry-run e aprova o bundle `publish-ready-<slug>`. Só então cria
`.publish-queue/<slug>.txt`, com estas três linhas:

```text
<slug>
<source_run_id>
<request_id>
```

O preflight também faz dispatch explícito do publisher porque o push da queue
feito com `GITHUB_TOKEN` não dispara outro workflow. Isso não torna dispatch
obrigatório nos workflows Duplicate e Media Preflight, cujo trigger é push.

## Diagnosticar e reparar

1. Consulte estado, diagnóstico e metadados do run exato.
2. Leia `errors[]`, `error_code`, `error_class`, `recoverable`, `stage`, `slug`,
   `request_id`, `target`, `detail` e `commit_sha`. Markers legados como
   `MEDIA_PREFLIGHT_ERRORS_JSON` e `MEDIA_PREFLIGHT_ERROR_ITEM` continuam úteis.
3. Use o artefato `media-preflight-diagnostics-<run_id>` e `final-result.txt` quando
   disponíveis. Logs brutos não são pré-requisito. Reproduza o validator no mesmo
   slug/SHA quando necessário.
4. Repare todos os itens independentes recuperáveis. Um item externo não deve
   ocultar reparos locais possíveis.
5. Revalide o lote inteiro. Sem mudança concreta, erro determinístico não recebe
   retry. Falhas externas transitórias admitem espera/backoff conforme diagnóstico.
   Erro desconhecido exige causa concreta.

A [taxonomia dos validators](media-preflight-errors.md) distingue seleção,
timeline, mídia local, reparáveis, externos e não recuperáveis. Um pool sem
alternativa válida exige completar a autoria; nunca baixar um gate.

## Retentativa antes do publisher

O workflow reconhece `.publish-retry/<slug>-retry-1.txt`,
`.publish-retry/<slug>-retry-2.txt` e `.publish-retry/<slug>-retry-3.txt` por
compatibilidade. Esses nomes não autorizam uma segunda chamada à API depois que
um publisher iniciou; a reserva central prevalece sobre todos os triggers.

Um retry só é elegível se estado e evidência provarem que nenhuma plataforma
pôde receber o vídeo, não existir reserva de tentativa e a causa tiver sido
corrigida. Reutilize o bundle aprovado com o mesmo `source_run_id` e `request_id`.
Não altere mídia/metadata de uma queue já entregue: isso exigiria cancelar e
reconciliar primeiro todas as execuções que ainda podem consumi-la. Não existe
reset automático de `QUEUED`. Nunca sobrescreva queue ou request já entregue.

O job de queue usa `scripts/dispatch_publication.py`. Uma intenção durável em
`.pipeline/dispatch/<slug>--<request_id>.json` precede o dispatch. Se a fila foi
gravada mas o dispatch ainda não começou, o mesmo job pode retomá-lo. Resposta
aceita ou ambígua só permite observar o mesmo pedido, identificado também no
`run-name`; não permite outro POST. Rejeição explícita da API permite retomar
somente após corrigir a causa. O polling geral continua no controlador.

Depois de falha do preflight externo, o workflow registra `VALIDATION_FAILED`
somente para o request/run correspondente. Uma queue ou tentativa de publicação
nunca é revertida por esse finalizador.

## Reserva após início do publisher

Antes da primeira chamada de publicação, inclusive pelo CLI local, o fluxo exige
autenticação GitHub e persiste uma reserva central em
`.publication-attempts/<slug>.json`. O estado passa a informar:

```text
EVER_PUBLISHED_OR_ATTEMPTED=YES
REPUBLICATION_ALLOWED=NO
recovery_mutation_allowed=NO
```

A existência de `.publish-retry/` não torna uma API idempotente. Reserva e estado
impedem automaticamente outra tentativa. Não apague o marker, não force rerun nem
altere o episódio para contornar esse bloqueio. Resposta de rede ambígua pode
esconder upload concluído: consulte a plataforma e preserve a proibição de
republicar e de mutar.

## Conclusão verificável

Queue significa publicação solicitada. Registre separadamente Media Preflight,
queue, Publish Action e resultado de cada plataforma. `queued`, `pending`,
`waiting`, `requested` e `in_progress` pedem polling com backoff. Timeout é estado
não verificado/em andamento, preservando o request para retomar a consulta.

Falha genérica ou ausência de log não é causa raiz; upload iniciado não prova
sucesso. Só reporte publicação concluída com evidência real terminal.



## Recovery distribuído após crash

Execute `pipeline_control.py reconcile --slug <slug> --repository <owner/repo>`.
PREPARED admite takeover após expiração e CAS; SENDING exige observar o mesmo
run. Encerramento conservador DISPATCH_UNCERTAIN libera o canal e impede o mesmo
slug de publicar. Listagem vazia/incompleta não autoriza repetir POST.

No publisher local, confirmed_not_sent exige versão ainda prepared. Depois de
sending, só prova positiva permite confirmed_sent; casos sem prova são uncertain,
sem republicação. Tombstones antigos valem mesmo com outro episódio no canal.
