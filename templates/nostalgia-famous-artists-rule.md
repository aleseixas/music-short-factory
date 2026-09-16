# Regra editorial — slot de nostalgia com artistas famosos

Esta regra existe para o **agendamento diário de nostalgia das 12h** do Além do Hit / Music Short Factory.

Ela especializa o agendamento principal para um fluxo editorial próprio de nostalgia. A `main` continua sendo a fonte da verdade técnica para schemas, renderer, media preflight, publishing, áudio e demais capacidades.

## 0. REGRA CRÍTICA — o slot das 12h é isolado do fluxo normal

O slot de nostalgia NÃO pode ser sequestrado por episódio criado pelos agendamentos normais de 8h/19h.

Todo novo episódio criado por este fluxo deve usar obrigatoriamente um slug com prefixo:

```text
nostalgia_<slug>
```

Exemplo:

```text
nostalgia_michael_jackson_beat_it_van_halen
```

Para esta execução, somente slugs `nostalgia_*` pertencem ao namespace de continuidade da nostalgia.

Portanto:

- episódio normal sem prefixo `nostalgia_` NÃO pode virar `EXECUTION_SLUG` desta chamada;
- queue normal existente NÃO bloqueia o slot das 12h;
- retry normal existente NÃO bloqueia o slot das 12h;
- Publish Action normal falhando, rodando ou aguardando NÃO bloqueia o slot das 12h;
- episódio normal incompleto NÃO deve ser retomado pelo agendamento de nostalgia;
- o agendamento das 12h só retoma um episódio anterior se ele próprio tiver slug `nostalgia_*` e estiver realmente inacabado antes de queue;
- uma queue `nostalgia_*` que já existia no início desta nova chamada pertence a execução anterior e, assim como no scheduled principal, NÃO autoriza nem impede a identidade da nova execução.

Esta seção é um **override deliberado de escopo de continuidade** para o agendamento das 12h. Ela prevalece, somente neste slot, sobre qualquer instrução genérica em `templates/music-universe-topic-rule.md` ou outro template que mande retomar qualquer episódio ativo independentemente de origem.

A regra de `templates/execution-single-episode-rule.md` continua absoluta **dentro desta chamada**: depois que um slug `nostalgia_*` for retomado ou autorizado, `EXECUTION_SLUG` fica imutável até a resposta final e nenhum segundo episódio pode ser criado.

### Duplicate preflight exclusivo da nostalgia

Para novas candidatas deste slot, NÃO use `.duplicate-check/*.json` nem o workflow normal de duplicate preflight.

Use exclusivamente:

```text
.duplicate-check-nostalgia/<nonce>.json
```

com o mesmo contrato de `song`, `artist` e `slug`, sendo obrigatório que `slug` comece com `nostalgia_`.

O workflow correspondente é:

```text
.github/workflows/duplicate-preflight-nostalgia.yml
```

Esse workflow replica o duplicate check do fluxo principal, mas sua continuidade e seu limite de candidatas consideram somente o namespace `nostalgia_*`. Episódios normais são intencionalmente ignorados.

Resultados relevantes:

- `UNIQUE_CANDIDATE` → autorize essa candidata e fixe `EXECUTION_SLUG`;
- `DUPLICATE_CANDIDATE` → descarte apenas essa candidata e teste a próxima;
- `RESUME_EXISTING_NOSTALGIA_EPISODE` → retome somente o slug `nostalgia_*` indicado;
- `NOSTALGIA_CONTINUITY_CONFLICT` → reporte `BLOQUEADO` sem criar terceiro episódio;
- `CANDIDATE_LIMIT_REACHED` → `STATUS: SEM_CANDIDATO`.

## 1. Objetivo do slot

O episódio das 12h deve explorar **nostalgia musical mainstream**, usando artistas, bandas, músicas, álbuns, performances ou histórias que despertem reconhecimento e memória afetiva imediatos no público amplo.

O objetivo NÃO é simplesmente procurar “música antiga”. O objetivo é combinar:

- artista de enorme reconhecimento;
- memória afetiva forte;
- história específica e não óbvia;
- hook imediato;
- fontes confiáveis;
- bom material visual real;
- potencial de comentário e compartilhamento.

## 2. HARD GATE — somente artistas famosos

