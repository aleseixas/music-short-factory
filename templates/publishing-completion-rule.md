# Regra obrigatória — conclusão real da publicação

Esta regra define quando uma execução do **Além do Hit / Music Short Factory** pode ser considerada concluída.

Ela é autoritativa para **queue, acompanhamento da Publish Action, status final, recuperação de falhas e verificação por plataforma**. Em qualquer conflito com instruções antigas equivalentes a `queue -> STOP`, `a criação da queue encerra a tarefa`, `não acompanhe a Action`, `pare na primeira falha de workflow` ou `ACTION: SUCESSO` baseado apenas no media preflight, **esta regra prevalece**.

A `main` continua sendo a fonte da verdade para capacidades técnicas, workflows, publishers, schemas e estados realmente disponíveis.

## 1. Queue não é conclusão

A criação de `.publish-queue/<slug>.txt` significa apenas que o episódio foi entregue ao pipeline de publicação.

Nunca considere a execução concluída apenas porque o media preflight passou, a queue foi criada ou o workflow de publicação foi disparado.

## 2. Fluxo obrigatório até publicação

Depois de o episódio passar pelo media preflight e a queue ser criada, não escolha outro tema e não crie outro episódio.

Acompanhe a execução exata de `.github/workflows/publish-episode.yml` correspondente ao mesmo slug até obter evidência real do estado do job e das etapas por plataforma.

`tema -> duplicate preflight -> autoria -> media preflight PASS -> queue -> Publish episode -> YouTube -> Instagram -> Facebook -> TikTok -> verificar resultados -> STOP`

## 3. Estados separados

Use sempre estados separados:

- `MEDIA PREFLIGHT`: resultado do gate de mídia;
- `QUEUE`: criação da publish queue;
- `PUBLISH ACTION`: resultado do workflow de publicação;
- `YOUTUBE`, `INSTAGRAM`, `FACEBOOK` e `TIKTOK`: resultado real de cada plataforma.

É proibido emitir `PUBLISH ACTION: SUCESSO` se a única Action verificada foi o media preflight.

## 4. Critério para STATUS: PUBLICADO

Só use `STATUS: PUBLICADO` quando houver evidência real acessível de que a Publish Action chegou a estado terminal e todas as etapas de plataforma que a `main` realmente executa terminaram com sucesso.

Se a Publish Action ainda estiver rodando e não houver estado terminal acessível, use `STATUS: PUBLICAÇÃO_EM_ANDAMENTO` e `PUBLISH ACTION: EM_ANDAMENTO`.

Se não houver acesso suficiente para verificar a Publish Action, use `STATUS: PUBLICAÇÃO_NÃO_VERIFICADA`.

Uma primeira falha de Media Preflight ou Publish Action **não é automaticamente terminal**. Status genérico como `failure`, `exit code 1`, nome da etapa que ficou vermelha ou `MEDIA_PREFLIGHT_RESULT=FAIL` nunca é causa raiz suficiente.

## 5. Status por plataforma

Reporte separadamente, usando apenas evidência real:

- YouTube: `PUBLICADO_PUBLICO`, `PUBLICADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou `BLOQUEADO`;
- Instagram: `PUBLICADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou `BLOQUEADO`;
- Facebook: `PUBLICADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou `BLOQUEADO`;
- TikTok: reporte o estado real suportado pela `main`, por exemplo `PUBLICADO`, `DRAFT_ENVIADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou outro estado comprovado pelo publisher atual.

## 6. Falhas e retries — recuperação automática obrigatória

Uma GitHub Action com `failure` é um estado intermediário enquanto existir diagnóstico ou recuperação segura e autorizada.

### 6.0 Escada obrigatória de diagnóstico — sem depender do antigo log bruto

O antigo log bruto/completo do job **não é mais uma fonte obrigatória**. A automação não pode bloquear, encerrar ou declarar `DIAGNÓSTICO_INACESSÍVEL` apenas porque esse log não está disponível.

Antes de considerar uma falha terminal, faça tudo que for aplicável abaixo na **execução exata** que falhou:

1. identifique `run_id`, `job_id`, workflow, commit/branch, conclusão do job e a etapa exata usando metadados estruturados da Action;
2. procure outputs, summaries, annotations, mensagens estruturadas do step e campos emitidos pelo workflow, principalmente `MEDIA_PREFLIGHT_ERROR_COUNT`, `MEDIA_PREFLIGHT_ERRORS_JSON`, `MEDIA_PREFLIGHT_ERROR_ITEM`, `MEDIA_PREFLIGHT_ERROR_CODE`, `MEDIA_PREFLIGHT_ERROR_CLASS`, `MEDIA_PREFLIGHT_ERROR_DETAIL`, `MEDIA_PREFLIGHT_TARGET`, `MEDIA_PREFLIGHT_RECOVERABLE` e equivalentes atuais;
3. consulte o artefato `media-preflight-diagnostics-<run_id>` quando estiver acessível; leia `final-result.txt` e, se necessário, os `attempt-*.log`;
4. se o artefato não puder ser lido pela ferramenta disponível, **reproduza o diagnóstico pelo código**: leia a versão exata de `.github/workflows/episode-media-preflight.yml`, os scripts/validators chamados por ele e os arquivos do mesmo `episodes/<slug>/`; aplique as mesmas validações determinísticas para localizar o arquivo, asset, URL, enum, cue, duração, background ou constraint incompatível;
5. quando a validação envolver URL/asset remoto e a ferramenta permitir, confira a disponibilidade/metadado real exigido pelo validator; não afirme inspeção não realizada;
6. cruze a causa com o mesmo slug e com o SHA/versão da `main` usada naquele run;
7. se ainda não houver causa concreta e a falha for de Media Preflight antes da queue, faça **uma única rechecagem diagnóstica adicional do mesmo slug com nonce novo**, sem alteração artificial do episódio e sem criação de queue, para gerar nova evidência;
8. somente depois classifique a falha como corrigível, não recuperável ou `DIAGNÓSTICO_INACESSÍVEL`.

Se algum log textual do job ainda estiver acessível pela ferramenta atual, use-o como evidência complementar. Ele não é pré-requisito.

Mensagens genéricas como `Process completed with exit code 1`, `Validate and auto-repair episode media failed`, `MEDIA_PREFLIGHT_RESULT=FAIL`, `job failed` ou apenas o nome de uma etapa nunca são suficientes para `ERRO/BLOQUEIO` final.

Enquanto houver uma fonte diagnóstica ainda não consultada ou uma correção segura restante, continue trabalhando no mesmo slug.

### 6.1 Falha no Media Preflight antes da queue — diagnóstico e correção em lote

O Media Preflight deve tentar coletar todos os erros independentes observáveis na mesma passada:

`validar tudo possível -> coletar lote de erros -> corrigir lote inteiro -> revalidar uma vez -> corrigir somente erros novos/dependentes`

Se `Episode media preflight` falhar:

1. aplique primeiro a escada da seção 6.0;
2. trate a lista completa de erros estruturados como diagnóstico autoritativo quando existir;
3. identifique todos os itens recuperáveis, preservando o mesmo slug;
4. corrija em lote assets inválidos, referências visuais, trims, background e demais constraints recuperáveis antes de uma nova passada completa;
5. grave as correções na `main` quando necessário;
6. crie novo `.episode-check/<slug>-<nonce>.json` somente quando nova Action externa for realmente necessária;
7. acompanhe a nova Action exata;
8. se houver `MEDIA_PREFLIGHT_RESULT=PASS`, continue automaticamente para queue/publicação.

Faça no máximo **3 ciclos externos normais totais de Media Preflight** por episódio: tentativa inicial + até 2 novas Actions após correções reais.

A rechecagem diagnóstica adicional da seção 6.0 é separada e pode ocorrer uma única vez quando não houver causa concreta acessível.

### 6.2 Falha na Publish Action

Se a Publish Action falhar, aplique a seção 6.0 e identifique a causa concreta por metadados/outputs/artefato ou reprodução do código atual.

Se a falha ocorreu comprovadamente antes de qualquer plataforma poder ter recebido o vídeo e a correção puder ser feita somente nos arquivos do episódio, a recuperação automática é obrigatória enquanto houver tentativa segura disponível.

Siga `docs/publishing-retry.md`: no máximo **3 tentativas totais de publicação** — tentativa inicial pela queue + `retry-1` + `retry-2`.

Antes de cada retry:

1. corrija primeiro o episódio;
2. grave a correção na `main`;
3. crie o próximo arquivo de retry permitido;
4. acompanhe a nova Publish Action;
5. continue até sucesso ou até não existir mais retry seguro.

### 6.3 Segurança contra duplicação

Não republique cegamente uma plataforma que já possa ter concluído. Depois que alguma etapa de publicação começou, concluiu ou pode ter concluído, preserve a política conservadora de `docs/publishing-retry.md`.

Uma falha de Media Preflight ou publicação não autoriza escolher outro tema dentro da mesma execução.

## 7. Limite de episódio continua valendo

A regra de no máximo 1 episódio por execução continua absoluta.

Depois que um slug recebe autorização e a autoria começa, toda a execução permanece dedicada a esse slug até chegar a um estado final verificável, falha real após recuperação permitida ou bloqueio operacional.

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

**Erro genérico de Action = continuar diagnóstico; não encerrar.**

**Queue criada = publicação solicitada.**

**Publish Action terminal + plataformas verificadas = publicação concluída.**
