# Revisão final do pipeline — 29/09/2026

`SAFE_TO_COMMIT: YES` — os seis riscos distribuídos estão mitigados e cobertos por
testes; as falhas da suíte completa são idênticas às reproduzidas no HEAD.

Escopo: Content e Music. Nenhum commit, push, workflow remoto ou publicação real
foi executado. Os testes distribuídos usam servidor CAS HTTP de teste e processos
independentes. Arquivos preexistentes do usuário foram preservados.

## Riscos corrigidos

| Risco | Implementação e evidência |
| --- | --- |
| Dois clones assumem o canal | Autoridade compartilhada por canal no ref canônico, owner, relógio do servidor, lease/heartbeat, claim CAS e takeover apenas de lease expirada do mesmo slug/request. Disputa e takeover testados em processos independentes. |
| Escrita antiga após publisher | Mutações preparadas em diretório privado; metadata e fence confirmados no mesmo CAS. Worker pausado antes da confirmação perde a geração e não escreve na autoridade. Publicação fecha o slug permanentemente. |
| Hash conferido difere do upload | Bundle valida as cópias antes de gerar manifest. Apenas esse gate marca bundle_approved. Snapshot privado confere os bytes copiados; cada plataforma usa esse snapshot e a claim reconfere seu fingerprint. |
| Índice stale autoriza ação | Índice e working tree são caches. Claim/queue/dispatch/publicação conferem autoridade e CAS. Reconcile baixa receipt/queue exatos, remove metadata ausente com backup e impede replay de journal stale. Workflow atrasado não assume request novo. |
| Crash antes/depois do dispatch | PREPARED e SENDING persistidos por CAS com owner/lease/prazos. Só PREPARED expirado prova ausência de autorização para POST. SENDING é observado/adotado por run exato; ambiguidade terminal fecha slug e libera canal sem novo POST. |
| Crash no publisher local | attempt_id/plataforma/fingerprint/timestamp/estágio persistidos antes do envio; recovery lê marcador e versão na mesma revisão, consulta plataforma quando possível e aplica CAS. confirmed_not_sent exige versão ainda prepared; uncertain nunca permite resend. |

Testes incluem lease ativa não roubada, morte abrupta de processo, worker pausado
após verificar o fence e antes do CAS, retomada da mesma sessão sem repetir
plataforma, recibo histórico sem alterar a reserva atual, arquivo modificado após
validação e reconciliação sem duplicar publicação.

## Regressões de integração corrigidas

- Claim inicial entra em CANDIDATE atomicamente; crash antes do ledger local não
  pula duplicatas. Metadata removida remotamente não pode ressurgir via cache stale.
- Reconcile recupera receipt/queue exatos, guarda conflitos e neutraliza journal stale.
  Status atualiza evidência sem substituir rascunhos locais ainda não confirmados.
- Atualizações concorrentes dos dois canais mesclam o índice local sob o mesmo
  lock de processo. O marcador de cache aplicado é invalidado antes do download
  e só confirmado após todos os arquivos e o ledger serem escritos; crash no
  meio ou após o CAS impede reenvio de metadata antiga. O ledger da mutação é
  preparado a partir da revisão remota exata.
- Heartbeat começa antes da leitura de hashes e da cópia do bundle, segue até
  terminar a preparação e renova a lease antes do CAS final.
- Persistência direta de visual_usage atualiza o hash no manifesto autoritativo,
  preserva uma versão remota mais nova e escreve no cache os bytes canônicos
  confirmados. O arquivo local client_secret.json de Content foi ignorado.
- Inputs de diagnóstico são copiados para a área privada e os paths de resultado
  são remapeados ao checkout. Destinos não coordenados são rejeitados antes da escrita.
- Dispatch libera a lease depois do CAS SENDING e antes do POST, permitindo posse
  imediata pelo publisher. Resolver falhando agora impede handoff/render posterior.
- Render Preflight reconcilia antes de aplicar o handoff visual. A reconciliação
  posterior restaurava assets.json/timeline.json remotos e podia desfazer a
  deduplicação binária do Music antes de validar e renderizar.
- CLI verifica SHA/request/run/workflow exatos. Run de origem ainda em andamento
  é observado por até cinco minutos; somente completed/success autoriza a claim.