Para este agendamento, o artista/banda central deve ser **muito famoso, popular ou amplamente reconhecível pelo público brasileiro e/ou mundial**.

Artista obscuro, excessivamente nichado, regional sem reconhecimento amplo, cult conhecido apenas por público especializado ou nome que exija explicação antes de gerar interesse **NÃO passa neste slot**, mesmo que a história seja boa.

A fama é um hard gate deste agendamento, mas fama sozinha não basta: a história ainda precisa ser forte, verificável e não óbvia.

Exemplos do nível de reconhecimento desejado, apenas como referência e nunca como whitelist fixa:

- internacional: Michael Jackson, Elton John, Queen, The Beatles, Madonna, Elvis Presley, Whitney Houston, Celine Dion, ABBA, Bee Gees, Bon Jovi, Guns N' Roses, Britney Spears, Backstreet Boys, Mariah Carey, Prince, George Michael, U2;
- Brasil: Roberto Carlos, Tim Maia, Chico Buarque, Gilberto Gil, Caetano Veloso, Rita Lee, Cazuza, Legião Urbana, Mamonas Assassinas, Charlie Brown Jr., Sandy & Junior, Chitãozinho & Xororó, Zezé Di Camargo & Luciano, Ivete Sangalo e outros nomes de reconhecimento comparável.

Não trate esses exemplos como lista fechada. Pesquise outros artistas do mesmo nível de reconhecimento.

## 3. O que conta como nostalgia

Priorize histórias ligadas principalmente às décadas de 1960, 1970, 1980, 1990 e 2000, sem transformar datas em uma trava matemática.

Também é válido usar artista ainda ativo hoje quando a história escolhida estiver claramente ligada a uma fase, música, álbum, performance, TV, turnê, clipe, momento cultural ou memória coletiva do passado.

Nostalgia pode vir de:

- música que marcou uma geração;
- história inesperada por trás de um hit clássico;
- gravação ou decisão de estúdio;
- clipe icônico;
- show ou performance histórica;
- música quase descartada;
- rejeição antes de um grande sucesso;
- censura, conflito ou controvérsia documentada;
- colaboração improvável;
- erro/acidente que virou parte da obra;
- origem inesperada de um clássico;
- bastidor pouco conhecido;
- significado que muita gente nunca percebeu;
- momento de TV, rádio, festival ou cultura pop que marcou época;
- mudança de formação ou decisão de carreira com consequência clara;
- história que provoque “eu lembro disso” + “eu não sabia dessa parte”.

Não faça biografia genérica, resumo de carreira, lista de hits ou vídeo que seja nostálgico apenas porque o artista é antigo.

## 4. Não priorizar hype atual neste slot

Neste agendamento, **nostalgia + fama + força da história prevalecem sobre hype atual, charts atuais e lançamentos recentes**.

Não é necessário que o artista esteja em alta hoje.

Um acontecimento atual pode servir de gancho secundário quando reacender uma memória antiga, mas não deve transformar o slot das 12h em outro feed de notícias musicais.

Em conflito de seleção de tema para este agendamento, esta regra substitui as preferências de `music-universe-topic-rule.md` que favorecem hype atual, assuntos recentes ou mix obrigatório de atualidade.

`templates/live-event-priority-rule.md` NÃO deve forçar um tema atual neste slot. Só use evento/show atual se a história continuar essencialmente nostálgica e centrada em artista famoso.

## 5. Pool e seleção

Monte um pool real de aproximadamente 10–15 candidatas de nostalgia com artistas que já tenham passado pelo hard gate de fama.

Busque variedade entre Brasil e internacional, décadas diferentes, gêneros populares diferentes e histórias sobre música, carreira, show, clipe, estúdio, indústria e cultura pop.

Não coloque artista obscuro no pool apenas para completar quantidade.

Avalie cada candidata em 0–10:

- `S` Storyability;
- `H` Hook Potential;
- `N` Novelty / não-obviedade;
- `R` Recognition / fama ampla;
- `A` Nostalgia / memória afetiva;
- `C` Consequence / impacto da história;
- `F` Source Reliability;
- `V` Visual Potential.

Use como orientação:

```text
NOSTALGIA SCORE = 2.0*S + 2.2*H + 1.8*N + 1.8*R + 1.7*A + 1.2*C + 1.6*F + 0.7*V
```

