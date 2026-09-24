# Regra obrigatória — temas do universo da música

Esta regra define a direção editorial atual do **Além do Hit / Music Short Factory** e **prevalece sobre instruções editoriais antigas que obriguem cada episódio a ser centrado em uma música específica**.

Ela não altera schemas nem capacidades técnicas da `main`. Quando houver conflito técnico, a `main` continua sendo a fonte da verdade.

## 0. Continuidade operacional obrigatória — termine o episódio ativo antes de escolher outro

Esta checagem acontece **ANTES de pesquisar temas, montar pool, aprofundar candidata ou criar um novo `.duplicate-check`**.

A execução deve primeiro verificar se existe um **EPISÓDIO ATIVO SEM QUEUE**. Para esta regra, considere ativo um slug novo que começou a ser autorado depois da publish queue mais recente e que já possui `episodes/<slug>/story.json` (ou outros arquivos do episódio), mas ainda não possui `.publish-queue/<slug>.txt`.

**Uma publish queue criada por uma execução anterior NÃO bloqueia uma nova execução.** Não use hora do relógio, bloco 08h/11h/19h, dia ou outra janela temporal como identidade da execução. A queue mais recente serve apenas como fronteira de continuidade para descobrir se algum episódio começou depois dela e ficou incompleto.

Se existir exatamente um episódio ativo sem queue:

- NÃO pesquise um novo tema;
- NÃO monte novo pool de candidatas;
- NÃO crie outro slug;
- NÃO crie outro duplicate-check;
- NÃO reavalie se o tema antigo ainda seria o vencedor;
- retome exclusivamente o mesmo slug e leve-o até o próximo estado válido do pipeline.

Use esta ordem de retomada:

1. se os arquivos do episódio estiverem incompletos, complete/corrija somente o mesmo slug;
2. se ainda não houver media preflight, crie `.episode-check/<slug>-<nonce>.json` e acompanhe a Action exata;
3. se o último media preflight falhou, corrija somente o mesmo episódio e rode novo media preflight conforme `templates/publishing-completion-rule.md` e `docs/publishing-retry.md`;
4. se houver `MEDIA_PREFLIGHT_RESULT=PASS`, confirme que `assets.json`, `timeline.json`, background e demais arquivos relevantes NÃO foram alterados depois desse PASS;
5. se nada relevante mudou após o PASS, leia a política de publicação atual, confirme que a queue ainda não existe e crie `.publish-queue/<slug>.txt` com conteúdo exatamente `<slug>`;
6. depois da criação bem-sucedida da queue, **NÃO encerre a execução por causa da queue**: siga `templates/publishing-completion-rule.md`, acompanhe a Publish Action correspondente ao mesmo slug e só conclua em um estado terminal real de publicação ou bloqueio permitido.

Se qualquer asset, timeline ou background tiver sido alterado depois do último PASS, o media preflight deve ser executado novamente antes da queue.

Se `.publish-queue/<slug>.txt` já existir para o MESMO slug que estava sendo retomado, não recrie a queue. A partir daí, trate o slug conforme `templates/publishing-completion-rule.md`: localize e acompanhe a Publish Action correspondente em vez de interpretar a existência da queue como conclusão.

Se forem encontrados **dois ou mais episódios ativos sem queue** no mesmo estado de continuidade, não escolha arbitrariamente entre eles. Trate como conflito operacional e reporte `BLOQUEADO` para evitar criar/publicar um terceiro episódio.

O workflow `Duplicate candidate preflight` também possui uma segunda camada de proteção. Se, apesar desta checagem inicial, um novo duplicate-check for criado enquanto existe um episódio ativo, a Action pode retornar:

- `PREFLIGHT_RESULT=RESUME_EXISTING_EPISODE` — pare de trabalhar na nova candidata e retome imediatamente o slug informado por `RESUME_SLUG`;
- `PREFLIGHT_RESULT=CONTINUITY_CONFLICT` — não autorize nenhuma nova candidata e reporte bloqueio.

`RESUME_EXISTING_EPISODE` não significa candidata duplicada e não autoriza um novo episódio. Ele significa: **há trabalho anterior já iniciado que deve ser concluído antes de qualquer nova seleção editorial**.

Uma queue anterior à execução atual nunca deve ser reinterpretada como `EXECUTION_ALREADY_COMPLETED`. **Queue não é estado terminal**: conclusão de publicação é regida por `templates/publishing-completion-rule.md` e exige acompanhar o estado real da Publish Action/plataformas quando acessível.

