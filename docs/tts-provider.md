# TTS provider atual

Este documento descreve a arquitetura de narração atualmente usada pelo Music Short Factory. A `main` continua sendo a fonte da verdade; se o código divergir deste texto, siga o código atual.

## Cadeia de providers

No estado atual:

1. provider principal: `gemini`;
2. fallback: `edge`;
3. voz do fallback Edge: `pt-BR-AntonioNeural`;
4. velocidade base do fallback Edge: `+6%`.

O agente/editor NÃO escolhe o provider por episódio e NÃO grava credenciais, API keys ou campos específicos de provider em `story.json`/`timeline.json`. Essa seleção é responsabilidade do engine/configuração global.

## Gemini principal

O provider principal usa, no estado atual:

- modelo TTS: `gemini-3.1-flash-tts-preview`;
- voz: `Charon`;
- temperatura: `0.6`;
- idioma: português do Brasil;
- direção de voz: masculina, madura, confiante, tom médio-grave, ritmo rápido e fluido, conversa natural de creator para viewer, sem cadência de locutor comercial/radiofônico.

O objetivo editorial é manter energia de Shorts/Reels/TikTok sem soar teatral, artificialmente sorridente, publicitário ou cantado.

## Delivery por segmento

O schema editorial continua provider-independent. Os valores atualmente suportados em `story.json` permanecem:

- `neutral`
- `hook`
- `curious`
- `emotional`
- `dramatic`
- `reveal`
- `payoff`

No Gemini, esses deliveries são traduzidos pelo provider em tags/instruções de interpretação. O agente deve escolher apenas a intenção editorial correta; não deve escrever pitch, rate, volume, nome de voz ou prompt TTS diretamente no episódio.

No Edge fallback, o engine traduz os mesmos deliveries para os controles suportados pelo provider.

## Uma chamada de voz por narração

Quando o episódio possui múltiplos segmentos/deliveries, o provider Gemini atual tenta sintetizar a narração completa em uma única chamada, preservando as mudanças de interpretação por tags. Isso reduz requisições, melhora consistência de identidade vocal e evita concatenar uma chamada Gemini separada por frase.

Não divida manualmente a narração em múltiplas requisições nem altere o episódio para tentar economizar quota. O engine decide a estratégia de síntese.

## Timings e legendas

O TTS Gemini não fornece os mesmos `WordBoundary` nativos usados pelo Edge. Por isso, após gerar o áudio, o pipeline atual usa transcrição com timestamps por palavra para produzir `WordTiming` e manter captions/textos sincronizados.

O agente não precisa estimar timestamps de fala nem criar sidecars manualmente. Use os timings reais resolvidos pelo pipeline.

## Fallback automático

Se qualquer parte do caminho Gemini falhar — por exemplo indisponibilidade, quota/rate limit, erro de síntese, erro de transcrição, ausência de credencial no runtime, resposta inválida ou timing incompatível — o resolver tenta automaticamente o provider `edge` com `pt-BR-AntonioNeural`.

O episódio não deve ser reescrito nem reconfigurado para provocar o fallback. O mesmo `story.json` deve permanecer válido em ambos os providers.

## Regra para o GPT/agente

Ao criar episódios:

- escolha `delivery` pela função narrativa;
- não mencione nem manipule `GEMINI_API_KEY`;
- não crie configuração de TTS específica dentro do episódio;
- não suponha que cada segmento gera uma chamada separada;
- não estime timings quando o pipeline fornece timings reais;
- não trate Charon ou Antonio como conteúdo editorial do vídeo;
- confirme sempre o comportamento vigente na `main` antes de assumir detalhes técnicos.
