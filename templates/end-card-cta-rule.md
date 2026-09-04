# Regra de CTA contextual final — Além do Hit

Esta regra complementa `templates/editorial-direction-prompt.md` e deve ser aplicada na criação de novos episódios sempre que a `main` atual continuar compatível com o contrato descrito abaixo.

## Objetivo

Todo episódio deve terminar com **um único CTA contextual**, curto e diretamente ligado à história daquele episódio.

O CTA deve parecer a última batida editorial da narrativa — não um bloco publicitário genérico.

## Regra absoluta — sem end card visual

A arte `assets/branding/end_card_template.jpg` fica **DESATIVADA para novos episódios**.

- NÃO copiar esse arquivo para `episodes/<slug>/assets/`;
- NÃO adicionar esse asset ao `assets.json`;
- NÃO usar essa imagem no primeiro shot;
- NÃO usar essa imagem no meio do vídeo;
- NÃO usar essa imagem no último shot;
- NÃO gerar uma nova end card por episódio;
- NÃO substituir por outra arte genérica de branding.

O arquivo pode continuar existindo na repo apenas por histórico/compatibilidade. Sua presença na `main` NÃO significa autorização editorial para usá-lo.

## Primeiro visual

O primeiro visual continua seguindo `templates/short-form-style-rule.md`: nos primeiros 0,0–1,5s, mostrar capa oficial da música/álbum/single OU artista principal claramente reconhecível.

## Fluxo editorial esperado

`capa/artista reconhecível → história visual contextual → payoff → CTA contextual curto sobre o último visual da história`

O último visual deve continuar pertencendo ao conteúdo do episódio. Prefira um asset forte e semanticamente ligado ao payoff, à música ou ao artista.

## Regra principal do CTA

Nunca encerre automaticamente com `curta, compartilhe e comente` ou pedido triplo equivalente.

Escolha apenas **UMA ação principal** por episódio:

- `comment`: quando houver pergunta/opinião natural sobre música, álbum, artista, letra, teoria, ranking ou decisão criativa;
- `share`: quando a história tiver surpresa, polêmica, identificação, nostalgia ou algo que faça sentido mandar para um amigo/fã;
- `follow`: use com menor frequência, apenas quando o fechamento naturalmente convidar para outra história/música.

Prioridade editorial normal: `comment` ou `share` > `follow`.

O texto deve ser específico para o episódio. Exemplos de direção — NÃO copiar mecanicamente:

- `Qual faixa desse álbum é a melhor?`
- `Você acha que ele passou do limite?`
- `Manda praquele amigo que ainda defende essa música.`
- `Qual música dela merece o próximo vídeo?`
- `Você interpreta essa letra do mesmo jeito?`
- `Qual hit dessa era ainda está na sua playlist?`

Evite frases vazias como `Comenta aí`, `Compartilha` ou `Segue para mais` sem contexto.

## Integração narrativa

O payoff da história continua sendo prioridade. Não sacrifique a conclusão factual/emocional só para encaixar CTA.

Quando o contrato atual continuar `1 segment = 1 shot`, faça o último segmento ser curto e servir de CTA contextual imediatamente depois do payoff, mantendo como visual principal um asset normal do episódio.

A narração do CTA deve ser curta e natural. Prefira aproximadamente 4–10 palavras quando isso funcionar; uma pergunta curta pode ocupar mais palavras se continuar ágil.

Não estenda artificialmente o vídeo para explicar o CTA.

## CTA visual com text FX

Quando a `main` suportar `text_fx_cues` relativos por segmento, o CTA pode aparecer sobre o último visual do episódio.

Prefira:

- `segment`: último segmento;
- `offset_seconds`: próximo de `0` ou depois do payoff, conforme a fala;
- `duration_seconds`: suficiente para leitura sem ultrapassar o segmento;
- `position`: `center` ou outra zona que NÃO conflite com legenda falada;
- animação curta e discreta compatível com o tom;
- texto aproximadamente 4–10 palavras.

A legenda falada continua tendo prioridade conforme `templates/caption-layout-rule.md`. Se o CTA visual competir com a legenda, reduza, atrase ou omita o `text_fx` e mantenha apenas o CTA falado.

## Duração e ritmo

O CTA final deve ser rápido. Como referência editorial, mire normalmente algo próximo de **1–2,5 segundos** quando a frase permitir, sem criar uma pausa morta no final.

O último visual pode durar mais se já estiver sustentando o payoff e tiver contexto real, respeitando as regras de pacing de `templates/short-form-style-rule.md`.

## SFX

SFX no CTA é opcional. Se usado, escolha apenas type existente no catálogo e mantenha volume discreto. Não use SFX apenas para preencher o encerramento.

## Validação obrigatória antes da queue

Confirme:

- existe exatamente um CTA contextual final;
- o CTA pede apenas uma ação principal;
- o CTA é específico da música/artista/história do episódio;
- o payoff narrativo veio antes e não foi substituído por propaganda;
- `assets/branding/end_card_template.jpg` NÃO foi usado no episódio;
- nenhuma outra end card genérica foi criada para substituí-lo;
- o último visual pertence à história e tem relação semântica clara com o encerramento;
- o último shot continua respeitando `1 segment = 1 shot` e demais constraints atuais;
- CTA visual não disputa espaço com legenda falada;
- nenhuma imagem de conteúdo foi gerada por IA.
