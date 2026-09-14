# Override obrigatório — unicidade visual por conteúdo e segmento

Leia esta regra antes de fechar `assets.json`, `visual_candidates.json` e `timeline.json`.

Gate obrigatório de autoria, antes de `.episode-check`:

```text
python -m engine.visual_candidates episodes/<slug>/visual_candidates.json
```

Cada candidato precisa preservar URL HTTPS/localizador real, `kind`, origem/provider
e metadata de aquisição. Para YouTube, preserve o `VIDEO_ID` e a página pública;
nunca use apenas um título de busca ou `id: 1`. Um ID de vídeo válido com provider
YouTube pode reconstruir a URL. `watch?v=`, `shorts/`, `embed/` e `youtu.be/` do
mesmo ID são o mesmo visual; IDs diferentes, inclusive por maiúsculas/minúsculas,
são origens diferentes. Não descarte o parâmetro `v` ao comparar URLs.

Esta é a regra vigente e **SOBREPÕE qualquer texto anterior que imponha quantidade editorial fixa de vídeos, imagens, takes, mudanças visuais ou candidatos por slot, OU que permita vídeo `contextual`, `generic` ou sem `semantic_fit` como fallback**.

## Imagens — zero reuso

A mesma imagem principal NUNCA pode ser usada em dois shots do episódio.

Crop, focus, zoom, motion, transition, visual FX, overlay ou qualquer outro tratamento NÃO transforma a mesma imagem em um visual novo.

## Vídeos — mesma fonte pode alimentar takes diferentes

O mesmo vídeo-fonte PODE ser usado em mais de um shot quando cada uso corresponde a um **trecho temporal realmente diferente e não sobreposto**.

Regras obrigatórias:

- respeite somente os limites técnicos reais vigentes da `main` para quantidade de usos por fonte, sobreposição e trims;
- para cada uso, escolha `source_start_seconds` e, quando fizer sentido, `source_end_seconds` com intenção semântica própria;
- NÃO reutilize o mesmo intervalo nem intervalos que se sobreponham;
- mudar crop, focus, speed, motion, transition, visual FX, overlay ou outro tratamento sobre o MESMO TRECHO não cria um novo take;
- distribua reaproveitamentos de forma editorialmente natural e evite repetição cansativa;
- quando um candidato repetido não trouxer trim explícito, o resolver pode atribuir um baseline temporal distinto automaticamente; ainda assim, um trim editorial explícito e semanticamente escolhido é preferível;
- o Best Segment continua podendo otimizar cada baseline dentro da sua vizinhança conservadora e não deve criar sobreposição com outro shot da mesma fonte;
- se uma fonte continuar sendo a melhor escolha editorial e a `main` permitir tecnicamente seu uso, ela pode ser reutilizada em trechos distintos; se começar a ficar repetitiva, escolha outra fonte ou imagem relevante.

URLs, aliases ou nomes de arquivo diferentes que resolvam para o mesmo vídeo/provider continuam sendo a **mesma fonte** para controle técnico e de sobreposição.

## Hard gate — vídeo nunca pode ser genérico

Para VÍDEO, somente `semantic_fit=exact` ou `semantic_fit=direct` é elegível.

- `exact`: mostra diretamente o acontecimento, performance, pessoa, local, objeto ou momento narrado;
- `direct`: mostra diretamente o artista/banda/personagem/evento relevante ao beat, mesmo que não seja o instante exato;
- `contextual`: **proibido para vídeo**;
- `generic`: **proibido para vídeo**;
- candidato de vídeo sem `semantic_fit`: **proibido**.

Não use crowd genérico, palco genérico, festival genérico, cidade genérica, estúdio genérico, mãos no celular, luzes, stock footage, clipe de outro artista ou qualquer B-roll apenas para manter movimento. Ser “do mesmo gênero”, “da mesma vibe” ou até “do mesmo artista” não basta quando o take fala de uma pessoa, evento, música ou ação específica que o vídeo não representa diretamente.

Se nenhum vídeo `direct/exact` funcionar tecnicamente em um slot comum, prefira **uma imagem realmente relevante**. Não rebaixe para vídeo genérico.

### Abertura

O primeiro visual editorial continua obrigatoriamente sendo VÍDEO. Portanto, o primeiro slot deve ter candidatos `direct/exact` reais e redundantes. Se um download falhar, tente outros vídeos `direct/exact`; não substitua a abertura por imagem nem por vídeo genérico.

## Autonomia editorial total — qualidade, clareza e aprendizado acima de contagem

A partir daqui, a escolha visual é guiada por **bom senso editorial e pela qualidade final do vídeo**, não por metas numéricas.

O agente tem autonomia para decidir livremente:

- quantos takes o episódio precisa;
- quantos vídeos diferentes usar;
- quantas imagens usar;
- quantas vezes trocar de fonte;
- quando manter a mesma fonte por mais tempo;
- quando reutilizar outra parte da mesma fonte, desde que tecnicamente válido;
- quanto material buscar para cada slot;
- quando parar de buscar porque a seleção já está boa o suficiente.

