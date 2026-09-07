# Regra obrigatória — conclusão real da publicação

Esta regra define quando uma execução do **Além do Hit / Music Short Factory** pode ser considerada concluída.

Ela é autoritativa para **queue, acompanhamento da Publish Action, status final e verificação por plataforma**. Em qualquer conflito com instruções antigas equivalentes a `queue -> STOP`, `a criação da queue encerra a tarefa`, `não acompanhe a Action` ou `ACTION: SUCESSO` baseado apenas no media preflight, **esta regra prevalece**.

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

Se o workflow terminar com falha, use `STATUS: FALHA_PUBLICAÇÃO` e reporte a plataforma/etapa que falhou com base nos logs/steps reais.

Se o episódio foi criado, mas a ferramenta não permitir verificar a Publish Action, use `STATUS: PUBLICAÇÃO_NÃO_VERIFICADA`; não invente sucesso.

## 5. Status por plataforma

Reporte separadamente, usando apenas evidência real da execução:

- YouTube: `PUBLICADO_PUBLICO`, `PUBLICADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou `BLOQUEADO`;
- Instagram: `PUBLICADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou `BLOQUEADO`;
- TikTok: reporte o estado real suportado pela `main`, por exemplo `PUBLICADO`, `DRAFT_ENVIADO`, `FALHOU`, `EM_ANDAMENTO`, `NÃO_VERIFICADO` ou outro estado comprovado pelo publisher atual.

Nunca diga que uma plataforma publicou apenas porque o workflow geral foi disparado.

## 6. Falhas e retries

Se a Publish Action falhar, leia a etapa/log acessível e identifique o erro concreto.

Não republique cegamente uma plataforma que já possa ter concluído. Siga `docs/publishing-retry.md` e a implementação atual da `main` para qualquer retry, preservando segurança contra publicação duplicada.

Uma falha de publicação não autoriza escolher outro tema dentro da mesma execução.

## 7. Limite de episódio continua valendo

A regra de no máximo 1 episódio por execução continua absoluta.

Depois que um slug recebe `UNIQUE_CANDIDATE` e a autoria começa, toda a execução permanece dedicada a esse slug até chegar a um estado final de publicação verificável, falha real ou bloqueio operacional.

Acompanhar a Publish Action não autoriza criar um segundo episódio.

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
RETRIES: <número real | N/A>
ERRO/BLOQUEIO: <resumo objetivo ou NENHUM>
```

## 9. Regra de ouro

**Queue criada = publicação solicitada.**

**Publish Action terminal + plataformas verificadas = publicação concluída.**

Nunca reporte a primeira como se fosse a segunda.