- Workflows usam snapshot privado; dry-run não exige restaurar output mutável.
- Fixtures de restauração e redação de credenciais adaptadas ao novo caminho.
  Os validators e os testes preexistentes não foram afrouxados.

## Classificação de todas as falhas restantes da suíte


Referências de baseline: Content `503af258c5c37f7879494585ca664c8dba8b14e0`;
Music `57859414b62ba694f378f9322785682367c1453a`.
Os módulos de produção de SFX remoto, cache, música, TikTok e smart pacing
envolvidos nestas falhas estão idênticos ao HEAD anterior às mudanças.
As mesmas falhas foram reproduzidas em baseline isolada; logs em
`output/pipeline-validation/pipeline-baseline*-results.txt`.

As nove falhas comuns são **preexistentes, com expectativas ou fixtures obsoletas**.
Elas não se tornaram incompatíveis por causa da nova arquitetura.

| Arquivo/classe | Teste | Evidência da classificação |
| --- | --- | --- |
| `test_manual_sfx.py / ManualSfxTests` | `test_manual_sfx_uses_https_and_25mb_limit` | Mock antigo omite `allowed_hosts`; HTTPS e limite de 25 MB continuam exigidos. |
| `test_publishing.py / CredentialAndPublisherTests` | `test_tiktok_official_flow_queries_creator_uploads_and_fetches_status` | Fixture espera `direct` sem defini-lo; o padrão anterior já era `draft`. |
| `test_remote_media_cache.py / RemoteMediaCacheTests` | `test_url_must_point_directly_to_the_expected_file_type` | Exige rejeição por ausência de extensão; HEAD já aceita essas URLs e valida o conteúdo. |
| `test_remote_media_cache.py / RemoteAudioCatalogTests` | `test_invalid_remote_music_is_rejected_after_download` | Áudio inválido é rejeitado; somente a mensagem esperada diverge da agregação atual. |
| `test_smart_visual_pacing.py / SmartVisualPacingTests` | `test_long_run_of_static_images_is_reduced_conservatively` | Sequência de imagens de 5 s não admite ajuste válido sob todos os limites. |
| Mesma classe | `test_slow_static_sequence_gives_time_to_moving_video` | Imagens de 7/6,5 s não atingem máximo de 4 s com deslocamento limitado a 1 s. |
| Mesma classe | `test_video_with_real_motion_can_sustain_more_time_than_static_video` | Ambos os cenários saturam o limite da imagem; não existe a diferença exigida. |
| Mesma classe | `test_visual_fx_stays_on_the_same_scenes_after_boundary_shift` | Cue em 5,5–6,5 s impede reduzir a imagem de 6 s preservando o efeito. |
| `test_smart_visual_pacing.py / SmartVisualPacingPipelineTests` | `test_enabled_episode_passes_adjusted_plan_to_render_preflight` | Duas imagens limitadas a 4 s não cobrem a narração de 9 s. |

Os cinco testes de pacing conflitam com limites já documentados no HEAD
(imagens de 2–4 s, deslocamento máximo de 1 s/35% e preservação da timeline
quando não há ajuste seguro). Não foi enfraquecido o código para satisfazê-los.

Três falhas exclusivas de Music:

| Arquivo/classe | Teste | Classificação |
| --- | --- | --- |
| `test_audio_search.py / AudioSearchTests` | `test_scheduled_agent_contract_uses_direct_http_and_local_fallback` | **Preexistente, asserção textual obsoleta:** exige a expressão antiga “tarefa agendada do ChatGPT”. |
| `test_duplicates.py / DuplicateEpisodeTests` | `test_episode_mode_can_exclude_itself_and_find_older_duplicate` | **Preexistente, teste defeituoso:** verifica `is_file()` após fechar/remover `TemporaryDirectory`; a detecção da duplicidade passa. |
| `test_editorial.py / EditorialDirectionTests` | `test_agent_guidance_matches_high_energy_sfx_budget` | **Preexistente, contrato obsoleto:** exige quota aproximada de 15 SFX/busca externa, já substituídas por biblioteca curada e ausência de quota. |

Não há teste obsoleto restante cuja incompatibilidade tenha sido causada pela
arquitetura nova. Nenhum dos testes acima foi removido, desabilitado ou convertido
em xfail. Os riscos distribuídos encontrados na revisão anterior foram tratados nesta implementação.


## Limites operacionais e cenários conservadores

