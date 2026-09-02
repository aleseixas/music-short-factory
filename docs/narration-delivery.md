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

1. escreva primeiro um roteiro natural e factual;
2. identifique a função narrativa de cada segmento;
3. escolha `delivery` somente entre os valores reais da `main`;
4. use mudanças de delivery quando elas reforçarem a curva narrativa;
5. preserve contraste entre momentos fortes e momentos neutros;
6. coordene voz, asset, motion, text FX, SFX e música quando fizerem parte do mesmo beat;
7. deixe o pipeline determinar a duração e os timings reais.

A voz deve ajudar retenção e emoção sem virar caricatura ou competir com clareza.