Fluxo de continuidade obrigatório:

`episódio iniciado -> concluir arquivos -> media preflight PASS -> queue -> Publish episode -> verificar plataformas -> estado terminal`

Somente quando NÃO existir episódio ativo sem queue a execução pode seguir para a seleção normal de tema descrita abaixo.

## 1. Escopo editorial

O episódio não precisa mais partir de uma música específica.

O canal cobre o **universo da música** e pode produzir histórias centradas em:

- artista ou banda;
- curiosidade marcante de carreira;
- fato chocante e verificável do passado;
- acontecimento recente relevante;
- bastidor pouco conhecido;
- conflito, rivalidade ou disputa real;
- decisão inesperada;
- rejeição ou fracasso que gerou consequência importante;
- acidente ou erro que mudou uma obra/carreira;
- processo criativo;
- colaboração improvável;
- censura;
- contrato ou episódio relevante da indústria musical;
- show, turnê ou performance com história forte;
- recorde ou acontecimento cultural relevante;
- comportamento, documento ou declaração incomum e verificável;
- origem surpreendente de álbum, visual, performance, persona ou projeto;
- música específica, quando a história daquela faixa for realmente o melhor assunto.

A unidade editorial passa a ser **TEMA/HISTÓRIA**, não obrigatoriamente `música`.

Uma música pode aparecer como contexto, exemplo, consequência ou payoff sem ser o assunto principal.

## 2. Critério principal: não óbvio

Não escolha um assunto só porque o artista é famoso.

O tema precisa fazer o público médio pensar algo próximo de: **“como eu nunca soube disso?”**.

Rebaixe fortemente:

- biografia básica;
- fatos triviais;
- curiosidades repetidas em listas genéricas;
- informação que praticamente todo fã casual já conhece;
- números sem história;
- prêmio famoso sem detalhe novo;
- nome verdadeiro ou origem artística quando isso já for amplamente conhecido;
- resumo de carreira sem conflito, surpresa ou consequência.

Prefira temas com pelo menos um destes elementos:

- contradição inesperada;
- consequência importante;
- detalhe de bastidor pouco conhecido;
- decisão que quase mudou a carreira;
- fracasso ou rejeição antes de uma virada;
- erro/acidente que virou parte da obra;
- conexão improvável entre artistas;
- disputa real;
- fato recente com impacto;
- origem surpreendente;
- reviravolta que muda a forma de enxergar o artista ou a obra.

## 3. Hook obrigatório

A primeira frase deve entregar **imediatamente** a informação mais forte do episódio.

Quando houver artista/banda central, seu nome deve aparecer naturalmente já na primeira frase.

NÃO abra com:

- `[MÚSICA], de [ARTISTA]...` por obrigação;
- `Hoje vamos falar de...`;
- `Você sabia que...` genérico;
- apresentação biográfica;
- contexto longo antes da curiosidade.

A abertura deve priorizar um fato:

- chocante;
- curioso;
- surpreendente;
- estranho;
- controverso, quando comprovado;
- emocionalmente forte;
- ou atual e relevante.

**Sensacionalista na forma, factual no conteúdo.**

É proibido inventar escândalo, exagerar causalidade, transformar rumor ou teoria em fato, omitir qualificação essencial ou prometer algo que o episódio não sustenta.

Exemplos abaixo mostram apenas a FORMA e não devem ser reutilizados como fatos sem pesquisa:

- `[ARTISTA] tomou uma decisão em estúdio que parecia um erro — e ela acabou mudando a carreira.`
- `Antes de estourar, [ARTISTA] quase perdeu uma oportunidade decisiva por um motivo absurdo.`
- `Essa história de [ARTISTA] parece inventada, mas aconteceu de verdade.`
- `Um detalhe quase esquecido de [ARTISTA] voltou a chamar atenção agora por causa de...`

## 4. Fatos atuais

Acontecimentos recentes podem ter prioridade quando forem realmente fortes.

Exemplos de categorias possíveis:

- lançamento;
- retorno;
- viralização/revival;
- recorde;
- declaração relevante;
- controvérsia confirmada;
- processo ou disputa documentada;
- mudança de carreira;
- show/turnê;
- colaboração;
- prêmio;
- acontecimento relevante da indústria.

