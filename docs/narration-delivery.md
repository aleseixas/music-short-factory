# Narração com delivery por segmento

O Music Short Factory suporta direção de voz por segmento em `story.json`.

A intenção editorial é permitir que o GPT/agente trate a narração como parte da edição: hook, contexto, emoção, tensão, reveal e payoff podem receber entregas diferentes quando isso melhora o vídeo.

A `main` é sempre a fonte da verdade. Os nomes, comportamento e limitações abaixo devem ser confirmados no código atual antes da autoria.

## Campo `delivery`

Cada item de `segments` pode declarar `delivery`:

```json
{
  "id": "hook",
  "text": "Ninguém esperava o que aconteceu depois.",
  "delivery": "hook"
}
```

No estado atual, os valores suportados são:

- `neutral`
- `hook`
- `curious`
- `emotional`
- `dramatic`
- `reveal`
- `payoff`

Nunca invente outro valor. Se a `main` mudar, os enums do código prevalecem.

O campo é opcional para compatibilidade com episódios antigos. Quando o episódio usa delivery segmentado, o engine sintetiza os segmentos individualmente e concatena a narração preservando os timings globais.

## Uso editorial

Delivery é uma ferramenta narrativa, não uma quota.

Escolha a intenção que melhor corresponde ao texto e ao papel do segmento:

- `hook`: abertura que precisa prender atenção imediatamente;
- `curious`: pergunta, mistério, contexto intrigante ou informação que prepara uma descoberta;
- `emotional`: momento íntimo, triste, sensível ou reflexivo;
- `dramatic`: tensão, conflito, consequência forte ou virada pesada;
- `reveal`: descoberta, resposta, surpresa ou informação que resolve uma expectativa;
- `payoff`: conclusão, frase final ou fechamento memorável;
- `neutral`: contexto factual ou trecho que funciona melhor sem intervenção perceptível.

Essas relações são guias editoriais, não regras rígidas. O texto continua sendo mais importante que o preset.

Não alterne deliveries apenas para criar variedade. Uma sequência pode permanecer `neutral` ou usar o mesmo delivery em mais de um segmento quando essa for a melhor decisão.

## Voz editorial e linguagem conversada

A narração deve soar como uma pessoa jovem, curiosa e bem informada contando uma história muito interessante para outra pessoa — não como locução de telejornal, verbete enciclopédico ou mini-documentário excessivamente formal.

Use português brasileiro natural, oral e levemente informal. Prefira frases curtas, construções que soem bem faladas e pequenas reações do narrador quando elas aumentarem curiosidade, surpresa, tensão ou proximidade.

Expressões conversacionais podem aparecer ocasionalmente quando encaixarem de forma natural, por exemplo:

- `olha que loucura`;
- `cara`;
- `só que tem um detalhe`;
- `e é aí que fica interessante`;
- `agora olha isso`;
- `pois é`;
- `e o mais curioso é que...`;
- `só que aí aconteceu uma coisa`;
- `e tem mais`.

Esses exemplos NÃO são uma lista obrigatória nem bordões para repetir. Não force gíria em todo segmento, não use uma expressão só para parecer jovem e evite empilhar interjeições. A naturalidade vem primeiro.

O objetivo é aproximadamente **80% conversa natural + 20% personalidade/reação do narrador**, ajustando livremente quando o assunto pedir mais emoção, seriedade ou objetividade.

A personalidade deve aparecer também na forma de reagir à história, e não apenas pela inserção de gírias. Sempre que fizer sentido, transforme fatos secos em uma descoberta contada com intenção. Exemplo de direção:

- mais frio: `A música foi escrita após o término do relacionamento.`
- mais nativo de short: `Só que aí vem a parte meio absurda: ela escreveu isso logo depois do término.`

Outro exemplo:

- mais frio: `Anos depois, fãs continuaram investigando a identidade da pessoa citada.`
- mais nativo de short: `E olha que loucura: anos depois, os fãs ainda estavam tentando descobrir de quem ela estava falando.`

Não invente emoção, opinião ou reação que distorça os fatos. A linguagem pode ser espontânea; a informação continua factual e sustentada pelas fontes.

Evite especialmente:

- tom acadêmico ou excessivamente jornalístico quando não for necessário;
- frases que parecem texto escrito para leitura silenciosa e ficam artificiais em voz alta;
- gírias muito marcadas, datadas ou regionais sem motivo;
- repetição de `cara`, `olha que loucura`, `mano`, `tipo`, `bizarro` ou equivalentes;
- tentar transformar todo fato em choque, escândalo ou exagero;
- caricatura de linguagem de TikTok.

Pense no narrador como alguém dizendo mentalmente: **“cara, descobri uma história muito boa sobre essa música e preciso te contar”**, mas escreva cada roteiro com bom senso para que essa sensação seja percebida sem precisar repetir essa frase.

## Como o engine traduz a intenção

O schema de `story.json` permanece independente do provider. Cada provider pode traduzir um delivery apenas para os controles que suporta.

No provider Edge atual, os presets modificam de forma moderada parâmetros como velocidade, pitch e volume. Portanto, `delivery` representa uma **intenção de interpretação**, e não deve ser descrito como garantia de uma emoção humana específica.

Os ajustes atuais são deliberadamente moderados para preservar naturalidade e inteligibilidade.

## Duração e timings

Delivery pode alterar a duração real da fala.

Por isso:

- não calcule shots a partir de uma duração imaginada do preset;
- não force a narração a caber no `target_duration_seconds` por estimativa;
- use os timings e a duração reais produzidos pelo pipeline;
- text FX relativos continuam ligados aos segmentos e são resolvidos após os timestamps reais da voz.

O pipeline concatena os segmentos sintetizados e ajusta os word timings para a linha do tempo global.

## Áudio customizado

Áudio customizado tem prioridade sobre a síntese automática.

Quando `custom_voice.mp3` é usado pelo fluxo atual, os deliveries definidos nos segmentos não são aplicados àquele áudio. O agente não deve afirmar que a interpretação emocional foi aplicada se a narração final veio de áudio customizado.

## Compatibilidade e fallback

A feature foi projetada para preservar episódios antigos: segmentos sem `delivery` continuam válidos.

Providers que não suportarem tradução de delivery podem usar seus controles neutros conforme o comportamento atual do engine. A autoria não deve inventar capacidades específicas do provider.

## Regra para o GPT agendado

Ao criar um episódio novo:

1. escreva primeiro um roteiro natural, factual e pensado para ser falado em português brasileiro;
2. faça a narração soar conversada e humana, com informalidade leve e reações pontuais quando elas melhorarem o beat;
3. use expressões como `cara`, `olha que loucura`, `só que tem um detalhe` ou equivalentes apenas quando encaixarem naturalmente; nunca como quota ou bordão;
4. identifique a função narrativa de cada segmento;
5. escolha `delivery` somente entre os valores reais da `main`;
6. use mudanças de delivery quando elas reforçarem a curva narrativa;
7. preserve contraste entre momentos fortes e momentos neutros;
8. coordene voz, asset, motion, text FX, SFX e música quando fizerem parte do mesmo beat;
9. leia o roteiro final em voz alta mentalmente e reescreva trechos que soem como texto acadêmico, telejornal ou legenda enciclopédica;
10. deixe o pipeline determinar a duração e os timings reais.

A voz deve ajudar retenção e emoção sem virar caricatura, forçar gíria ou competir com clareza.