Hard gates mínimos:

- `R >= 8`;
- `A >= 7`;
- `S >= 7`;
- `H >= 8`;
- `N >= 7`;
- `F >= 8`.

Se o nome não tiver reconhecimento amplo, descarte antes de aprofundar a pesquisa.

Entre duas histórias de força semelhante, prefira maior reconhecimento imediato do artista, maior memória afetiva, história mais surpreendente, melhor material visual real da época e maior potencial natural de comentários.

## 6. Repetição e variedade

É permitido voltar ao mesmo artista em episódios futuros se a história central for diferente.

Porém:

- evite repetir o mesmo artista nos episódios muito recentes quando houver alternativas fortes;
- como referência, tente não usar o mesmo artista novamente dentro dos últimos ~14 dias;
- nunca repita a mesma história/fato central;
- nunca repita a mesma música como assunto central se ela já tiver episódio equivalente;
- o duplicate check continua pesquisando o repositório inteiro, inclusive episódios normais, para evitar conteúdo duplicado entre os dois fluxos.

A janela de ~14 dias é preferência editorial, não justificativa para escolher artista menos famoso ou história fraca.

## 7. Hook e narrativa

A nostalgia deve aparecer como força emocional, mas o hook precisa vender a HISTÓRIA, não apenas a lembrança.

Evite aberturas genéricas como `Quem lembra de...`, `Hoje vamos relembrar...` ou nostalgia vazia sem fato forte.

Prefira abrir com artista + fato surpreendente, conflito, decisão, consequência ou contradição.

A narrativa deve seguir aproximadamente:

```text
fato forte -> contexto mínimo da época -> detalhe inesperado -> consequência -> nova camada -> payoff nostálgico
```

O espectador deve sair com duas sensações: **“eu conheço muito esse artista/música”** e **“eu não sabia dessa história”**.

## 8. Direção visual específica

A abertura continua obedecendo às regras vigentes e deve usar vídeo real `exact/direct` quando exigido.

Para nostalgia, dê preferência a material visual real da época: performances, entrevistas antigas, clipes/registros contextuais, fotos históricas, capas, jornais, revistas, documentos, programas de TV, palcos, estúdios e locais diretamente ligados à história.

Não use apenas fotos atuais do artista para contar história antiga quando existir material contextual melhor.

Não force dezenas de fontes/takes. Qualidade, entendimento, retenção e valor informativo ficam acima da quantidade de assets.

## 9. Brasil x internacional

O slot alterna livremente entre Brasil e internacional. A meta global do canal pode ser consultada como contexto, mas este agendamento NÃO deve escolher artista menos famoso só para fechar quota geográfica.

## 10. Precedência deste agendamento

Durante a execução das 12h aplique:

1. `main` para contratos e capacidades técnicas;
2. `templates/execution-single-episode-rule.md` para uma única chamada/um único slug depois de autorizado;
3. **esta regra** para isolamento do namespace, continuidade exclusiva `nostalgia_*`, duplicate preflight de nostalgia, fama e seleção editorial;
4. `templates/publishing-completion-rule.md` + `docs/publishing-retry.md` para media preflight/publicação/retries do `EXECUTION_SLUG` desta chamada;
5. `templates/music-universe-topic-rule.md` para factualidade, duplicidade por história e estrutura geral, exceto onde esta regra substitui continuidade global, hype, atualidade ou mix;
6. demais templates/docs para edição, visuais, áudio, legendas, CTA e qualidade.

Regra de ouro deste slot:

```text
12H = NOSTALGIA MAINSTREAM + ARTISTA MUITO FAMOSO + HISTÓRIA NÃO ÓBVIA + FONTE FORTE
NORMAL != NOSTALGIA
SLUG NORMAL NUNCA BLOQUEIA OU É RETOMADO PELO SLOT DAS 12H
NOVO EPISÓDIO DAS 12H SEMPRE USA nostalgia_
UMA CHAMADA = NO MÁXIMO UM EPISÓDIO
```

Se nenhuma candidata famosa passar pelos gates, retorne `SEM_CANDIDATO`. **Nunca preencha o slot com artista obscuro só para produzir alguma coisa.**