Nunca use `agora`, `recentemente`, `voltou a explodir`, `acabou de` ou equivalente sem fonte recente compatível com a data da execução.

## 5. Pesquisa e factualidade

Nunca invente fatos.

Priorize:

- entrevistas do próprio artista e envolvidos;
- materiais oficiais;
- créditos oficiais;
- documentários/biografias confiáveis;
- Billboard;
- Rolling Stone;
- Variety;
- NME;
- Pitchfork quando aplicável;
- jornais e veículos reconhecidos;
- arquivos e documentos públicos;
- entrevistas de produtores, compositores, músicos e colaboradores.

Wikipedia, Reddit, fanpages e posts de fãs podem servir como pista, mas não devem ser a única base de um fato forte.

Para um fato histórico extraordinário, controverso ou fácil de distorcer, procure confirmação independente adicional quando possível.

Para fato recente, use evidência recente.

## 6. Seleção do tema

Antes da autoria, considere um pool real de aproximadamente **10–15 temas**, não necessariamente 10–15 músicas.

Esse pool de 10–15 é o **pool inicial para ranking**, não um limite máximo de tentativas. A execução não deve morrer apenas porque as primeiras candidatas eram repetidas.

Busque mistura de:

- aproximadamente 40–60% assuntos atuais/recentes;
- aproximadamente 40–60% histórias fortes de catálogo/passado.

### Loop obrigatório de candidatas até produção

Depois de ranquear o pool, processe as candidatas em ordem de qualidade:

1. faça a checagem editorial de história e o duplicate preflight técnico da melhor candidata ainda não testada;
2. se retornar `PREFLIGHT_RESULT=DUPLICATE_CANDIDATE`, descarte **SOMENTE aquela candidata**;
3. avance imediatamente para a próxima candidata melhor ranqueada, sem encerrar a execução e sem retornar `BLOQUEADO`;
4. se uma candidata falhar em score, fontes, factualidade, potencial visual ou outro gate editorial, descarte apenas ela e avance;
5. se o pool inicial for consumido principalmente por duplicatas ou reprovações, **pesquise e acrescente novas candidatas** em vez de encerrar automaticamente;
6. continue esse ciclo até encontrar uma candidata inédita que passe pelos gates e possa seguir para autoria;
7. depois que uma candidata receber `UNIQUE_CANDIDATE`, pare de avaliar outras, autorize somente esse slug e conduza-o até `MEDIA_PREFLIGHT_RESULT=PASS` e criação da queue;
8. depois da criação bem-sucedida da nova queue desta execução, **continue com o mesmo slug pela regra de conclusão/publicação; não use `STOP` na queue**.

`DUPLICATE_CANDIDATE` é um resultado normal de filtragem, não um erro de execução. Uma queue de execução anterior também não é um erro nem motivo de bloqueio.

`BLOQUEADO` deve ficar reservado a impedimentos operacionais reais, como `CONTINUITY_CONFLICT`, falha de infraestrutura sem resultado confiável ou outra condição técnica que torne inseguro continuar.

`SEM_CANDIDATO` só deve ser usado depois de pesquisa realmente ampla e expansão razoável além do pool inicial quando necessário. **Não use `SEM_CANDIDATO` ou `BLOQUEADO` só porque a primeira, segunda ou várias candidatas eram duplicadas.**

Objetivo operacional normal:

`pool -> candidata 1 duplicada? próxima -> candidata 2 falhou gate? próxima -> ampliar pool se necessário -> UNIQUE -> autoria -> media preflight PASS -> queue -> Publish episode -> verificar plataformas -> estado terminal`

### Mix Brasil x internacional

Como **meta editorial de longo prazo**, tente manter aproximadamente:

- **65% de conteúdo brasileiro**;
- **35% de conteúdo internacional/gringo**.

Essa proporção deve orientar o histórico recente do canal, e não funcionar como quota rígida de cada execução ou de cada pool. Quando o histórico estiver acessível, observe aproximadamente os últimos 15–20 episódios: se um dos lados estiver claramente abaixo da meta, aumente a prioridade de boas candidatas desse grupo até o mix voltar a se aproximar de 65/35.

Nos dois grupos, dê forte preferência a **cantores, artistas e bandas famosos, populares ou amplamente reconhecíveis pelo público**. Não use artista obscuro apenas para cumprir a proporção. Ao mesmo tempo, fama sozinha não basta: o assunto escolhido sobre esse artista ainda precisa ser não óbvio, forte e passar por todos os gates editoriais.

