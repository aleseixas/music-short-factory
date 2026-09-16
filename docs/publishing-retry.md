# Publicação e retry da automação

A `main` é sempre a fonte da verdade. Este documento descreve o protocolo operacional atual para disparar a publicação automática de um episódio e, quando for seguro, diagnosticar, corrigir o episódio e executar novamente a Action.

## Objetivo

A automação pode fazer no máximo **3 tentativas totais** para o mesmo episódio:

1. tentativa inicial pela queue;
2. primeiro retry após uma correção real do episódio;
3. segundo e último retry após uma nova correção real do episódio.

Nunca faça uma quarta tentativa automática.

## Tentativa inicial

O workflow `Episode media preflight` cria por último, e somente depois do render e de
todas as validações locais:

```text
.publish-queue/<slug>.txt
```

O conteúdo registra o artefato imutável aprovado:

```text
<slug>
<source_run_id>
```

`source_run_id` é a execução do preflight que contém `publish-ready-<slug>`. O
preflight faz o dispatch explícito de `Publish episode`; o push da queue feito pelo
`GITHUB_TOKEN` não é usado como único gatilho.

## Retry com commit corrigido

Se `Publish episode` falhar e a falha for segura para retry, reutilize o mesmo bundle
validado. Alterar apenas os arquivos do episódio na `main` não altera os bytes que
serão publicados.

Primeiro retry:

```text
.publish-retry/<slug>-retry-1.txt
```

Segundo e último retry:

```text
.publish-retry/<slug>-retry-2.txt
```

Em ambos os casos, a primeira linha deve ser o slug original. Sem segunda linha, o
workflow recupera `source_run_id` da queue original. Se uma correção de mídia,
render, capa ou metadata for realmente necessária, execute antes um novo `Episode
media preflight` e coloque o novo `source_run_id` na segunda linha do retry. Nunca
publique um arquivo modificado que não tenha passado pelo novo preflight.

## Quando o retry automático é permitido

O retry automático só é permitido quando todas as condições abaixo forem verdadeiras:

- existe uma causa concreta identificada por alguma fonte diagnóstica acessível;
- a falha ocorreu antes de qualquer etapa de publicação poder ter enviado o vídeo a uma plataforma;
- a correção pode ser feita somente nos arquivos do episódio `episodes/<slug>/`;
- a nova tentativa corresponde a uma alteração objetiva que corrige o erro anterior;
- ainda não foram consumidas as 3 tentativas totais.

Exemplos normalmente corrigíveis incluem incompatibilidades de `timeline.json`, `story.json`, `assets.json`, `post.json`, trims, referências a assets, enums, limites de cues, duração ou outras constraints explicitamente impostas pela `main`.

Nunca faça retry cego de publicação.

## Erro genérico nunca é causa terminal

Mensagens como `failure`, `Process completed with exit code 1`, `Validate and auto-repair episode media failed`, `MEDIA_PREFLIGHT_RESULT=FAIL`, nome da etapa em vermelho ou somente o status terminal do job **não são causa raiz**.

### Escada de diagnóstico sem dependência de log bruto

O acesso ao antigo log bruto/completo do job **não é mais requisito** e não deve ser tratado como fonte obrigatória. Se algum log textual do job ainda estiver acessível pela ferramenta atual, ele pode ser usado como evidência complementar, mas a automação não deve bloquear nem encerrar o diagnóstico por sua ausência.

Antes de concluir que uma falha não pode ser corrigida, esgote nesta ordem o que estiver acessível para a execução exata:

1. identifique `run_id`, `job_id`, workflow, commit/branch, conclusão do job e a etapa exata que falhou usando os metadados estruturados da Action;
2. procure saídas estruturadas, summaries, annotations, mensagens de step e campos emitidos pelo workflow, principalmente `MEDIA_PREFLIGHT_ERROR_COUNT`, `MEDIA_PREFLIGHT_ERRORS_JSON`, `MEDIA_PREFLIGHT_ERROR_ITEM`, `MEDIA_PREFLIGHT_ERROR_CODE`, `MEDIA_PREFLIGHT_ERROR_CLASS`, `MEDIA_PREFLIGHT_ERROR_DETAIL`, `MEDIA_PREFLIGHT_TARGET`, `MEDIA_PREFLIGHT_RECOVERABLE` e equivalentes existentes na `main`;
3. consulte o artefato `media-preflight-diagnostics-<run_id>` quando ele estiver acessível; leia primeiro `final-result.txt` e depois os `attempt-*.log` necessários;
4. se o artefato não puder ser baixado/lido pela ferramenta disponível, **reproduza o diagnóstico pelo código**: leia a versão exata de `.github/workflows/episode-media-preflight.yml`, os scripts/validators chamados por ele e os arquivos do mesmo `episodes/<slug>/`; aplique as mesmas regras determinísticas ao episódio para localizar arquivo, asset, URL, enum, cue, duração, background ou constraint incompatível;
5. confira também URLs/assets remotos e metadados que o validator atual realmente verifica, sem inventar inspeções que a ferramenta não realizou;
6. cruze todas as causas com o mesmo slug e com o SHA/versão da `main` usada na execução que falhou;
7. se ainda não houver causa concreta e a falha for de **Media Preflight antes da queue**, use uma única rechecagem diagnóstica adicional do mesmo slug com nonce novo, sem alterar artificialmente o episódio e sem criar queue, para produzir nova evidência estruturada/persistente;
8. somente depois classifique a falha como corrigível, não recuperável ou `DIAGNÓSTICO_INACESSÍVEL`.

