# Publicação e retry da automação

A `main` é sempre a fonte da verdade. Este documento descreve o protocolo operacional atual para disparar a publicação automática de um episódio e, quando for seguro, corrigir o episódio e executar novamente a Action.

## Objetivo

A automação pode fazer no máximo **3 tentativas totais** para o mesmo episódio:

1. tentativa inicial pela queue;
2. primeiro retry após uma correção real do episódio;
3. segundo e último retry após uma nova correção real do episódio.

Nunca faça uma quarta tentativa automática.

## Tentativa inicial

Depois de todos os arquivos do episódio estarem completos e validados, crie por último:

```text
.publish-queue/<slug>.txt
```

O conteúdo deve ser exatamente:

```text
<slug>
```

Esse arquivo é o gatilho da primeira execução da Action.

## Retry com commit corrigido

Se a Action falhar e a falha for segura para retry, corrija primeiro os arquivos do episódio no branch `main`. Somente depois da correção crie um novo arquivo de retry.

Primeiro retry:

```text
.publish-retry/<slug>-retry-1.txt
```

Segundo e último retry:

```text
.publish-retry/<slug>-retry-2.txt
```

Em ambos os casos, o conteúdo do arquivo deve ser exatamente o slug original:

```text
<slug>
```

Cada retry é um arquivo novo e, portanto, dispara uma nova execução usando o commit mais recente da `main`, já contendo a correção. Não use rerun de uma execução antiga depois de alterar o episódio, porque a execução antiga pode continuar associada ao SHA anterior.

## Quando o retry automático é permitido

O retry automático só é permitido quando todas as condições abaixo forem verdadeiras:

- existe uma falha concreta e um log ou artefato de diagnóstico acessível que explique o erro;
- a falha ocorreu antes de qualquer etapa de publicação poder ter enviado o vídeo a uma plataforma;
- a correção pode ser feita somente nos arquivos do episódio `episodes/<slug>/`;
- a nova tentativa corresponde a uma alteração objetiva que corrige o erro anterior;
- ainda não foram consumidas as 3 tentativas totais.

Exemplos normalmente corrigíveis no episódio incluem incompatibilidades de `timeline.json`, `story.json`, `assets.json`, `post.json`, trims, referências a assets, enums, limites de cues, duração ou outras constraints explicitamente impostas pela `main`.

Nunca faça retry cego de publicação. Uma falha sem correção correspondente não justifica uma nova tentativa de publicação.

## Erro genérico nunca é causa terminal

Mensagens genéricas como `failure`, `Process completed with exit code 1`, `Validate and auto-repair episode media failed`, `MEDIA_PREFLIGHT_RESULT=FAIL`, nome da etapa em vermelho ou apenas o status terminal do job **não são causa raiz** e **não autorizam o agente a parar e devolver o controle ao usuário**.

Antes de concluir que uma falha não pode ser corrigida, o agente deve esgotar, nesta ordem, o diagnóstico disponível para a execução exata:

1. localizar o `run_id`, `job_id` e a etapa exata que falhou;
2. ler o **log bruto/completo do job** e procurar a mensagem imediatamente anterior ao exit code, traceback, `ERROR`, `Exception`, validator detail, target/asset/URL/arquivo ou outro motivo concreto;
3. consultar o artefato `media-preflight-diagnostics-<run_id>` e seu `final-result.txt` quando existir;
4. consultar os `attempt-*.log` do mesmo artefato quando `final-result.txt` ainda estiver genérico;
5. cruzar a causa encontrada com os arquivos do mesmo slug e com a versão da `main` usada naquele run;
6. somente depois disso decidir entre corrigir, retry seguro ou bloqueio real.

É proibido transformar o **nome da etapa que falhou** em `ERRO/BLOQUEIO`. O relatório deve trazer a causa concreta encontrada nos logs/artefatos, por exemplo arquivo, asset, URL, constraint, traceback, autenticação, permissão ou indisponibilidade externa.

Se o endpoint de logs falhar, estiver truncado ou não expuser a causa, isso **não encerra o diagnóstico**: use obrigatoriamente o artefato persistente. Se o artefato também não trouxer a causa e a falha for de **Media Preflight antes da queue**, é permitido disparar **uma única rechecagem diagnóstica adicional do mesmo slug com nonce novo**, sem criar outro episódio e sem criar queue, para tentar obter diagnóstico completo. Essa rechecagem puramente diagnóstica não é uma tentativa de publicação e não autoriza loop infinito.

Somente depois de esgotar essa escada de diagnóstico pode existir um bloqueio por diagnóstico inacessível. Nesse caso, reporte explicitamente `DIAGNÓSTICO_INACESSÍVEL` e quais fontes foram tentadas; nunca reporte apenas `preflight failed`, `step failed` ou `exit code 1` como se fossem a causa.

## Quando NÃO fazer retry automático