### PRIORIDADE DE AUDIÊNCIA — RENOME + HYPE ATUAL

O potencial de audiência do artista é um dos principais critérios de seleção do tema. **Priorize fortemente artistas com nome muito grande, fandom relevante, reconhecimento imediato e capacidade real de gerar clique, retenção, comentário e compartilhamento.** Entre duas histórias de qualidade editorial semelhante, normalmente deve vencer a que envolve o artista com maior reconhecimento público ou maior momento cultural.

Considere dois caminhos igualmente válidos:

1. **grandes nomes consolidados**, nacionais ou internacionais, com enorme reconhecimento público e fandom;
2. **artistas em forte hype agora**, mesmo que ainda não tenham o mesmo peso histórico, quando estiverem dominando conversa, charts, lançamentos, TikTok, Reels, Shorts, YouTube, Spotify ou cobertura musical recente.

No Brasil, pesquise ativamente quem está em alta na data REAL da execução. Nomes como **Matuê, Teto, Veigh, WIU, Filipe Ret, Orochi, MC Cabelinho, KayBlack, Ana Castela, Anitta** e outros podem ser ótimos candidatos **quando os sinais atuais confirmarem relevância**. Esses nomes são somente exemplos de referência: **NÃO são whitelist, NÃO são lista fixa e NÃO devem continuar recebendo bônus se o hype tiver passado**.

Da mesma forma, no internacional, dê prioridade a artistas de enorme reconhecimento ou que estejam dominando o momento cultural. Não fique preso a uma lista histórica de megastars: pesquise quem está realmente movimentando audiência na data da execução.

Antes de fechar o pool/ranking, use a web para procurar sinais atuais de demanda quando isso for relevante: charts, lançamentos recentes, viralização, volume de cobertura, tendências em plataformas sociais, turnês, colaborações, controvérsias confirmadas e outros indicadores públicos de interesse. **Não invente métricas e não trate um único sinal isolado como prova definitiva de hype.**

Na comparação de candidatas com qualidade semelhante, aplique esta ordem de desempate editorial:

1. maior potencial de audiência/reconhecimento imediato;
2. maior hype ou momento cultural atual comprovável;
3. fandom mais ativo/engajado;
4. assunto com maior chance de reconhecimento instantâneo no feed;
5. melhor combinação entre nome forte e história não óbvia.

Evite gastar episódios com artistas excessivamente nichados ou pouco reconhecidos quando houver uma história de força semelhante envolvendo um nome muito maior. **Artista obscuro só deve vencer um artista de grande renome quando a história for claramente superior, muito mais surpreendente ou significativamente melhor documentada.**

A meta 65/35 é uma preferência, não uma trava. Se a melhor história disponível for claramente superior e pertencer ao lado momentaneamente mais representado, ela ainda pode vencer; evite sacrificar qualidade apenas para fechar uma conta exata.

Dentro desse mix, continue buscando variedade de artistas, gêneros e épocas.

Avalie cada tema em 0–10:

- `S` Storyability;
- `H` Hook Potential;
- `N` Novelty / não-obviedade;
- `C` Consequence / impacto;
- `R` Recognition;
- `M` Cultural Moment / atualidade;
- `F` Source Reliability;
- `V` Visual Potential.

Ao atribuir `R` e `M`, seja exigente: `R` deve refletir o quanto o artista é reconhecível para o público amplo e `M` deve refletir sinais atuais verificáveis de hype/relevância. Não dê `R` ou `M` altos por preferência pessoal.

Fórmula editorial:

`FINAL SCORE = 2.0*S + 2.2*H + 2.0*N + 1.5*C + 1.0*R + 1.0*M + 1.6*F + 0.7*V`

Só autorize produção quando:

- `S >= 7`;
- `H >= 8`;
- `N >= 7`;
- `F >= 8`;
- `FINAL SCORE >= 90`.

Tema óbvio perde prioridade mesmo com artista muito famoso.

## 7. Estrutura narrativa

O episódio deve progredir, não apenas listar curiosidades.

Estrutura preferencial:

`hook forte -> contexto mínimo -> detalhe inesperado -> consequência -> nova virada -> payoff`

A curiosidade principal deve aparecer no primeiro beat; não esconda o único fato interessante até metade do vídeo.

Depois do hook, abra novas camadas para manter retenção.

