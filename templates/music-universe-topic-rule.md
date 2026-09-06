# Regra obrigatória — temas do universo da música

Esta regra define a direção editorial atual do **Além do Hit / Music Short Factory** e **prevalece sobre instruções editoriais antigas que obriguem cada episódio a ser centrado em uma música específica**.

Ela não altera schemas nem capacidades técnicas da `main`. Quando houver conflito técnico, a `main` continua sendo a fonte da verdade.

## 0. Continuidade operacional obrigatória — termine o episódio ativo antes de escolher outro

Esta checagem acontece **ANTES de pesquisar temas, montar pool, aprofundar candidata ou criar um novo `.duplicate-check`**.

A execução deve primeiro verificar se existe um **EPISÓDIO ATIVO SEM QUEUE**. Para esta regra, considere ativo um slug novo que começou a ser autorado depois da publish queue mais recente e que já possui `episodes/<slug>/story.json` (ou outros arquivos do episódio), mas ainda não possui `.publish-queue/<slug>.txt`.

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
3. se o último media preflight falhou, corrija somente o mesmo episódio e rode novo media preflight;
4. se houver `MEDIA_PREFLIGHT_RESULT=PASS`, confirme que `assets.json`, `timeline.json`, background e demais arquivos relevantes NÃO foram alterados depois desse PASS;
5. se nada relevante mudou após o PASS, leia a política de publicação atual, confirme que a queue ainda não existe e crie `.publish-queue/<slug>.txt` com conteúdo exatamente `<slug>`;
6. depois da criação bem-sucedida da queue, encerre imediatamente a execução.

Se qualquer asset, timeline ou background tiver sido alterado depois do último PASS, o media preflight deve ser executado novamente antes da queue.

Se `.publish-queue/<slug>.txt` já existir, o episódio não está mais pendente: não recrie a queue e encerre conforme o estado real.

Se forem encontrados **dois ou mais episódios ativos sem queue** no mesmo estado de continuidade, não escolha arbitrariamente entre eles. Trate como conflito operacional e reporte `BLOQUEADO` para evitar criar/publicar um terceiro episódio.

O workflow `Duplicate candidate preflight` também possui uma segunda camada de proteção. Se, apesar desta checagem inicial, um novo duplicate-check for criado enquanto existe um episódio ativo, a Action pode retornar:

- `PREFLIGHT_RESULT=RESUME_EXISTING_EPISODE` — pare de trabalhar na nova candidata e retome imediatamente o slug informado por `RESUME_SLUG`;
- `PREFLIGHT_RESULT=CONTINUITY_CONFLICT` — não autorize nenhuma nova candidata e reporte bloqueio;
- `PREFLIGHT_RESULT=EXECUTION_ALREADY_COMPLETED` — uma queue já foi criada na janela atual; encerre imediatamente.

`RESUME_EXISTING_EPISODE` não significa candidata duplicada e não autoriza um novo episódio. Ele significa: **há trabalho anterior já iniciado que deve ser concluído antes de qualquer nova seleção editorial**.

Fluxo de continuidade obrigatório:

`episódio iniciado -> concluir arquivos -> media preflight PASS -> queue -> STOP`

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

Busque mistura de:

- aproximadamente 40–60% assuntos atuais/recentes;
- aproximadamente 40–60% histórias fortes de catálogo/passado.

### Mix Brasil x internacional

Como **meta editorial de longo prazo**, tente manter aproximadamente:

- **65% de conteúdo brasileiro**;
- **35% de conteúdo internacional/gringo**.

Essa proporção deve orientar o histórico recente do canal, e não funcionar como quota rígida de cada execução ou de cada pool. Quando o histórico estiver acessível, observe aproximadamente os últimos 15–20 episódios: se um dos lados estiver claramente abaixo da meta, aumente a prioridade de boas candidatas desse grupo até o mix voltar a se aproximar de 65/35.

Nos dois grupos, dê forte preferência a **cantores, artistas e bandas famosos, populares ou amplamente reconhecíveis pelo público**. Não use artista obscuro apenas para cumprir a proporção. Ao mesmo tempo, fama sozinha não basta: o assunto escolhido sobre esse artista ainda precisa ser não óbvio, forte e passar por todos os gates editoriais.

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
- o tema é realmente interessante e não apenas famoso;
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
- `MEDIA_PREFLIGHT_RESULT=PASS` existe antes da publish queue.

## 12. Precedência

Em conflito EDITORIAL com regras antigas, aplique esta ordem:

1. `main` para capacidades e contratos técnicos;
2. esta regra para **continuidade operacional antes da seleção**, escopo de tema, escolha editorial, hook e duplicidade por história;
3. demais templates para edição, pacing, visual, áudio, legenda, CTA e publicação.

Qualquer instrução antiga equivalente a `todo episódio deve ser sobre uma música`, `escolha obrigatoriamente uma música` ou `a primeira frase deve identificar música + artista` deve ser considerada substituída por esta regra.
