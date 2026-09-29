# Regra autoritativa — um episódio válido por execução

Esta regra define o contrato de uma execução do Além do Hit / Music Short Factory. Em conflitos sobre identidade da execução, troca de candidato, continuidade e critério de saída, esta regra prevalece sobre instruções editoriais antigas. A `main` continua sendo a fonte da verdade para contratos técnicos.

## 1. Contrato da execução

**Uma execução = uma invocação do agendamento cujo objetivo é entregar EXATAMENTE 1 episódio NOVO, válido e publicável para o slot atual, sempre que tecnicamente possível.**

A execução NÃO é considerada concluída só porque um candidato foi escolhido, autorado, bloqueado ou reprovado. Falha de candidato não é automaticamente falha do job.

No inicio registre `EXECUTION_START_HEAD`, `request_id`, `EXECUTION_SLUG` e `DELIVERED_EPISODE=NO` conforme a autoridade compartilhada.

## 2. Anti-duplicação absoluta

Nunca publique novamente episódio/slug que já tenha qualquer evidência de publisher iniciado, concluído, parcial, falho após início ou incerto. Esse slug é `CLOSED_HISTORY`.

Duplicate de candidata descarta somente a candidata e obriga a continuar o pool. Episódio já publicado nunca satisfaz uma nova execução.

## 3. Candidato ativo, estado compartilhado e substituicao segura

<!-- pipeline-contract: config/pipeline-contract.json -->

Contrato tecnico obrigatorio: [`docs/pipeline-contract.md`](../docs/pipeline-contract.md), baseado em [`config/pipeline-contract.json`](../config/pipeline-contract.json). Use o estado persistido e os triggers reais antes de decidir continuidade.

Consulte `python scripts/pipeline_control.py status` e a autoridade compartilhada para obter `request_id`, `EXECUTION_SLUG` e `can_create_new_episode`. Retome o slug ativo do canal. A listagem de queues pode ser truncada e nunca prova ausencia de trabalho ou autorizacao para outro episodio.

Depois de iniciar autoria, repare o mesmo slug enquanto houver correcao segura. Se ele se tornar inviavel antes da publicacao, so abandone e substitua quando a autoridade compartilhada confirmar por CAS o encerramento da reserva, nenhum publisher tiver iniciado ou puder ter iniciado e `can_create_new_episode` for verdadeiro. Sem essa confirmacao, preserve o slug e reporte o bloqueio concreto; nao crie outro candidato para o mesmo slot. Um candidato abandonado nunca deve receber queue posteriormente.

## 4. Limites

A execução pode avaliar até 15 candidatas e pode autorar candidatos substitutos quando necessário, mas deve publicar **no máximo 1 episódio**. O objetivo não é gerar vários episódios; é obter um único episódio válido para o slot.

Use no máximo 5 candidatos autorados/substitutos por execução, salvo regra mais restritiva da main. Para cada candidato, respeite os limites técnicos de reparo/preflight da main.

## 5. Publicação fecha a possibilidade de substituição

Imediatamente antes da queue, valide generation, request e reserva na autoridade compartilhada. So publique se `EVER_PUBLISHED_OR_ATTEMPTED=NO` e o CAS permitir.

No instante em que qualquer publisher de plataforma iniciar ou puder ter iniciado:
- `EVER_PUBLISHED_OR_ATTEMPTED=YES`;
- `REPUBLICATION_ALLOWED=NO`;
- o slug fica fechado para retry/republicação automática conforme as regras conservadoras vigentes;
- **não crie episódio substituto para o mesmo slot**, pois o slot já teve tentativa real de publicação e criar outro pode gerar duplicação editorial.

## 6. Critério de saída

A execução só pode terminar normalmente quando ocorrer um destes estados:

1. `SUCCESS/PUBLICADO`: um episódio novo desta execução passou pelos gates e chegou ao estado terminal exigido pela main;
2. `PARTIAL_NO_TOUCH`: publisher iniciou e o resultado ficou parcial/misto/incerto; não tocar novamente;
3. `HARD_FAILURE/BLOCKED`: bloqueio externo/técnico real impede continuar, ou todos os limites globais de candidatas/substitutos foram realmente esgotados.

**É proibido usar `MEDIA PREFLIGHT: FAIL/BLOQUEADO`, gate semântico reprovado, asset ruim ou candidato inviável como resultado final do job enquanto ainda for pré-publicação e houver capacidade de selecionar outro candidato.**

## 7. Fluxo obrigatório

`início -> selecionar -> duplicate check -> autorar -> validar ->`

- `PASS -> queue -> publicação -> terminal -> fim`
- `FAIL recuperável -> reparar mesmo candidato -> revalidar`
- `FAIL pre-publicacao irrecuperavel -> reconciliar e encerrar reserva por CAS -> novo tema somente se autorizado -> duplicate check -> autorar -> validar`

## 8. Regra de ouro

**O JOB ENTREGA UM EPISÓDIO; ELE NÃO ENTREGA UMA TENTATIVA.**

**FALHA DE CANDIDATO != FALHA DA EXECUÇÃO.**

**DUPLICATE = DESCARTAR E CONTINUAR.**

**PRE_PUBLISH_UNRECOVERABLE + NENHUM PUBLISHER INICIADO + CAS DE LIBERACAO CONFIRMADO = PODE SUBSTITUIR.**

**QUALQUER PUBLISHER INICIADO = ZERO REPUBLICAÇÃO E ZERO SUBSTITUTO AUTOMÁTICO PARA O MESMO SLOT.**

**NO MÁXIMO 1 EPISÓDIO PUBLICADO POR EXECUÇÃO.**