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

- existe uma falha concreta e um log acessível que explique o erro;
- a falha ocorreu antes de qualquer etapa de publicação poder ter enviado o vídeo a uma plataforma;
- a correção pode ser feita somente nos arquivos do episódio `episodes/<slug>/`;
- a nova tentativa corresponde a uma alteração objetiva que corrige o erro anterior;
- ainda não foram consumidas as 3 tentativas totais.

Exemplos normalmente corrigíveis no episódio incluem incompatibilidades de `timeline.json`, `story.json`, `assets.json`, `post.json`, trims, referências a assets, enums, limites de cues, duração ou outras constraints explicitamente impostas pela `main`.

Nunca faça retry cego. Uma falha sem correção correspondente não justifica uma nova tentativa.

## Quando NÃO fazer retry automático

Pare e reporte a falha quando:

- alguma etapa de publicação já iniciou, concluiu ou pode ter concluído;
- não for possível confirmar com segurança se YouTube ou Instagram já receberam o vídeo;
- a falha for de autenticação, segredo, permissão, indisponibilidade externa, infraestrutura ou código global da engine/publishing;
- a correção exigiria alterar `engine/`, `publishing/`, `config/`, `.github/`, catálogos globais ou episódios anteriores;
- o erro não estiver suficientemente explicado pelos logs;
- a terceira tentativa já tiver falhado.

A prioridade é evitar publicação duplicada.

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
ler log → corrigir episódio
    ↓
.publish-retry/<slug>-retry-1.txt
    ↓
ACTION — tentativa 2
    ↓
SUCESSO → encerrar
    ↓ falha segura antes da publicação
ler log → corrigir episódio
    ↓
.publish-retry/<slug>-retry-2.txt
    ↓
ACTION — tentativa 3
    ↓
SUCESSO → encerrar
FALHA → parar e reportar para intervenção manual
```

## Regras de escrita

- Nunca recrie ou sobrescreva a queue original para tentar novamente.
- Nunca modifique um arquivo de retry já criado para disparar outra execução.
- `retry-1` e `retry-2` são os únicos retries automáticos aceitos pelo workflow atual.
- Todos os arquivos do episódio devem ser corrigidos antes de criar o respectivo arquivo de retry.
- Se uma escrita de queue/retry retornar sucesso, não repita a mesma escrita.

## Acompanhamento da Action

Quando a conexão GitHub permitir, acompanhe a execução até obter um estado conclusivo. Use jobs e logs reais; não invente resultado de render, publicação ou score visual.

Se a execução ainda estiver em andamento e não houver mecanismo apropriado para continuar acompanhando dentro da mesma execução do agente, reporte o estado real disponível. Não trate ausência de evidência como sucesso.

## Segurança de publicação

No workflow atual, renderização e preparação acontecem antes das etapas de publicação. Uma falha comprovadamente anterior à primeira etapa de publicação pode ser elegível a retry. Depois que a execução alcançar uma etapa de publicação, a decisão deve ser conservadora: não iniciar automaticamente outra publicação sem confirmar o estado real das plataformas.

A existência de `.publish-retry/` não torna o publisher idempotente. Ela apenas garante que uma nova Action use a `main` corrigida. A proteção contra duplicidade continua sendo responsabilidade do agente ao decidir se é seguro criar o próximo retry.
