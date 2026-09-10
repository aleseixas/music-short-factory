# Regra de layout — legenda falada e texto editorial

Esta regra existe para manter a legenda falada sempre legível em Shorts/Reels/TikTok sem conflito com `text_fx`, highlights ou overlays.

## Prioridade visual

A legenda falada tem PRIORIDADE sobre qualquer texto editorial.

Quando houver risco de conflito visual, faça nesta ordem:

1. preserve a legenda falada;
2. reduza, adie ou remova o `text_fx` editorial;
3. use highlight ou overlay somente se ocupar outra zona e acrescentar informação real;
4. nunca duplique a mesma informação simultaneamente em legenda, `text_fx`, highlight e overlay.

## Zonas preferenciais

- metade superior: artista, rosto, objeto ou ação principal do shot;
- centro: `text_fx` editorial curto, quando realmente necessário;
- meio-baixo: legenda falada;
- rodapé e bordas: evite depender deles por causa da interface das plataformas.

A composição deve deixar respiro entre o texto editorial central e a legenda falada. Se o `text_fx` ficar grande o suficiente para invadir a faixa da legenda, encurte o texto ou não use essa cue naquele beat.

## Legenda falada

O renderer define posição, tamanho e agrupamento globais. Editorialmente, considere que a legenda deve ser a camada textual mais fácil de ler durante a fala.

Prefira blocos curtos e rápidos. Não crie outro texto editorial grande por cima de uma legenda importante apenas para aumentar movimento.

## Texto editorial

Use `text_fx` para uma ideia forte, número, contraste, revelação ou frase curta que realmente melhore retenção/compreensão. Não use como decoração constante.

Se uma cena já tiver legenda falada forte e o visual principal estiver claro, deixar o centro limpo é uma decisão editorial válida e preferível a poluir a tela.

## Legenda de publicação e hashtags

Nos campos públicos de `post.json`, mantenha o texto da legenda/descrição separado das hashtags.

- `youtube.description`, `instagram.caption` e `tiktok.caption` devem conter APENAS o texto editorial da publicação, sem hashtags embutidas no fim do texto.
- As hashtags devem existir SOMENTE no array `hashtags` da respectiva plataforma, porque o publisher já adiciona esse array ao texto final da publicação.
- Para TODO novo episódio, `youtube.hashtags`, `instagram.hashtags` e `tiktok.hashtags` são obrigatórios: os três arrays devem existir e conter hashtags editoriais relevantes para aquele episódio. Nunca omita o array de uma plataforma e nunca o deixe vazio esperando que o código complete depois.
- Defaults técnicos como `shorts` ou `reels` são apenas fallback de segurança e NÃO substituem a autoria das hashtags. O episódio deve chegar ao commit/queue com hashtags reais já preenchidas para YouTube, Instagram e TikTok.
- Gere as hashtags conscientemente por plataforma, priorizando combinações úteis de artista/banda, música, tema, evento, gênero ou contexto e `curiosidade`, sem adicionar tags genéricas só para aumentar quantidade.
- Siga as recomendações atuais da `main` para quantidade quando aplicáveis; no estado atual, a referência é aproximadamente 5 hashtags no YouTube, 8 no Instagram e 5 no TikTok. Isso é orientação editorial, não motivo para adicionar tags irrelevantes.
- Nunca copie o mesmo bloco de hashtags para dentro da caption/description e também para `hashtags`; isso gera repetição no post publicado.
- Cada hashtag deve aparecer no máximo uma vez por plataforma, considerando comparação sem diferença entre maiúsculas e minúsculas.
- TODAS as hashtags devem ser salvas em letras minúsculas. Nunca use CamelCase, iniciais maiúsculas ou capitalização de nomes próprios. Exemplos corretos: `jotaquest`, `timmaia`, `musicabrasileira`, `rockinrio`, `curiosidade`.
- Antes do commit/queue, faça uma checagem final das TRÊS plataformas: nenhum array `hashtags` pode estar ausente ou vazio; o texto renderizado deve ter apenas UM bloco de hashtags no final e nenhuma hashtag pode estar repetida.

Exemplo ERRADO:

```text
caption: "... #JotaQuest #TimMaia #MusicaBrasileira #RockInRio"
hashtags: ["JotaQuest", "TimMaia", "MusicaBrasileira", "RockInRio"]
```

Exemplo CORRETO:

```text
caption: "Em 1998, o Jota Quest foi retirado do palco durante um show de Tim Maia. O próprio Tim interveio, nasceu a ideia de uma colaboração e, 28 anos depois, a história virou um álbum-tributo."
hashtags: ["jotaquest", "timmaia", "musicabrasileira", "rockinrio", "curiosidade"]
```

## Regra prática

Nunca empilhe no mesmo beat:

- legenda falada longa ou densa;
- `text_fx` grande;
- highlight chamativo;
- overlay textual relevante.

Escolha uma camada editorial principal além da legenda, no máximo, e somente quando houver espaço visual claro.