A autoridade usa CAS do ref Git canônico; locks locais só protegem caches/threads.
Falha de autenticação, permissão, API ou CAS impede a ação até reconciliação.
Working tree é cache: a confirmação da mutação é remota; o payload vem de snapshot.

Prepare coordenado confirma o request remotamente e devolve seu SHA. Duplicate e
Media continuam push; Publish conserva dispatch explícito para GITHUB_TOKEN.
Prepare dentro de Actions é rejeitado antes de criar um request sem trigger real.

Um slug uncertain permanece fechado mesmo que talvez nunca tenha sido enviado.
A recuperação terminal libera o canal; ausência/listagem truncada não autoriza
novo POST. Recovery exige executar reconcile; não existe daemon em clone desligado.
Lease expirada permite recuperar o mesmo episódio, não abandonar arbitrariamente
uma pauta. Diretórios coordenados suportados: episodes, work, output e .publish-ready.
O contador editorial de 15 candidatos ainda é um cache local: um clone que não
observou o reset feito após a queue em outro clone pode responder
`CANDIDATE_LIMIT_REACHED` indevidamente. Isso não concede posse nem publicação;
exige reconstruir esse contador local a partir da última queue autoritativa.

Não foi testada publicação nem escrita GitHub real, conforme solicitado. Adapters
atuais não oferecem chave de idempotência utilizável; envio incerto nunca recebe
retry automático. Essas limitações não são alegações de entrega exactly-once.

## Arquivos adicionais desta rodada

Em ambos: shared_coordination, coordination_runtime, mutation_transaction,
snapshot e recovery; integrações em pipeline/state, attempts, publish, criação,
render, post, resolução/reparos, controlador, workflow, bundle e visual_usage.
Workflows, contrato, documentação, gitignore e fixtures foram atualizados.
README e requirements-dev.txt declaram pytest como runner da suíte completa;
os comandos unittest específicos dos workflows também foram verificados.
Novas suítes: test_shared_coordination, test_mutation_fencing e
test_publication_snapshot_recovery; dispatch/crash/contrato receberam novos casos.
Testes adicionais com processos independentes cobrem a corrida do índice entre
canais e os dois pontos de crash do cache. Há casos para heartbeat durante hash
e cópia, para persistência visual stale/canônica e para a ordem do handoff
visual; Music também testa a referência ao binário deduplicado.

## Resultados finais

Focados finais: 214 passaram em Content; 215 passaram em Music.
Suíte completa: Content 730 passaram, 9 falharam e 1 skip; Music 750 passaram,
12 falharam e 1 skip. A comparação exata de IDs com o baseline encontrou zero
falhas novas e zero divergências. Actionlint e git diff --check: exit 0 em ambos.
Logs desta rodada:

- output/pipeline-validation/distributed-final-focused-handoff.txt
- output/pipeline-validation/distributed-final-suite-verified.txt
- output/pipeline-validation/distributed-actionlint-final.txt
- output/pipeline-validation/distributed-diff-check-final.txt

Python 3.13.14, pytest 8.4.2, ambiente isolado. Suíte completa:
`python -m pytest tests -q --tb=short`. Cópias de bibliotecas sob output não
pertencem à suíte do projeto. Actionlint 1.7.12; ShellCheck/Pyflakes indisponíveis
localmente e desabilitados. Nenhum teste foi desabilitado ou convertido em xfail.

## Revalidacao apos integrar origin/main (2026-09-29)

Base remota integrada: `d2343c919fbdcd2e8525ab575200b4f59c60ddfd`.
Testes focados: 265 passaram, 1 falhou (fixture TikTok preexistente).
Suite completa: 750 passaram, 1 ignorado, 16 falharam.
As 12 falhas da baseline original continuam; 3 falhas adicionais em
`tests/test_video_assets.py` e 1 em `tests/test_youtube_cache_preflight.py`
foram reproduzidas sem o commit do pipeline, num worktree isolado da base
remota (4 falhas em 4 testes selecionados). Os arquivos de teste e codigo
desses casos nao foram modificados por este commit. Nao houve falha nova
causada pela integracao do pipeline.
`actionlint -shellcheck= -pyflakes=` e `git diff --check` passaram.
Logs: `output/pipeline-validation/post-rebase-focused.txt` e
`output/pipeline-validation/post-rebase-full.txt`.
