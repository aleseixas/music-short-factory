# Regra de end card + CTA contextual — Além do Hit

Esta regra complementa `templates/editorial-direction-prompt.md` e deve ser aplicada na criação de novos episódios sempre que a `main` atual continuar compatível com o contrato descrito abaixo.

## Objetivo

Todo episódio deve terminar com uma assinatura visual curta do **Além do Hit**, usando o template de marca aprovado em `assets/branding/end_card_template.jpg`, acompanhada por **um único CTA contextual** ligado diretamente ao conteúdo daquele episódio.

O CTA não é um bloco publicitário genérico. Ele deve parecer a última batida editorial da história.

## Regra principal do CTA

Nunca encerre automaticamente com `curta, compartilhe e comente` ou pedido triplo equivalente.

Escolha apenas **UMA ação principal** por episódio:

- `comment`: quando houver pergunta/opinião natural sobre música, álbum, artista, letra, teoria, ranking ou decisão criativa;
- `share`: quando a história tiver surpresa, polêmica, identificação, nostalgia ou algo que faça sentido mandar para um amigo/fã;
- `follow`: use com menor frequência, apenas quando o fechamento naturalmente convidar para outra história/música.

Prioridade editorial normal: `comment` ou `share` > `follow`.

O texto deve ser específico para o episódio. Exemplos de direção — NÃO copiar mecanicamente:

- `Qual faixa desse álbum é a melhor?`
- `Você acha que o Ye passou do limite?`
- `Manda praquele amigo que ainda defende essa música.`
- `Qual música dela merece o próximo vídeo?`
- `Você interpreta essa letra do mesmo jeito?`
- `Qual hit dessa era ainda está na sua playlist?`

Evite frases vazias como `Comenta aí`, `Compartilha`, `Segue para mais` sem contexto.

## Integração narrativa

O payoff da história continua sendo prioridade. Não sacrifique a conclusão factual/emocional só para encaixar CTA.

Quando o contrato atual continuar `1 segment = 1 shot`, faça o **último segmento** ser curto e servir de CTA contextual, imediatamente depois do payoff. Esse último segmento deve usar a end card como visual principal.

A narração do CTA deve ser curta e natural. Prefira aproximadamente 4–10 palavras quando isso funcionar; uma pergunta curta pode ocupar mais palavras se continuar ágil. Não estenda artificialmente o vídeo para explicar o CTA.

Se o CTA falado soar forçado, use uma frase mínima na voz e deixe a formulação principal no kinetic text da end card, respeitando o schema atual.

## Template visual fixo

Fonte canônica da marca:

`assets/branding/end_card_template.jpg`

O template é um **asset gráfico de branding pré-aprovado**, não um visual factual do episódio. Portanto ele é uma exceção explícita à regra que proíbe imagens geradas por IA como conteúdo visual. Essa exceção vale SOMENTE para este arquivo de identidade visual fixo; fotos, vídeos, eventos, artistas e demais visuais de conteúdo continuam proibidos de ser gerados por IA.

Não gere uma nova end card por episódio. Não altere logo, paleta, ícones ou layout do template.

## Como usar no episódio

O renderer atual só resolve assets principais dentro de `episodes/<slug>/assets/`. Portanto, antes de finalizar o episódio:

1. confirme que `assets/branding/end_card_template.jpg` existe na `main`;
2. copie/reutilize o blob EXATO desse arquivo em `episodes/<slug>/assets/end_card_template.jpg` sem recomprimir nem gerar variante;
3. adicione um asset local dedicado em `episodes/<slug>/assets.json`, por exemplo `end_card_brand`, apontando para `end_card_template.jpg` e sem URL remota;
4. use `end_card_brand` SOMENTE no último shot/segmento;
5. `motion` deve ser discreto, preferencialmente `hold`, e `transition_out` do último shot deve continuar `cut` se essa for a exigência atual da `main`.

A cópia do mesmo blob de branding ENTRE episódios é permitida e esperada. A regra de zero reuso visual continua valendo para conteúdo principal dentro de um episódio; a end card de branding não deve aparecer mais de uma vez no mesmo episódio.

Se a ferramenta GitHub permitir operações de árvore/blob, prefira reutilizar o SHA do blob já existente no arquivo canônico, em vez de recodificar a imagem.

## Texto dinâmico sobre a end card

Use `text_fx_cues` para escrever o CTA no grande espaço vazio central do template quando a `main` atual suportar essa capacidade.

Quando timing relativo por segmento estiver disponível, prefira:

- `segment`: ID do último segmento;
- `offset_seconds`: próximo de `0`;
- `duration_seconds`: cobrindo a maior parte do último segmento sem ultrapassá-lo;
- `position`: `center`;
- animação curta compatível com a `main`, normalmente `pop_in`, `scale_bounce` ou `fade_in`, escolhida conforme o tom;
- `accent_text`: opcional e somente quando aparecer literalmente no texto.

Headline visual ideal: aproximadamente **4–10 palavras**, em uma ou duas linhas. Pode chegar a três linhas somente se a leitura continuar imediata.

O texto deve ser facilmente entendido sem depender da legenda da voz.

Não cubra o logo superior nem os ícones inferiores. Use o espaço vazio central como área principal do CTA.

## Duração e ritmo

Não imponha uma duração rígida independente da narração, porque a duração real dos shots deriva dos timings de voz.

Como referência editorial, a end card deve ser percebida como uma assinatura rápida — normalmente algo próximo de **1–2 segundos** quando a frase curta permitir. Não deixe a tela parada por vários segundos sem necessidade.

Evite crossfade lento para a saída. Prefira entrada/corte limpo e final ágil.

## SFX

SFX na end card é opcional. Se usado, escolha apenas type existente no catálogo e mantenha volume discreto. Não use SFX apenas para preencher a tela final.

## Validação obrigatória antes da queue

Confirme:

- existe exatamente um CTA contextual final;
- o CTA pede apenas uma ação principal;
- o CTA é específico da música/artista/história do episódio;
- o payoff narrativo veio antes e não foi substituído por propaganda;
- `end_card_template.jpg` usado no episódio é cópia/reuso do asset canônico de branding;
- a end card aparece somente no último shot;
- o último shot continua respeitando `1 segment = 1 shot` e demais constraints atuais;
- o kinetic text cabe no centro e não invade logo/ícones;
- não foi gerada nova imagem de end card nem usado branding diferente;
- nenhuma outra imagem de conteúdo foi gerada por IA.

Se o arquivo canônico de branding estiver ausente na `main`, NÃO invente uma imagem substituta e NÃO bloqueie a criação inteira do episódio por isso: omita apenas a end card visual, mantenha um CTA contextual curto na narração quando editorialmente adequado e reporte a ausência do template no resumo operacional se houver campo apropriado.
