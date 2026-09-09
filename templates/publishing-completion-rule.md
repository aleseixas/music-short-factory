# Regra obrigatória — conclusão real da publicação

Esta regra define quando uma execução do **Além do Hit / Music Short Factory** pode ser considerada concluída.

Ela é autoritativa para **queue, acompanhamento da Publish Action, status final, recuperação de falhas e verificação por plataforma**. Em qualquer conflito com instruções antigas equivalentes a `queue -> STOP`, `a criação da queue encerra a tarefa`, `não acompanhe a Action`, `pare na primeira falha de workflow` ou `ACTION: SUCESSO` baseado apenas no media preflight, **esta regra prevalece**.

A `main` continua sendo a fonte da verdade para capacidades técnicas, workflows, publishers, schemas e estados realmente disponíveis.

## 1. Queue não é conclusão

A criação de `.publish-queue/<slug>.txt` significa apenas que o episódio foi entregue ao pipeline de publicação.

**Nunca considere a execução concluída apenas porque:**

- `MEDIA_PREFLIGHT_RESULT=PASS`;
- o workflow `Episode media preflight` terminou com sucesso;
- a publish queue foi criada;
- o workflow de publicação foi apenas disparado;
- existe uma Action em andamento.

`MEDIA PREFLIGHT: SUCESSO` e `QUEUE: CRIADA` não significam `PUBLICAÇÃO: SUCESSO`.

## 2. Fluxo obrigatório até publicação

Depois de o episódio passar pelo media preflight e a queue ser criada, não escolha outro tema e não crie outro episódio.

Acompanhe a execução exata de `.github/workflows/publish-episode.yml` correspondente ao mesmo slug até obter evidência real do estado do job `publish` e das etapas por plataforma.

Fluxo normal:

`tema -> duplicate preflight -> autoria -> media preflight PASS -> queue -> Publish episode -> YouTube -> Instagram -> TikTok -> verificar resultados -> STOP`

Se o workflow atual da `main` mudar, adapte os nomes das etapas ao código atual sem inventar capacidades.

## 3. Nunca confundir Media Preflight com Publish Action

Use estados separados:

- `MEDIA PREFLIGHT`: resultado do gate de mídia;
- `QUEUE`: criação da publish queue;
- `PUBLISH ACTION`: resultado do workflow de publicação;
- `YOUTUBE`, `INSTAGRAM` e `TIKTOK`: resultado real de cada plataforma.

É proibido emitir `ACTION: SUCESSO` se a única Action verificada foi o media preflight.

No relatório final, `ACTION` significa **PUBLISH ACTION**, nunca media preflight.

## 4. Critério para STATUS: PUBLICADO

Só use `STATUS: PUBLICADO` quando houver evidência real acessível de que o workflow de publicação chegou a estado terminal e todas as etapas de plataforma que a `main` realmente executa terminaram com sucesso.

Para o estado atual da `main`, quando `publish-episode.yml` tiver etapas de YouTube, Instagram e TikTok, verifique cada uma separadamente.

Não transforme `NÃO_VERIFICADO` em sucesso por inferência.

Se a Publish Action ainda estiver rodando e não houver estado terminal acessível, use `STATUS: PUBLICAÇÃO_EM_ANDAMENTO` e `PUBLISH ACTION: EM_ANDAMENTO`. Nunca use `STATUS: CRIADO` como sinônimo de publicação concluída.

Uma primeira falha de Media Preflight ou Publish Action **não é automaticamente um estado terminal**. Antes de reportar falha final, aplique obrigatoriamente o protocolo de recuperação da seção 6 quando a falha for elegível.

**Status genérico de workflow não é causa raiz.** `failure`, `exit code 1`, nome da etapa que ficou vermelha, `MEDIA_PREFLIGHT_RESULT=FAIL` ou frases equivalentes não podem ser usados sozinhos para encerrar a tarefa. Antes de parar, a causa concreta deve ser procurada nos logs brutos e nos artefatos persistentes conforme a seção 6.0 e `docs/publishing-retry.md`.

Se, depois do protocolo permitido, a Publish Action terminar com falha não recuperável ou com tentativas seguras esgotadas, use `STATUS: FALHA_PUBLICAÇÃO` e reporte a plataforma/etapa que falhou com base nos logs/steps reais.

Se o episódio foi criado, mas a ferramenta não permitir verificar a Publish Action, use `STATUS: PUBLICAÇÃO_NÃO_VERIFICADA`; não invente sucesso.

## 5. Status por plataforma

Reporte separadamente, usando apenas evidência real da execução:

- YouTube: `PUBLICADO_PUBLICO`, `PUBLICADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou `BLOQUEADO`;
- Instagram: `PUBLICADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou `BLOQUEADO`;
- TikTok: reporte o estado real suportado pela `main`, por exemplo `PUBLICADO`, `DRAFT_ENVIADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou outro estado comprovado pelo publisher atual.

Nunca diga que uma plataforma publicou apenas porque o workflow geral foi disparado.

## 6. Falhas e retries — recuperação automática obrigatória

**Uma GitHub Action com `failure` é um estado intermediário, não conclusão da tarefa, enquanto existir uma recuperação segura e autorizada.**

É proibido encerrar a execução após a primeira falha elegível apenas reportando o erro ao usuário.

### 6.0 Escada obrigatória de diagnóstico — não parar em erro genérico

Antes de considerar uma falha terminal, faça tudo que for aplicável abaixo na **execução exata** que falhou:

1. identifique `run_id`, `job_id`, commit/branch e a etapa exata;
2. abra o **log bruto/completo do job** e procure a mensagem imediatamente anterior ao exit code, traceback, `ERROR`, `Exception`, validator detail, target, asset, URL, arquivo ou constraint;
3. se o log bruto estiver indisponível, truncado ou genérico, consulte obrigatoriamente o artefato `media-preflight-diagnostics-<run_id>` quando existir;
4. leia `final-result.txt`; se ainda estiver genérico, leia os `attempt-*.log` do mesmo run;
5. quando existirem `MEDIA_PREFLIGHT_ERROR_COUNT`, `MEDIA_PREFLIGHT_ERRORS_JSON` ou linhas `MEDIA_PREFLIGHT_ERROR_ITEM`, trate **a lista completa** como o diagnóstico autoritativo daquela passada — não reduza o diagnóstico ao primeiro erro legado;
6. cruze todas as causas encontradas com os arquivos do **mesmo slug** e com a versão da `main` usada naquele run;
7. somente depois classifique a falha como corrigível, não recuperável ou diagnóstico inacessível.

Mensagens como `Process completed with exit code 1`, `Validate and auto-repair episode media failed`, `MEDIA_PREFLIGHT_RESULT=FAIL`, `job failed` ou somente o nome de uma etapa **nunca são suficientes** para `ERRO/BLOQUEIO` final.

Enquanto houver uma fonte diagnóstica ainda não consultada ou uma correção segura restante, **continue trabalhando no mesmo slug e não devolva o controle ao usuário**.

Se todas as fontes disponíveis forem realmente esgotadas sem causa concreta, use `DIAGNÓSTICO_INACESSÍVEL` como motivo explícito e informe quais fontes foram tentadas. Não mascare isso como erro técnico do episódio.

### 6.1 Falha no Media Preflight antes da queue — diagnóstico e correção em lote

O Media Preflight atual deve tentar **coletar todos os erros independentes observáveis na mesma passada** antes de pedir nova validação. O comportamento desejado é:

`validar tudo possível -> coletar lote de erros -> corrigir lote inteiro -> revalidar uma vez -> corrigir somente erros novos/dependentes`

Não use o padrão antigo `achar 1 erro -> corrigir -> revalidar -> achar o próximo` quando o log já fornecer múltiplos erros estruturados.

Se `Episode media preflight` falhar:

1. aplique primeiro a escada obrigatória da seção 6.0;
2. leia `MEDIA_PREFLIGHT_ERROR_COUNT` e `MEDIA_PREFLIGHT_ERRORS_JSON` quando existirem;
3. identifique **todos** os itens recuperáveis do lote, preservando o mesmo slug;
4. corrija todos os assets 403/404/inválidos, referências visuais e background recuperável que puderem ser corrigidos com segurança **antes de uma nova passada completa**;
5. não dispare um novo Media Preflight depois de cada asset individual corrigido;
6. grave as correções na `main` quando o fluxo exigir persistência externa;
7. crie um novo request `.episode-check/<slug>-<nonce>.json` apenas quando uma nova Action externa for realmente necessária pelo contrato atual;
8. acompanhe a nova Action exata;
9. se surgirem erros novos que dependiam das correções anteriores, faça outro lote somente desses erros novos;
10. se houver `MEDIA_PREFLIGHT_RESULT=PASS`, continue automaticamente para a queue/publicação sem devolver controle ao usuário entre essas etapas.

O workflow pode executar internamente mais de uma passada dentro da mesma Action para resolver dependências, mas cada passada deve corrigir o **lote inteiro conhecido**. Um retry/passado é contado por nova validação completa, **não por quantidade de assets reparados**.

Exemplo: se uma passada retornar três imagens 403, uma imagem 404 e um background repetido, a ação correta é reparar os cinco itens em lote e só então revalidar. Não são cinco retries.

Para background, prefira selecionar diretamente uma faixa/profile válido e não usado recentemente. Não rotacione cegamente um profile por nova execução quando o histórico já permite eliminar opções repetidas de uma vez.

Para evitar loop infinito no acompanhamento manual do agente, faça no máximo **3 ciclos externos totais de Media Preflight por episódio dentro da mesma execução do agente**: tentativa inicial + até 2 novas Actions após correções reais. Correções internas em lote realizadas pelo próprio workflow não contam como novas Actions do agente.

**Exceção diagnóstica:** se o Media Preflight falhar antes da queue e tanto o log bruto quanto o artefato persistente não revelarem a causa concreta, é permitida **uma única rechecagem diagnóstica adicional do mesmo slug com nonce novo**, sem alteração de tema e sem criação de queue. Essa rechecagem existe somente para produzir diagnóstico melhor e não autoriza loop infinito.

Pare antes do PASS somente se existir pelo menos um erro não recuperável que bloqueie a continuidade, se a correção não puder ser feita com segurança dentro do escopo autorizado do episódio, se depender de autenticação/secrets/permissões/infraestrutura externa, se a escada de diagnóstico tiver sido integralmente esgotada sem causa concreta ou se as tentativas seguras permitidas tiverem sido consumidas.

Uma mensagem genérica de step/job, isoladamente, **não satisfaz nenhuma dessas condições de parada**.

### 6.2 Falha na Publish Action

Se a Publish Action falhar, aplique a seção 6.0, leia a etapa/log acessível e identifique o erro concreto.

Se a falha ocorreu comprovadamente antes de qualquer plataforma poder ter recebido o vídeo, e a correção puder ser feita somente nos arquivos do episódio, a recuperação automática é **obrigatória** enquanto houver tentativa segura disponível. Siga exatamente `docs/publishing-retry.md` e a implementação atual da `main`: hoje são no máximo **3 tentativas totais de publicação** — tentativa inicial pela queue + `retry-1` + `retry-2`.

Antes de cada retry:

1. corrija primeiro o episódio;
2. grave a correção na `main`;
3. crie o próximo arquivo de retry permitido pelo contrato atual;
4. acompanhe a nova Publish Action;
5. continue até sucesso ou até não existir mais retry seguro permitido.

Não use rerun cego de execução antiga depois de alterar o episódio se a política atual exigir um novo arquivo de retry associado ao commit corrigido.

### 6.3 Segurança contra duplicação

Não republique cegamente uma plataforma que já possa ter concluído. Depois que alguma etapa de publicação começou, concluiu ou pode ter concluído, preserve a regra conservadora de `docs/publishing-retry.md`.

Se uma plataforma falhar e a `main`/workflow atual permitir continuar ou recuperar as demais plataformas de forma independente e comprovadamente segura, continue com as demais. **Nunca republique uma plataforma já confirmada como publicada.** Se o workflow atual não oferecer mecanismo seguro de continuação independente, reporte o estado real sem inventar idempotência.

Uma falha de Media Preflight ou publicação não autoriza escolher outro tema dentro da mesma execução.

## 7. Limite de episódio continua valendo

A regra de no máximo 1 episódio por execução continua absoluta.

Depois que um slug recebe `UNIQUE_CANDIDATE` e a autoria começa, toda a execução permanece dedicada a esse slug até chegar a um estado final de publicação verificável, falha real após recuperação permitida ou bloqueio operacional.

Acompanhar ou recuperar Actions não autoriza criar um segundo episódio.

## 8. Formato final obrigatório

O relatório final deve distinguir claramente criação, preflight e publicação:

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
TIKTOK: <status real>
RETRIES: <número de novas validações/publicações; não quantidade de itens corrigidos | N/A>
ERRO/BLOQUEIO: <causa concreta ou resumo do lote; nunca apenas nome genérico de step/status | NENHUM>
```

## 9. Regra de ouro

**Uma passada de preflight = descobrir o máximo de erros independentes possível.**

**Lote recuperável = corrigir todos os itens conhecidos antes da próxima validação.**

**Falha elegível = investigar log bruto, identificar causas concretas, corrigir e tentar novamente; não parar na primeira falha.**

**Erro genérico de Action = continuar diagnóstico; não encerrar.**

**Queue criada = publicação solicitada.**

**Publish Action terminal + plataformas verificadas = publicação concluída.**

Nunca reporte a primeira como se fosse a segunda.
