# Regra autoritativa — exatamente um episódio por execução

Esta regra define a identidade de uma **execução** do agente e o limite absoluto de episódios do Além do Hit / Music Short Factory.

Ela existe para impedir que a mesma chamada do agendamento crie um episódio, chegue à queue/publicação e depois interprete esse estado como se uma nova execução tivesse começado.

Em qualquer conflito sobre identidade da execução, troca de slug, continuidade após queue ou possibilidade de iniciar outro tema, **esta regra prevalece** sobre `templates/music-universe-topic-rule.md`, `templates/publishing-completion-rule.md` e instruções editoriais antigas. A `main` continua sendo a fonte da verdade para contratos técnicos.

## 1. Definição absoluta de execução

**Uma execução = uma única invocação/chamada do agente disparada pelo agendamento.**

A execução começa quando o agente recebe a tarefa e termina somente quando ele devolve a resposta final daquela chamada.

NÃO começa uma nova execução quando:

- um commit é criado;
- o media preflight passa;
- uma publish queue é criada;
- uma GitHub Action começa ou termina;
- uma publicação termina;
- uma plataforma publica com sucesso;
- muda a hora do relógio;
- aparece uma nova queue no repositório;
- o agente conclui uma etapa do pipeline.

Somente um **novo disparo real do agendamento** pode iniciar outra execução.

## 2. Snapshot obrigatório no início

Antes de pesquisar temas ou criar qualquer arquivo, registre como baseline da chamada atual:

- `EXECUTION_START_HEAD`: SHA atual de `main`;
- `QUEUES_AT_START`: conjunto de arquivos que já existiam em `.publish-queue/` no início da chamada;
- `EXECUTION_SLUG = UNSET`.

Esse snapshot serve para distinguir trabalho herdado de trabalho criado nesta mesma invocação.

Uma queue que já estava em `QUEUES_AT_START` pode pertencer a uma execução anterior.

Uma queue que **não** estava em `QUEUES_AT_START` e foi criada depois do início desta chamada pertence à **execução atual** e jamais pode ser usada como evidência de que uma nova execução começou.

## 3. Travamento imutável do slug

Enquanto `EXECUTION_SLUG = UNSET`, é permitido avaliar várias candidatas e executar duplicate preflights conforme as regras editoriais.

Assim que ocorrer o PRIMEIRO destes eventos, defina `EXECUTION_SLUG=<slug>`:

1. a execução decide retomar um episódio incompleto existente;
2. uma candidata recebe autorização `UNIQUE_CANDIDATE` e a autoria é iniciada;
3. qualquer arquivo é criado/modificado em `episodes/<slug>/` como parte da autoria desta chamada.

Depois disso, `EXECUTION_SLUG` é **IMUTÁVEL até a resposta final**.

É proibido:

- trocar `EXECUTION_SLUG`;
- voltar ao pool para escolher outro tema;
- criar novo duplicate-check para outra candidata;
- criar ou modificar `episodes/<outro_slug>/` como novo episódio;
- criar media preflight para outro slug;
- criar queue para outro slug;
- usar falha, PASS, queue ou publicação do primeiro slug como autorização para começar outro.

Toda correção, retry, media preflight, queue e acompanhamento de publicação deve permanecer no MESMO `EXECUTION_SLUG`.

## 4. Queue não libera um segundo episódio

A criação de `.publish-queue/<EXECUTION_SLUG>.txt` significa apenas que a publicação daquele episódio foi solicitada.

Ela **NÃO**:

- encerra a identidade da execução;
- limpa `EXECUTION_SLUG`;
- transforma o trabalho anterior em “execução anterior”;
- autoriza nova seleção editorial;
- autoriza outro slug.

Depois da queue, siga somente o fluxo de publicação/recuperação do mesmo `EXECUTION_SLUG`.

## 5. Estado terminal = responder e encerrar a chamada

Quando o `EXECUTION_SLUG` chegar a um estado terminal permitido pelas regras de publicação — sucesso, falha terminal real, bloqueio operacional ou publicação não verificável quando for o estado final suportado — faça imediatamente a resposta final desta execução.

**Depois do estado terminal do primeiro slug, não execute nenhuma nova pesquisa editorial, duplicate-check, autoria ou criação de episódio.**

Fluxo correto:

`início da chamada -> snapshot -> pool/candidatas -> primeiro UNIQUE ou retomada -> EXECUTION_SLUG travado -> autoria -> media preflight -> queue -> publicação/recovery -> estado terminal -> RESPOSTA FINAL -> fim da chamada`

Fluxo proibido:

`... -> queue/publicação do slug A -> interpretar como nova execução -> escolher slug B -> criar segundo episódio`

## 6. Detecção de violação

Se a execução já tiver `EXECUTION_SLUG` definido e detectar que está prestes a iniciar autoria de outro slug, **não faça a escrita**. Volte imediatamente ao `EXECUTION_SLUG` original ou, se ele já estiver terminal, devolva a resposta final e encerre.

Se por erro desta mesma chamada já tiver sido iniciado um segundo slug, não crie um terceiro. Reporte a violação operacional claramente e preserve a regra de não expandir o dano.

## 7. Regra de ouro

**UMA CHAMADA DO AGENDAMENTO = NO MÁXIMO UM EPISÓDIO AUTORADO.**

**PRIMEIRO SLUG AUTORIZADO/RETOMADO = ÚNICO SLUG DA CHAMADA.**

**QUEUE/PASS/PUBLICAÇÃO NÃO REINICIAM A EXECUÇÃO.**

**ESTADO TERMINAL DO PRIMEIRO SLUG = RESPOSTA FINAL IMEDIATA.**