Não existe meta editorial fixa de vídeos, fotos, takes, mudanças visuais ou candidatos. Também não existe proporção obrigatória vídeo/imagem.

A prioridade é sempre produzir **o melhor short possível** para TikTok/Reels/Shorts, com três objetivos principais:

1. **qualidade percebida** — os visuais devem parecer bons, nítidos, relevantes e coerentes entre si;
2. **clareza** — quem assiste deve conseguir acompanhar a história sem edição confusa, frenética ou poluída;
3. **aprendizado** — o visual deve ajudar a pessoa a entender e aprender algo sobre a história narrada, e não apenas manter movimento na tela.

VIDEO FIRST continua significando que a abertura editorial é vídeo real e que vídeo relevante deve ser considerado com prioridade. Mas isso NÃO significa maximizar o número de vídeos nem trocar de asset a cada frase.

Use vídeo quando ele realmente ensina, mostra, contextualiza ou melhora a experiência. Use foto, documento, capa, manchete, frame histórico ou imagem contextual quando isso explicar melhor o que está sendo narrado. Se uma única fonte de vídeo tiver vários momentos excelentes, pode ser melhor explorar bons trechos dela do que usar várias fontes medianas só para aumentar variedade.

O agente deve assistir mentalmente à montagem como um editor humano: se a sequência parecer cansativa, poluída, repetitiva, confusa, apressada ou visualmente pobre, simplifique ou troque os visuais. Se estiver clara, interessante, fluida e didática, mantenha mesmo que use menos fontes.

A regra central é:

> **não otimize para quantidade de assets; otimize para qualidade, entendimento, retenção e valor informativo.**

## Prioridade editorial

A ordem editorial continua sendo:

1. vídeo `exact`;
2. vídeo `direct`;
3. imagem `exact` ou `direct`;
4. imagem `contextual`;
5. imagem `generic` apenas como último recurso real.

Essa ordem é um guia de qualidade, não uma obrigação de preencher o episódio com o maior número possível de vídeos.

## Pool de candidatos — buscar o suficiente, não maximizar quantidade

O pool existe para dar boas opções ao resolver, não para cumprir uma meta numérica.

Pesquise candidatos até existir **confiança editorial razoável** de que o slot tem boas alternativas reais. Quando houver material abundante, traga variedade suficiente para evitar dependência excessiva de uma única fonte ruim. Quando poucas opções realmente boas existirem, aceite um pool menor em vez de completar quantidade com material irrelevante.

Regras obrigatórias:

- cada slot deve pesquisar a partir do seu `visual_intent`, e não apenas pelo nome do artista ou da música;
- descarte vídeos repetidos, genéricos ou pouco ligados à fala;
- um mesmo YouTube `provider_id` continua sendo uma única fonte real;
- não faça busca adicional apenas para aumentar contagem se as melhores opções já estiverem claras;
- continue buscando quando os candidatos disponíveis ainda forem fracos, genéricos, redundantes ou pouco informativos;
- todo candidato de vídeo deve declarar `semantic_fit` e ele deve ser `exact` ou `direct`;
- `contextual` e `generic` continuam permitidos somente para IMAGENS;
- para pessoas, colaborações, bastidores, eventos ou locais citados na narração, faça buscas específicas com esses nomes/contextos;
- preserve diversidade quando ela melhora a história, não como fim em si mesma;
- não trate trims diferentes do mesmo vídeo como fontes diferentes;
- no primeiro slot, garanta redundância suficiente de vídeos `direct/exact` para permitir auto-recovery se uma aquisição falhar.

O objetivo é entregar ao resolver **as melhores opções disponíveis**, e não a maior quantidade possível.

## Gate antes da queue

Antes de finalizar o episódio, confirme:

- nenhuma imagem principal foi reutilizada;
- intervalos reutilizados da mesma fonte de vídeo são distintos e não se sobrepõem;
- o mesmo trecho não foi mascarado como novo take por crop/FX/speed;
- a seleção respeita os limites técnicos reais vigentes da `main`;
- todo vídeo usado é `exact` ou `direct`;
- o primeiro shot editorial é vídeo `exact/direct`;
- nenhuma imagem foi escolhida apenas para cumprir proporção;
- nenhum vídeo foi escolhido apenas para aumentar contagem;
- a quantidade de mudanças visuais parece natural para a história;
- a montagem ajuda o público a entender e aprender o conteúdo;
- a edição final prioriza qualidade, clareza e retenção, sem ficar poluída, monótona ou artificialmente frenética.

Esta regra substitui políticas editoriais anteriores baseadas em quotas, contagens ou proporções. A política correta agora é: **hard gates técnicos e semânticos onde realmente necessários; autonomia editorial total no restante; abertura em vídeo; e decisões visuais guiadas pela melhor qualidade final, clareza, retenção e aprendizado do público**.