Mantenha a linguagem conversada definida nas demais regras editoriais da `main`.

## 8. Duplicidade agora é também por história

A duplicidade não é mais apenas `mesma música`.

Antes de autorar, verifique também se o repositório já contou a **mesma história/fato central**.

Compare:

- artista/banda;
- acontecimento;
- curiosidade/fato-chave;
- música/álbum relacionado quando houver;
- pessoas envolvidas;
- slug/título provável.

É permitido publicar vários episódios sobre o mesmo artista se os **temas centrais forem realmente diferentes**.

Não publique a mesma curiosidade novamente apenas mudando hook, título ou música usada como contexto.

O duplicate preflight técnico existente continua obrigatório. Se ele ainda exigir `song`, `artist` e `slug`, use a melhor representação compatível com o contrato atual sem inventar novos campos; a checagem editorial por TEMA complementa o gate técnico.

Se o duplicate preflight acusar repetição, isso encerra somente a candidatura atual. Volte ao ranking e continue o loop descrito na seção 6.

## 9. Visuais

Nos primeiros 0,0–1,5s, mostre preferencialmente o artista/banda central claramente reconhecível ou um visual diretamente ligado ao fato principal.

Quando o episódio for realmente sobre uma música específica, a capa oficial continua sendo opção forte.

Não abra com B-roll genérico quando existe um sujeito visual reconhecível.

O pool visual deve contar a história específica do episódio. Busque pessoas, eventos, épocas, locais, documentos, performances, entrevistas, capas, objetos e outros elementos mencionados na narração.

Todas as regras atuais de relevância semântica, WEB-FIRST, proporção vídeo/imagem, deduplicação visual, pacing e media preflight continuam válidas.

## 10. Música específica continua permitida

Esta regra NÃO proíbe episódios sobre músicas.

Ela remove a obrigação de todo episódio ser sobre uma faixa.

Se uma música tiver a melhor história do pool, produza sobre ela normalmente. Se a melhor história for sobre o artista, carreira, show, bastidor, indústria ou acontecimento recente, produza sobre esse tema sem forçar uma faixa como protagonista.

## 11. Checklist antes da autoria/queue

Confirme:

- a checagem de continuidade foi feita antes de qualquer novo tema/duplicate-check;
- não existe episódio ativo sem queue; se existir, ele está sendo retomado em vez de criar outro;
- uma queue de execução anterior NÃO foi usada para bloquear indevidamente a execução atual;
- candidatas duplicadas foram descartadas individualmente e a seleção continuou;
- se o pool inicial foi consumido por duplicatas/reprovações, novas candidatas foram pesquisadas antes de considerar `SEM_CANDIDATO`;
- o tema é realmente interessante e não apenas famoso;
- o artista/tema tem forte potencial de audiência OU a história é claramente superior o bastante para justificar um nome menor;
- o hype atual foi verificado com sinais recentes quando ele foi usado como argumento de prioridade;
- nomes nacionais e internacionais foram avaliados sem tratar listas de exemplos como fixas;
- o fato central não é óbvio para o público médio;
- o hook entrega a curiosidade/tensão no primeiro beat;
- artista/banda aparece na primeira frase quando houver sujeito central;
- o episódio não foi artificialmente transformado em história de uma música;
- a história é verificável;
- fatos atuais têm fontes recentes;
- temas extraordinários têm sustentação suficiente;
- a mesma história não foi publicada antes;
- o duplicate preflight técnico passou para uma nova candidata OU a execução está retomando um slug previamente autorizado;
- as regras atuais de roteiro, visual, áudio, CTA e mídia continuam respeitadas;
- `MEDIA_PREFLIGHT_RESULT=PASS` existe antes da publish queue;
- a criação da queue será tratada apenas como solicitação de publicação, nunca como conclusão da execução;
- após a queue, `templates/publishing-completion-rule.md` será seguido até um estado real verificável.

## 12. Precedência

Em conflito, aplique esta ordem:

1. `main` para capacidades e contratos técnicos;
2. `templates/publishing-completion-rule.md` e `docs/publishing-retry.md` para **conclusão da execução, queue, acompanhamento da Publish Action, diagnóstico de falhas, retries e status de publicação**;
3. esta regra para **continuidade operacional antes da seleção**, escopo de tema, escolha editorial, hook e duplicidade por história;
4. demais templates para edição, pacing, visual, áudio, legenda e CTA.

