# Regra obrigatória — conclusão real da publicação

<!-- pipeline-contract: config/pipeline-contract.json -->

Contrato técnico obrigatório: [`docs/pipeline-contract.md`](../docs/pipeline-contract.md), baseado em [`config/pipeline-contract.json`](../config/pipeline-contract.json). Use o estado persistido e os triggers reais antes de decidir continuidade.

## 1. Continuidade e identidade

Execute `python scripts/pipeline_control.py status` para consultar a autoridade
compartilhada; o índice local é cache. Respeite `next_action`,
`mutation_allowed`, `republication_allowed` e `can_create_new_episode`.
Retome trabalho ativo no mesmo slug; uma chamada editorial não cria outro
episódio. Listagem truncada, sozinha, nunca autoriza `BLOQUEADO`.

## 2. Validar antes de solicitar a Action

Execute `python scripts/pipeline_control.py prepare <slug> --request-id <id>`.
Esse caminho compartilha o validator batch com o Media Preflight, corrige em lote
antes do request e só produz `.episode-check` após PASS local. Antes de aceitar
cada asset/slot, valide acesso, mídia, metadata, referências, duração, trims,
fontes e a regra de primeiro take do projeto. Complete o pool se faltarem opções.

Erro determinístico exige reparo real antes de nova validação. Nenhum retry cego,
nenhum relaxamento de validator. Use a [taxonomia](../docs/media-preflight-errors.md)
e o [protocolo de recuperação](../docs/publishing-retry.md).

## 3. Trigger e acompanhamento

O CAS remoto do prepare confirma o request e devolve o SHA que aciona push.
Não crie um segundo commit/push do mesmo request; acompanhe o SHA devolvido.
Não exija `workflow_dispatch` de Duplicate/Media Preflight. Acompanhe a combinação
`request_id + slug + commit SHA + workflow`; nunca o último workflow run.

A Action pode demorar a aparecer. `queued`, `pending`, `waiting`, `requested` e
`in_progress` são transitórios: use polling/backoff com timeout. Ao expirar,
preserve o request e reporte o estado real não verificado/em andamento.

A queue registra slug, `source_run_id` e `request_id` do bundle aprovado. Ela é criada pelo
preflight depois do render/dry-run, e não encerra a execução. O preflight faz
dispatch explícito do publisher para o caso de push com `GITHUB_TOKEN`.

## 4. Diagnóstico em lote

Leia o resultado estruturado da execução exata e artefatos disponíveis.
`failure`, `exit code 1` e nome de step isolados nunca são causa raiz. Preserve
`error_code`, `error_class`, `recoverable`, `stage`, `slug`, `request_id`, `target`,
`detail`, `errors[]` e `commit_sha`. Se necessário, reproduza os validators do
mesmo commit; ausência do log bruto não impede diagnóstico.

Repare todos os itens independentes recuperáveis e revalide o lote inteiro. Um
item externo ou que exige autoria não deve impedir reparos locais possíveis.
Pool sem candidatos adequados exige novas fontes, não uma seleção inventada.

## 5. Publicação e proteção contra duplicação

Antes da primeira chamada de publisher existe uma reserva persistida em
`.publication-attempts/<slug>.json`. Se algum publisher iniciou ou pode ter
atingido uma plataforma, mantenha obrigatoriamente:

```text
EVER_PUBLISHED_OR_ATTEMPTED=YES
REPUBLICATION_ALLOWED=NO
recovery_mutation_allowed=NO
```

Não remova a reserva nem republique ou altere o episódio após esse ponto. Consulte
o resultado real nas plataformas. Os retries legados são permitidos somente
antes dessa reserva e depois de correção comprovada, conforme o controlador.

## 6. Estado final verificável

Separe `MEDIA PREFLIGHT`, `QUEUE`, `PUBLISH ACTION`, `YOUTUBE`, `INSTAGRAM`,
`FACEBOOK` e `TIKTOK`. Só reporte `STATUS: PUBLICADO` quando a Action terminou e
as plataformas realmente executadas têm sucesso comprovado. Queue/PASS local
não provam publicação. Use `PUBLICAÇÃO_EM_ANDAMENTO` para trabalho transitório
e `PUBLICAÇÃO_NÃO_VERIFICADA` se faltar evidência para determinar o resultado.

YouTube pode informar `PUBLICADO_PUBLICO`/`PUBLICADO`; TikTok pode informar
`DRAFT_ENVIADO` quando esse é o resultado real. Não invente sucesso por plataforma.

## 8. Formato final obrigatório

```text
STATUS: <PUBLICADO | PUBLICAÇÃO_EM_ANDAMENTO | PUBLICAÇÃO_NÃO_VERIFICADA | FALHA_PUBLICAÇÃO | SEM_CANDIDATO | BLOQUEADO | FALHA>
TEMA: <assunto central | N/A>
ARTISTA/BANDA: <nome | N/A>
MÚSICA RELACIONADA: <música principal se houver | N/A>
SLUG: <slug | N/A>
PALAVRAS: <número | N/A>
DURAÇÃO ALVO: <segundos/faixa | N/A>
TAKES: <quantidade final | N/A>
POOL VISUAL: <resumo | N/A>
VÍDEOS BASE: <resumo | N/A>
IMAGENS BASE: <resumo | N/A>
VISUAL SCORES: <resumo | N/A>
BACKGROUND: <resumo | N/A>
SFX CATÁLOGO: <resumo | N/A>
EDIÇÃO: <resumo | N/A>
COMMIT: <hash/identificador ou N/A>
MEDIA PREFLIGHT: <SUCESSO | FALHA | EM_ANDAMENTO | NÃO_VERIFICADO | BLOQUEADO>
QUEUE: <CRIADA | NÃO_CRIADA | BLOQUEADA>
PUBLISH ACTION: <SUCESSO | FALHA | EM_ANDAMENTO | NÃO_VERIFICADO | BLOQUEADO>
YOUTUBE: <status real>
INSTAGRAM: <status real>
FACEBOOK: <status real>
TIKTOK: <status real>
RETRIES: <número de novas validações/publicações; não quantidade de itens corrigidos | N/A>
ERRO/BLOQUEIO: <causa concreta ou resumo do lote; nunca apenas nome genérico de step/status | NENHUM>
```

## 9. Regra de ouro

**Uma passada de preflight = descobrir o máximo de erros independentes possível.**

**Lote recuperável = corrigir todos os itens conhecidos antes da próxima validação.**

**Falha elegível = investigar por metadados/outputs/artefato ou reprodução do validator, identificar causa concreta, corrigir e tentar novamente.**

**Ausência do antigo log bruto != diagnóstico inacessível.**

**Ausência de workflow_dispatch != bloqueio quando o workflow atual é acionado por push.**

**Erro genérico de Action = continuar diagnóstico; não encerrar.**

**Queue criada = publicação solicitada.**

**Publish Action terminal + plataformas verificadas = publicação concluída.**