Pare e reporte a falha quando:

- alguma etapa de publicação já iniciou, concluiu ou pode ter concluído;
- não for possível confirmar com segurança se YouTube ou Instagram já receberam o vídeo;
- a falha for de autenticação, segredo, permissão, indisponibilidade externa, infraestrutura ou código global da engine/publishing;
- a correção exigiria alterar `engine/`, `publishing/`, `config/`, `.github/`, catálogos globais ou episódios anteriores;
- depois de esgotar a escada obrigatória de diagnóstico acima, ainda não existir causa concreta ou correção segura identificável;
- a terceira tentativa já tiver falhado.

A prioridade é evitar publicação duplicada. Uma mensagem genérica de workflow, isoladamente, nunca satisfaz os critérios acima.

## Sequência operacional

```text
criar episódio
    ↓
validação pré-queue
    ↓
.publish-queue/<slug>.txt
    ↓
ACTION — tentativa 1
    ↓
SUCESSO → encerrar
    ↓ falha segura antes da publicação
ler log bruto + diagnóstico → identificar causa concreta → corrigir episódio
    ↓
.publish-retry/<slug>-retry-1.txt
    ↓
ACTION — tentativa 2
    ↓
SUCESSO → encerrar
    ↓ falha segura antes da publicação
ler log bruto + diagnóstico → identificar causa concreta → corrigir episódio
    ↓
.publish-retry/<slug>-retry-2.txt
    ↓
ACTION — tentativa 3
    ↓
SUCESSO → encerrar
FALHA → esgotar diagnóstico → parar e reportar causa concreta
```

## Regras de escrita

- Nunca recrie ou sobrescreva a queue original para tentar novamente.
- Nunca modifique um arquivo de retry já criado para disparar outra execução.
- `retry-1` e `retry-2` são os únicos retries automáticos aceitos pelo workflow atual.
- Todos os arquivos do episódio devem ser corrigidos antes de criar o respectivo arquivo de retry.
- Se uma escrita de queue/retry retornar sucesso, não repita a mesma escrita.

## Diagnóstico persistente do media preflight

Cada execução de `Episode media preflight` preserva os logs de cada tentativa em um artefato do próprio run chamado:

```text
media-preflight-diagnostics-<run_id>
```

O artefato contém:

```text
ci-diagnostics/media-preflight-<run_id>/attempt-1.log
ci-diagnostics/media-preflight-<run_id>/attempt-2.log
...
ci-diagnostics/media-preflight-<run_id>/final-result.txt
```

`final-result.txt` é a fonte rápida e machine-readable para o resultado terminal. Em falhas ele registra, quando disponível, `MEDIA_PREFLIGHT_ERROR_CODE`, `MEDIA_PREFLIGHT_ERROR_CLASS`, `MEDIA_PREFLIGHT_ERROR_DETAIL`, `MEDIA_PREFLIGHT_RECOVERABLE`, `MEDIA_PREFLIGHT_TARGET`, `MEDIA_PREFLIGHT_RECOMMENDED_ACTION` e `MEDIA_PREFLIGHT_SAME_SLUG`.

O **log bruto do job é a primeira fonte para investigação detalhada**. O artefato persistente é a segunda fonte obrigatória e o fallback quando o endpoint normal de job logs estiver indisponível, truncado ou difícil de consumir. Não conclua que o erro é desconhecido enquanto alguma dessas fontes da execução exata ainda não tiver sido consultada.

Os logs persistentes são apenas diagnóstico; eles não autorizam troca de slug, retry cego ou criação de queue sem `MEDIA_PREFLIGHT_RESULT=PASS`.

## Acompanhamento da Action

Quando a conexão GitHub permitir, acompanhe a execução até obter um estado conclusivo. Use jobs, logs reais e os artefatos persistentes de diagnóstico; não invente resultado de render, publicação ou score visual.

Uma falha de step/job deve ser tratada primeiro como **evento a investigar**, não como resposta final. Enquanto a falha for anterior à publicação e houver uma ação diagnóstica ou correção segura restante, continue no mesmo slug sem devolver o controle ao usuário.

Se a execução ainda estiver em andamento e não houver mecanismo apropriado para continuar acompanhando dentro da mesma execução do agente, reporte o estado real disponível. Não trate ausência de evidência como sucesso.

## Segurança de publicação

No workflow atual, renderização e preparação acontecem antes das etapas de publicação. Uma falha comprovadamente anterior à primeira etapa de publicação pode ser elegível a retry. Depois que a execução alcançar uma etapa de publicação, a decisão deve ser conservadora: não iniciar automaticamente outra publicação sem confirmar o estado real das plataformas.

A existência de `.publish-retry/` não torna o publisher idempotente. Ela apenas garante que uma nova Action use a `main` corrigida. A proteção contra duplicidade continua sendo responsabilidade do agente ao decidir se é seguro criar o próximo retry.
