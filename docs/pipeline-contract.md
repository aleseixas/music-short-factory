# Contrato operacional do pipeline

<!-- pipeline-contract: config/pipeline-contract.json -->

Contrato executável: [`config/pipeline-contract.json`](../config/pipeline-contract.json).
Resultados e limites da revisão de implementação: [revisão final](pipeline-final-review.md).
Agendamentos atuais: [protocolo comum](automation-protocol.md),
[creator](../templates/music-short-factory-automation-prompt.md) e
[recovery](../templates/music-short-factory-recovery-prompt.md).

Ausência inicial de coordination é representada como IDLE pelo coordinator;
a reserva do duplicate guard inicializa por CAS/migra candidate_window quando
necessário. Não crie authority manual nem declare blocker pela ausência isolada.
A janela compartilhada tem até 15 candidatas desde a última queue; cinco autorados
por slot e cinco ciclos por rodada de recovery são limites operacionais adicionais.
Prepare tem até cinco reparos internos (seis validações); CI Media Preflight até dez passes internos
atualmente. Esses contadores não são reruns de publisher e não admitem reset manual.

## Autoridade, posse e continuidade

A autoridade é `.pipeline/coordination/<canal>.json` no ref main do repositório
canônico. Contém owner_id, request_id, generation, version, heartbeat e expiração
pelo relógio HTTP do servidor. O CAS cria commit com pai exato e atualiza o ref
sem force: metadata e fence são confirmados na mesma transação.

O índice `.pipeline/state.json`, os registros locais e tokens são caches.
Consulte `python scripts/pipeline_control.py status` antes de decidir continuidade.
Atualizações locais de canais diferentes mesclam o índice sob lock de processo;
esse lock só protege o cache, enquanto o CAS remoto decide a posse.
Consultas normais usam canais e paths exatos, sem listar o histórico inteiro.
Lease ativa não pode ser roubada. Expiração permite retomar somente o mesmo
slug/request; outro slug exige estado terminal. Liberação invalida a geração.
O primeiro claim fica em CANDIDATE mesmo se o processo morrer antes do ledger.

```powershell
python scripts/pipeline_control.py reconcile --slug meu_slug --repository dono/repositorio
```

Reconcile consulta autoridade, recupera metadata/request/queue, preserva conflitos
e neutraliza journal stale. Arquivos removidos remotamente são retirados do cache.

## Mutação e preflight

Criação, post, resolução, reparos, render e bundle preparam mudanças em diretório
privado, renovam a lease e confirmam bytes e fence por CAS. Alterações no checkout
durante preparação abortam a operação. Workers antigos não confirmam sobre uma
geração nova. Os paths coordenados são episodes, work, output e .publish-ready;
outros destinos são rejeitados antes da escrita.

O marcador local `.pipeline/cache-applied/<slug>.json` identifica a revisão de
artefatos efetivamente aplicada. Reconcile o invalida antes de copiar arquivos
e só o confirma após concluir a cópia. Se um processo morrer após o CAS ou no
meio do download, a próxima mutação recusa o cache parcial até reconciliar.
O heartbeat cobre também hash e cópia inicial; o ledger da área privada vem da
revisão remota exata. Persistência visual direta atualiza o hash autoritativo.

```powershell
python scripts/pipeline_control.py prepare meu_slug --request-id pedido_unico
```

O validator compartilhado coleta erros independentes, repara em lote e revalida
após mudança efetiva. O PASS confirma metadata e receipt juntos, devolvendo
commit_sha e next_action=wait_for_correlated_run. Não faça outro commit/push do
mesmo request. Comandos coordenados de mutação escrevem na autoridade remota.
Nenhum .episode-check é criado com erro determinístico conhecido.

## Triggers e identidade

Duplicate continua acionado por push. Media Preflight aceita push legado e o
`workflow_dispatch` correlacionado usado por autoria e recovery. Esses workflows
usam `GITHUB_TOKEN` nativo com `contents: write` para o prepare/CAS e
`actions: write` para o handoff explícito. Push feito pelo token nativo não
encadeia outro workflow. O dispatch envia `episode`, `request_id` e `source_sha`
do commit CAS que adicionou o request. O preflight confere o request, a metadata,
o fingerprint e a reserva autoritativa nesse SHA; um CAS reivindica um único run
antes dos gates. Dispatch manual com identidade divergente falha fechado.
Recovery não envia queue, retry nem publisher manualmente. Nenhum PAT é necessário.

```powershell
python scripts/pipeline_control.py wait meu_slug --request-id pedido_unico --commit-sha SHA_DO_REQUEST --workflow media --event workflow_dispatch --repository dono/repositorio
```

Associe sempre slug, request, SHA e workflow; nunca o último run. Para push com
vários commits, informe também --before-sha SHA_ANTERIOR_DO_PUSH. O controlador
busca o commit no origin quando ele ainda não estiver disponível no clone.
Workflow atrasado com request antigo não pode assumir a reserva atual.

Media Preflight externo é hard gate: render, dry-run e bundle precisam passar.
A etapa de render reconcilia a metadata antes de aplicar o handoff visual; depois
disso valida e renderiza os mesmos assets deduplicados que irão ao bundle.
A queue contém slug, source_run_id e request_id e é selada por CAS. CLI e Actions
conferem o run exato completed/success e seu SHA antes da primeira claim.

## Dispatch e crash recovery

O receipt `.pipeline/dispatch/<slug>--<request>.json` contém owner, lease,
geração, timestamps, workflow e SHA esperados. PREPARED expirado permite takeover
por CAS antes de autorizar POST. SENDING registra a possibilidade de envio:
a retomada procura/adota o run exato e nunca repete POST por ausência presumida.

Após o prazo seguro, ambiguidade encerra o slug como DISPATCH_UNCERTAIN e libera
o canal. Runs sem publisher têm prazo máximo de 24 horas. Um worker atrasado
não consegue publicar o slug fechado. A lease é liberada após persistir SENDING
e antes do POST para não bloquear a posse imediata pelo publisher.

## Snapshot e publicação local

Só create_bundle pode marcar bundle_approved depois de validar as cópias. O
manifest é vinculado por hash à autoridade; uma gravação genérica não aprova mídia.
Cada plataforma usa cópias privadas verificadas após a cópia. Mudanças em output,
post ou bundle original não alteram o payload. A claim reconfere o fingerprint.

Antes de hosting/upload persistem attempt_id, plataforma, fingerprint, timestamp
e estágio. Publisher iniciado fecha o slug definitivamente. Recovery consulta a
plataforma quando há identificador e classifica confirmed_sent, confirmed_not_sent
ou uncertain. confirmed_not_sent exige CAS sobre a versão ainda prepared; após
sending, ausência de recurso não prova que o envio não aconteceu.

Nunca há republicação automática de uncertain. O encerramento libera o canal,
mas preserva `.pipeline/closed/<slug>.json`. Execute reconcile para aplicar a
recuperação; não há daemon em um clone desligado. API indisponível ou CAS perdido
pede nova observação, sem substituir a autoridade por lock local.

Diagnósticos preservam erro, classe, recuperabilidade, etapa, slug, request,
alvo, detalhe, lote e SHA. Consulte [erros de mídia](media-preflight-errors.md)
e [recuperação de publicação](publishing-retry.md).