É proibido transformar o nome da etapa que falhou em `ERRO/BLOQUEIO`. O relatório deve trazer a causa concreta encontrada, por exemplo arquivo, asset, URL, constraint, traceback, autenticação, permissão ou indisponibilidade externa.

A ausência do antigo log bruto, isoladamente, **nunca** autoriza `DIAGNÓSTICO_INACESSÍVEL`.

## Quando NÃO fazer retry automático

Pare e reporte a falha quando:

- alguma etapa de publicação já iniciou, concluiu ou pode ter concluído;
- não for possível confirmar com segurança se uma plataforma já recebeu o vídeo;
- a falha for de autenticação, segredo, permissão, indisponibilidade externa, infraestrutura ou código global da engine/publishing;
- a correção exigiria alterar `engine/`, `publishing/`, `config/`, `.github/`, catálogos globais ou episódios anteriores;
- depois de esgotar a escada de diagnóstico acima, ainda não existir causa concreta ou correção segura identificável;
- a terceira tentativa já tiver falhado.

A prioridade é evitar publicação duplicada. Uma mensagem genérica de workflow, isoladamente, nunca satisfaz os critérios acima.

## Sequência operacional

```text
criar episódio
    ↓
media preflight: resolução visual + render + capa + dry-run + validação do bundle
    ↓
.publish-queue/<slug>.txt com source_run_id
    ↓
Publish episode baixa o bundle validado — tentativa 1
    ↓
SUCESSO → encerrar
    ↓ falha segura antes da publicação
metadados/outputs/artefato → causa concreta → confirmar se o mesmo bundle pode ser reenviado
    ↓
.publish-retry/<slug>-retry-1.txt
    ↓
ACTION — tentativa 2
    ↓
SUCESSO → encerrar
    ↓ falha segura antes da publicação
metadados/outputs/artefato → causa concreta → confirmar se o mesmo bundle pode ser reenviado
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

Quando o workflow atual gerar o artefato:

```text
media-preflight-diagnostics-<run_id>
```

prefira ler:

```text
ci-diagnostics/media-preflight-<run_id>/final-result.txt
ci-diagnostics/media-preflight-<run_id>/attempt-1.log
ci-diagnostics/media-preflight-<run_id>/attempt-2.log
...
```

`final-result.txt` é a fonte rápida quando acessível. Em falhas ele pode registrar códigos, classe, detalhe, recuperabilidade, target e ação recomendada.

Se a ferramenta conectada não permitir abrir esse artefato, isso não encerra a investigação: leia o workflow/validator atual e reproduza as validações determinísticas sobre o mesmo slug. O diagnóstico persistente é preferível quando disponível, mas **não é a única forma válida de descobrir a causa**.

## Acompanhamento da Action

Quando a conexão GitHub permitir, acompanhe a execução até obter um estado conclusivo. Use metadados estruturados de run/job/steps, outputs/summaries/annotations disponíveis, artefatos persistentes acessíveis e reprodução do validator pelo código atual. Não invente resultado de render, publicação ou score visual.

Uma falha de step/job deve ser tratada primeiro como evento a investigar, não como resposta final. Enquanto a falha for anterior à publicação e houver uma ação diagnóstica ou correção segura restante, continue no mesmo slug.

Se a execução ainda estiver em andamento e não houver mecanismo apropriado para continuar acompanhando dentro da mesma execução do agente, reporte o estado real disponível. Não trate ausência de evidência como sucesso.

## Segurança de publicação

No workflow atual, resolução visual, renderização, capa e validações locais acontecem
inteiramente em `Episode media preflight`. `Publish episode` apenas verifica a
integridade do bundle aprovado e chama as APIs. Uma falha comprovadamente anterior
à primeira chamada de publicação pode ser elegível a retry. Depois que a execução
alcançar uma etapa de publicação, a decisão deve ser conservadora: não iniciar
automaticamente outra publicação sem confirmar o estado real das plataformas.

A existência de `.publish-retry/` não torna o publisher idempotente. Ela apenas garante que uma nova Action use a `main` corrigida. A proteção contra duplicidade continua sendo responsabilidade do agente ao decidir se é seguro criar o próximo retry.
