# Regra autoritativa — um episódio válido por execução

Esta regra define o contrato de uma execução do Além do Hit / Music Short Factory. Em conflitos sobre identidade da execução, troca de candidato, continuidade e critério de saída, esta regra prevalece sobre instruções editoriais antigas. A `main` continua sendo a fonte da verdade para contratos técnicos.

## 1. Contrato da execução

**Uma execução = uma invocação do agendamento cujo objetivo é entregar EXATAMENTE 1 episódio NOVO, válido e publicável para o slot atual, sempre que tecnicamente possível.**

A execução NÃO é considerada concluída só porque um candidato foi escolhido, autorado, bloqueado ou reprovado. Falha de candidato não é automaticamente falha do job.

No início registre `EXECUTION_START_HEAD`, `QUEUES_AT_START`, `EXECUTION_SLUG=UNSET` e `DELIVERED_EPISODE=NO`.

## 2. Anti-duplicação absoluta

Nunca publique novamente episódio/slug que já tenha qualquer evidência de publisher iniciado, concluído, parcial, falho após início ou incerto. Esse slug é `CLOSED_HISTORY`.

Duplicate de candidata descarta somente a candidata e obriga a continuar o pool. Episódio já publicado nunca satisfaz uma nova execução.

## 3. Candidato ativo e substituição segura

`EXECUTION_SLUG` identifica o candidato ativo, não uma prisão irreversível da chamada.

Depois de iniciar autoria, tente reparar o MESMO slug enquanto houver correção materialmente segura e razoável. Porém, se esse candidato se tornar **PRE_PUBLISH_UNRECOVERABLE** e houver evidência positiva de `EVER_PUBLISHED_OR_ATTEMPTED=NO`, ele pode ser abandonado sem publicação e a execução DEVE voltar à seleção editorial para criar um NOVO candidato.

Considere `PRE_PUBLISH_UNRECOVERABLE` quando, antes de qualquer publisher de plataforma iniciar, o candidato falhar definitivamente em gate editorial/técnico, media preflight, assets, render/schema ou outra validação e as tentativas/correções seguras previstas tiverem sido esgotadas ou a causa tornar aquele candidato inviável.

Ao abandonar candidato pré-publicação:
- marque-o `ABANDONED_PRE_PUBLISH`;
- nunca crie queue para ele depois;
- limpe somente a identidade operacional do candidato: `EXECUTION_SLUG=UNSET`;
- volte ao pool e escolha tema realmente novo;
- refaça duplicate/history checks completos;
- continue a MESMA execução.

Isso NÃO é permitido se qualquer publisher já iniciou ou puder ter iniciado.

## 4. Limites

A execução pode avaliar até 15 candidatas e pode autorar candidatos substitutos quando necessário, mas deve publicar **no máximo 1 episódio**. O objetivo não é gerar vários episódios; é obter um único episódio válido para o slot.

Use no máximo 5 candidatos autorados/substitutos por execução, salvo regra mais restritiva da main. Para cada candidato, respeite os limites técnicos de reparo/preflight da main.

## 5. Publicação fecha a possibilidade de substituição

Imediatamente antes da queue, reconstrua novamente todo o histórico do slug. Só publique se `EVER_PUBLISHED_OR_ATTEMPTED=NO`.

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
- `FAIL pré-publicação irrecuperável -> abandonar candidato -> novo tema -> duplicate check -> autorar -> validar`

## 8. Regra de ouro

**O JOB ENTREGA UM EPISÓDIO; ELE NÃO ENTREGA UMA TENTATIVA.**

**FALHA DE CANDIDATO != FALHA DA EXECUÇÃO.**

**DUPLICATE = DESCARTAR E CONTINUAR.**

**PRE_PUBLISH_UNRECOVERABLE + NENHUM PUBLISHER INICIADO = SUBSTITUIR POR NOVO CANDIDATO.**

**QUALQUER PUBLISHER INICIADO = ZERO REPUBLICAÇÃO E ZERO SUBSTITUTO AUTOMÁTICO PARA O MESMO SLOT.**

**NO MÁXIMO 1 EPISÓDIO PUBLICADO POR EXECUÇÃO.**