Qualquer instrução antiga equivalente a `todo episódio deve ser sobre uma música`, `escolha obrigatoriamente uma música` ou `a primeira frase deve identificar música + artista` deve ser considerada substituída por esta regra.

Qualquer instrução antiga equivalente a `queue -> STOP`, `queue encerra a tarefa`, `não acompanhe a Publish Action` ou `pare na primeira falha de workflow` deve ser considerada substituída por `templates/publishing-completion-rule.md`.

## CAMADA DE OPORTUNIDADE ATUAL PARA SHORTS — DEMANDA + CONCORRÊNCIA

Antes de autorizar uma candidata do fluxo NORMAL do Além do Hit, faça uma pesquisa atual de oportunidade. Esta camada melhora a PRIORIZAÇÃO do pool; ela não substitui factualidade, duplicate preflight, qualidade narrativa, relevância no Brasil nem os demais hard gates da main.

### Objetivo
Encontrar assuntos musicais que combinem:
- interesse/demanda crescendo AGORA;
- concorrência/saturação ainda administrável;
- sinais de que vídeos recentes ainda conseguem performar;
- artista/assunto relevante para público brasileiro;
- história específica que funcione em aproximadamente 45–90s e sustente retenção.

### Fontes e sinais
Use, quando acessíveis nesta execução:
1. vidIQ público: Rising Keywords, páginas de tendências, crescimento recente, outliers, views/hour e outros sinais públicos;
2. dados do vidIQ autenticado SOMENTE se estiverem realmente acessíveis — nunca invente Search Volume, Competition ou Overall Score;
3. YouTube/Shorts: quantidade e idade de vídeos recentes sobre o MESMO assunto/ângulo, tamanho dos canais concorrentes, velocidade de views e presença de canais pequenos/médios obtendo desempenho acima do normal;
4. Google Trends, YouTube Charts, Billboard/Spotify e notícias recentes quando ajudarem a confirmar que a atenção é real no Brasil;
5. web/news/social discovery como sinais auxiliares, sempre distinguindo tendência comprovada de simples impressão.

Se Search Volume ou Competition exatos do vidIQ não estiverem disponíveis, NÃO bloqueie a seleção e NÃO fabrique números. Estime demanda e saturação de forma qualitativa com evidência pública.

### Como medir concorrência para Shorts
Não trate apenas o campo de keyword competition como concorrência real do Short. Avalie principalmente SATURAÇÃO RECENTE:
- quantos Shorts/vídeos recentes atacam o mesmo tema e principalmente o mesmo ângulo;
- quantos canais grandes já cobriram aquilo;
- quão repetitiva está a narrativa;
- se novos vídeos ainda estão acelerando;
- se canais pequenos/médios ainda conseguem virar outliers;
- se existe um ângulo factual forte ainda pouco explorado.

Tema muito popular mas já coberto de forma massiva e repetitiva deve perder prioridade. Tema em aceleração, com poucos vídeos equivalentes e outliers recentes, deve ganhar prioridade.

### Priorização interna sugerida
Use como guia flexível, não como matemática falsa:
- 35% tendência/crescimento recente;
- 25% baixa saturação/concorrência recente;
- 20% desempenho de vídeos recentes/outliers;
- 15% força da história para retenção;
- 5% volume de busca/search intent.

Quando houver números confiáveis, use-os. Quando não houver, classifique os sinais como ALTO/MÉDIO/BAIXO com base nas evidências encontradas.

### Regra de decisão
Entre candidatas editorialmente válidas, prefira a que apresentar a melhor combinação de:
`MOMENTUM ALTO + SATURAÇÃO BAIXA/MÉDIA + OUTLIERS RECENTES + RELEVÂNCIA NO BRASIL + HISTÓRIA FORTE`.

Não escolha um tema apenas porque está em alta. Não escolha um tema apenas porque tem baixa concorrência. Demanda sem história forte não passa; história forte completamente fria perde para uma história igualmente boa com oportunidade atual melhor.

### Nostalgia
Para o slot NOSTALGIA, esta camada é SOMENTE um bônus secundário. Nunca deixe hype, Rising Keyword, charts ou evento recente atropelarem `templates/nostalgia-famous-artists-rule.md`. Nostalgia mainstream + renome consolidado no Brasil + história não óbvia continuam prevalecendo. Tendência atual pode desempatar duas candidatas nostálgicas igualmente fortes, mas não é hard gate.

