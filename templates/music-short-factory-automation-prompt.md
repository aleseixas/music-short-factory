# Prompt canônico — Music Short Factory

<!-- pipeline-contract: config/pipeline-contract.json -->

Execute uma rodada criadora do Além do Hit somente em `aleseixas/music-short-factory`,
branch `main`, canal `default`, para o slot real desta invocação. Objetivo:
entregar exatamente um episódio novo válido sempre que houver continuação segura,
com no máximo uma publicação por slot.

Leia na main atual e siga integralmente:
1. `docs/automation-protocol.md`;
2. `docs/pipeline-contract.md` e `config/pipeline-contract.json`;
3. `templates/execution-single-episode-rule.md`;
4. `templates/editorial-direction-prompt.md`, `templates/short-form-style-rule.md`
   e as regras de tema/retention/visual/capa/CTA/legenda relevantes;
5. `templates/publishing-completion-rule.md`, `docs/publishing-retry.md`;
6. os workflows e implementação de coordinator/prepare/dispatch atuais.

Comece lendo/escrevendo o Sheet Short Factory Recovery State, RecoveryState/Protocol:
https://docs.google.com/spreadsheets/d/1U_wncV0GVzAVBaBEF5sBQZSJwrhKX_TPjQQ8puM26JY/edit .
Use project=music-short-factory, ignore EXAMPLE_ONLY e atualize em cada transição.
O Sheet é checkpoint; autoridade e posse vêm do coordinator remoto/CAS.

Consulte `pipeline_control.py status --channel default`; retome slug ativo seguro
antes de considerar nova candidata. Nunca decida pela mera ausência de arquivo
de coordenação, pastas/queues ou último run. Ausência inicial de authority pode
ser inicializada pelo duplicate guard/CAS já existente; nunca escreva authority
à mão. Slot já entregue/tentado não recebe novo episódio.

Faça seleção inédita, gate editorial/histórico, request único de duplicate,
reserva/gate da Action e autoria somente após UNIQUE_CANDIDATE. Preserve identidade
e contagem CAS. Autoridade atual prevalece sobre cache; lease ocupada exige
observação, não outro slug. Use a repo em clone autenticado para autoria
coordenada/prepare. Não presuma credencial de terminal pelo GitHub conectado.
Sem runtime/acesso necessário, reporte o blocker exato sem falsificar gates.

Leia todos os templates editoriais: história com retenção e artistas reconhecidos
no Brasil; vidIQ quando acessível sem métricas inventadas; primeiro take vídeo
real, capa do começo do próprio vídeo, aproximadamente 60% vídeo/40% imagem com
seleção por tipo e coerência semântica. Pool válido e locators concretos
suportados; sem assets repetidos ou URLs de busca/HTML. PT-BR, hashtags lowercase.

Prepare somente pelo controlador: `prepare <slug> --request-id <id>`. PASS
confirma request por CAS e retorna SHA; não crie .episode-check manualmente nem
faça segundo commit/push. Acompanhe request+slug+SHA+workflow até preflight,
render/dry-run/bundle, queue selada pelo job e publisher terminal. Queue tem
slug/source_run_id/request_id; não é sucesso final. Polling/timeout preserva
pedido, não autoriza rerun/dispatch duplicado. QUEUED já bloqueia mutação.

Repare falhas pré-publicação com mudança real e revalidação batch. Preserve
limites compartilhados: 15 candidatas desde última queue, até 5 autorados por
slot; prepare até 5 reparos internos (6 validações) e Media Preflight até 10 passes internos
conforme código atual. Candidata inviável pode ser substituída somente após
cancelamento seguro/CAS/tombstone e canal livre; não force transition CANCELLED
se o grafo não oferece a transição. Falha de candidata não encerra slot elegível.

Slug abandonado pelo usuário: `pedro_sampaio_ricky_martin_pikito_pikito_nfl`.
Nunca reparar/renderizar para publicação/queue/publicar; reconcilie fechamento
seguro e preserve tombstone. Não execute content-short-factory/Além do Óbvio.

Nunca duplicar/republicar. Qualquer publisher iniciado/possivelmente iniciado
fecha slug permanentemente: EVER_PUBLISHED_OR_ATTEMPTED=YES,
REPUBLICATION_ALLOWED=NO, RECOVERY_MUTATION_ALLOWED=NO. Apenas observe o run
original; zero nova sessão/rerun/retry, plataforma faltante ou substituto do
mesmo slot. Slot futuro só após liberação autoritativa. Respeite receipt
PREPARED/SENDING/uncertain sem repetir POST por falta de run.

Nunca altere schedule/enabled state, pause, desative, apague ou reagende
automação durante a execução. Somente pedido explícito do usuário autoriza.

Depois de TODA execução, entregue o log definido no protocolo comum, incluindo
slot/slug/request/generation, resultados reais de cada gate/plataforma,
contadores separados, run/job/SHA, Sheet confirmado, flags irreversíveis,
final_status, causa concreta e next_action. Não declare PUBLICADO sem evidência.

