# Regra autoritativa — um episódio válido por slot

<!-- pipeline-contract: config/pipeline-contract.json -->

Siga [docs/automation-protocol.md](../docs/automation-protocol.md), o
[contrato técnico](../docs/pipeline-contract.md) e
[config/pipeline-contract.json](../config/pipeline-contract.json).
Creator e recovery cooperam no mesmo canal `default` e conservam o vínculo
slot/slug/request. Código da main define autoridade, transições e comandos reais.

## Entrega e continuidade

O objetivo da invocação criadora é exatamente um episódio novo válido sempre
que tecnicamente possível; no máximo uma publicação por slot. Recovery completa
o slot criador mais recente vencido e elegível. Slot já entregue/tentado não
recebe substituto. Trabalho ativo anterior tem prioridade no canal.
Falha de candidata não é falha final do slot se houver continuação autorizada.

Registre EXECUTION_START_HEAD, slot, EXECUTION_SLUG/request_id quando definidos,
DELIVERED_EPISODE e checkpoints no Sheet. Slot não é campo nativo do schema.
Consulte `pipeline_control.py status --channel default` e estado exato do slug.
Não decida continuidade por diretórios, queues, último run ou Sheet isolado.

## Reserva e contadores

A candidata recebe reserva pelo duplicate guard/CAS acionado por request único
`.duplicate-check/<slug>--<request_id>.json`. Autoria só após
UNIQUE_CANDIDATE; RESUME_EXISTING_EPISODE exige seguir a reserva indicada.
DUPLICATE_CANDIDATE fecha a candidata e permite próxima somente após canal livre.
Ausência inicial da authority pode ser inicializada pelo guard existente, nunca
por arquivo manual. Não pule o guard usando start/new_episode para outro slug.

Até 15 candidatas na candidate_window compartilhada desde a última queue, não
15 por invocação. Até 5 autorados/substitutos por slot, somando creator/recovery.
No recovery, até 5 ciclos materiais por rodada; prepare tem até 5 reparos
internos (6 validações) e Media Preflight até 10 passes internos atualmente. Não resete contador
ou confunda passes com reruns. Código atual/limite mais restritivo prevalece.

## Substituição segura

Repare o mesmo slug enquanto houver correção segura antes de queue/dispatch.
Candidata inviável pode ser cancelada pelo coordinator com evidência positiva
de zero publisher/dispatch possível, identidade/fence atuais e fechamento por CAS.
O grafo de transition não oferece CANCELLED a partir de autoria/validation;
use somente APIs existentes de finish/abandon_expired com suas precondições.
Preserve tombstone, reconcilie e releia canal livre antes de outro candidato.
Sem fechamento comprovado não troque slug.

O slug `pedro_sampaio_ricky_martin_pikito_pikito_nfl` está abandonado: nunca
reparar/renderizar para publicação/queue/publicar ou ressuscitar.

## Queue e publicação

Prepare confirma request por CAS depois de PASS local; não crie .episode-check
manual ou faça segundo commit/push. Preflight aprova render/dry-run/bundle;
o job sela queue com slug/source_run_id/request_id e usa dispatch persistente.
QUEUED já bloqueia mutação; observe/reconcilie, sem escrever queue/retry.

Qualquer publisher iniciado/possivelmente iniciado fecha o slug:
EVER_PUBLISHED_OR_ATTEMPTED=YES, REPUBLICATION_ALLOWED=NO,
RECOVERY_MUTATION_ALLOWED=NO. Zero nova sessão/rerun/retry, plataforma faltante
ou substituto do mesmo slot. O run original pode concluir suas plataformas;
agente só observa. Canal liberado após fechamento admite slot futuro, não
substituto de um slot tentado.

## Saída verificável

PUBLICADO exige evidência terminal real. PARTIAL_NO_TOUCH fecha a tentativa sem
republicação. EM_ANDAMENTO/NÃO_VERIFICADO preservam request e observação.
BLOQUEADO/LIMITES_ESGOTADOS exigem causa real ou limite persistido esgotado.
SLOT_JA_ENTREGUE/SLOT_TENTADO_NO_TOUCH são no-op, não outra criação.

Candidata, commit, PASS ou queue não provam entrega. Reporte o log obrigatório
do protocolo comum e atualize o Sheet em cada transição. Nunca altere
schedule/enabled state ou desative agendamentos sem pedido explícito do usuário.
