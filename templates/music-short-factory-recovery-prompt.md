# Prompt canônico — Recovery Music Short Factory

<!-- pipeline-contract: config/pipeline-contract.json -->

Execute recuperação autônoma exclusivamente para `aleseixas/music-short-factory`
/ Além do Hit, branch `main`, canal `default`. Não execute, cheque ou altere
`content-short-factory` / Além do Óbvio nem nostalgia.

Leia na main atual e siga integralmente:
1. `docs/automation-protocol.md`;
2. `docs/pipeline-contract.md` e `config/pipeline-contract.json`;
3. `templates/execution-single-episode-rule.md`;
4. `templates/music-short-factory-automation-prompt.md` e templates editoriais;
5. `templates/publishing-completion-rule.md`, `docs/publishing-retry.md`;
6. workflows e implementação atuais de coordinator/prepare/dispatch.

Identifique o slot criador mais recente JÁ VENCIDO em America/Sao_Paulo, relacionando
checkpoints e evidências reais. Antes das 08h, use o slot das 16h do dia anterior,
não o das 08h futuras. Não invente slot_id no schema da repo.
Se entregue, observe/no-op. Se publisher iniciou/possivelmente iniciou, apenas
observe; zero substituto para esse slot. Se vazio e autoridade livre, tente
entregar exatamente um episódio novo pelos mesmos gates do creator.
Trabalho ativo anterior tem prioridade; não abra outro slug nem conte episódio
de outro slot como entrega. Vínculo incerto exige reconciliação, nunca publicação
por suposição.

No início leia/escreva Short Factory Recovery State, abas RecoveryState/Protocol:
https://docs.google.com/spreadsheets/d/1U_wncV0GVzAVBaBEF5sBQZSJwrhKX_TPjQQ8puM26JY/edit .
Use project=music-short-factory, ignore EXAMPLE_ONLY. Atualize linha lógica estável
em cada transição; preserve contadores acumulados e vínculo do slot.
Sheet é observabilidade; coordinator remoto/CAS é autoridade.

Use `pipeline_control.py status --channel default`, estado do slug exato e
`reconcile --slug <slug> --repository aleseixas/music-short-factory`.
Respeite request/owner/generation/version/revisão, lease/fence/CAS,
next_action, mutation_allowed, recovery_mutation_allowed e closed history.
Ausência inicial do arquivo de authority, sozinha, não bloqueia o fluxo:
a reserva pelo duplicate guard inicializa/migra via CAS quando permitido.
Nunca crie authority/cache/queue à mão ou force posse/permissões.
CAS/fence/cache stale exige releitura e reconcile; lease ocupada exige observação.

Para falha realmente pré-publicação: diagnóstico concreto -> correção material
de todos os erros recuperáveis -> revalidação pelo prepare -> acompanhar run
exato. Faça até 5 ciclos reais de reparo por rodada quando elegíveis. Polling
não conta como tentativa. Prepare permite até 5 reparos internos (6 validações);
Media Preflight tem até 10 passadas batch internas por run. Não transforme
5 ciclos em cinco reruns de Actions/publisher nem resete limites compartilhados.
Sem mudança, não repita erro determinístico; transitório admite backoff.
Run em andamento permanece em observação.

Reparo/autoria/prepare exige clone autenticado e runtime da repo; GitHub MCP
sozinho não prova credencial de terminal. Recovery pré-publicação pode usar o
caminho oficial `.recovery-request/*.json` -> `recovery-prepare.yml`: envie
somente o mesmo slug ativo, previous_request_id autoritativo, request_id de mídia
novo, repair_cycle 1..5 e os seis arquivos editoriais completos. O workflow deve
reconciliar e executar o mesmo prepare fenced/CAS com `GITHUB_TOKEN` nativo;
depois envia somente o `workflow_dispatch` de mídia com slug/request/SHA exatos.
Nunca use esse request para queue/publisher. Sem runtime/permissões reporte blocker exato, sem criar
.episode-check manual. PASS do prepare retorna commit_sha confirmado por CAS;
não faça outro commit/push do request. Espere slug+request+SHA+workflow,
sem usar o último run. Timeout preserva pedido para nova observação.

Até 15 candidatas na candidate_window desde a última queue, controladas por CAS;
até 5 candidatos autorados/substitutos por slot somando creator e recovery.
Sem reset manual. Candidata inviável só é substituída antes de queue/dispatch/
publisher, com zero tentativa possível, cancelamento seguro confirmado pelo
coordinator e tombstone, seguido de canal livre. Use APIs existentes de finish/
abandon_expired com precondições e token atuais; transition CANCELLED não é
atalho suportado do grafo de autoria. Sem fechamento confirmado, preserve slug.

`pedro_sampaio_ricky_martin_pikito_pikito_nfl` foi abandonado pelo usuário:
nunca reparar, renderizar para publicação, criar queue/retry ou publicar.
Se ainda ativo finalize apenas pelo mecanismo seguro; se fechado observe
tombstone. Preserve evidência anti-ressurreição.

Canal/slot livre: seleção inédita -> gate histórico -> request único duplicate ->
reserva/gate UNIQUE_CANDIDATE -> autoria editorial -> prepare local PASS ->
Media Preflight/render/dry-run/bundle -> queue CAS -> publisher original terminal.
Artistas reconhecidos no Brasil e retenção; vidIQ quando acessível; primeiro
take vídeo real, capa inicial, ~60% vídeo/~40% imagem, seleção separada por tipo,
pool válido, fit semântico, locators suportados, sem repetição. PT-BR/hashtags
lowercase. Leia templates e schemas reais; não altere engine/workflows/config.

QUEUED proíbe mutação mesmo sem publisher. Observe/reconcilie o receipt existente:
PREPARED pode retomar só pelo mecanismo CAS; SENDING/aceito/ambíguo exige observar
mesmo run, nunca segundo POST. Queue/retry/rerun/dispatch manual são proibidos.
DISPATCH_UNCERTAIN encerra o slug, não prova slot entregue nem autoriza
substituto na ambiguidade de envio.

Qualquer publisher iniciado/possivelmente iniciado:
EVER_PUBLISHED_OR_ATTEMPTED=YES; REPUBLICATION_ALLOWED=NO;
RECOVERY_MUTATION_ALLOWED=NO. Sem segunda sessão, retry/rerun, queue, reparo,
preenchimento de plataforma faltante ou substituto do mesmo slot. Observe o run
original até evidência terminal; ele pode concluir suas plataformas normalmente.
Publicação parcial/incerta = PARTIAL_NO_TOUCH permanente para o recovery.

Não conclua sucesso com tema/commit/PASS/queue. Falha de mídia/semântica/duplicate
não encerra slot vazio se houver autoridade e candidatos elegíveis. Resultado:
PUBLICADO, PARTIAL_NO_TOUCH, EM_ANDAMENTO, NÃO_VERIFICADO, SLOT_JA_ENTREGUE,
SLOT_TENTADO_NO_TOUCH, BLOQUEADO ou LIMITES_ESGOTADOS, sempre com evidência.

NUNCA desative, pause, exclua, reagende ou modifique schedule/enabled state desta
automação ou da principal. Somente pedido explícito do usuário em chat autoriza.

Após TODA rodada devolva log do protocolo comum: data/hora local, slot,
candidatos inicial/final/abandonados, coordinator/reconcile, request/generation,
ações reais, gates/render/bundle/queue/dispatch, plataformas, contadores
separados, passes internos, run/job/SHA, Sheet confirmado, flags irreversíveis,
final_status, erro concreto e next_action. Nunca invente IDs ou resultados.

