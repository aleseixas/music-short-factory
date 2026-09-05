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

## Seleção da história — escolha o assunto antes de tentar salvar o roteiro

Uma música famosa não é automaticamente uma boa candidata a episódio. Antes de aprofundar roteiro, assets ou edição, procure **qual é a história forte daquela música** e compare a força desse ângulo com as outras candidatas disponíveis.

Não escolha uma música só porque ela é popular e depois tente transformar fatos fracos em narrativa. Quando houver várias candidatas viáveis, favoreça a que tiver uma história mais clara, humana, surpreendente, visualizável e com consequência real.

### Archetypes de história com prioridade alta

Procure especialmente por histórias que contenham um ou mais destes motores narrativos:

- **quase não aconteceu:** música quase descartada, recusada, perdida, não lançada, retirada do álbum ou salva no último momento;
- **decisão pequena que mudou tudo:** troca de uma palavra, refrão, arranjo, sample, instrumento, produtor, colaborador, velocidade, estrutura ou performance que alterou radicalmente o resultado;
- **segredo ou detalhe escondido:** vocal, sample, pessoa, ruído, mensagem, referência, gravação, crédito, easter egg ou detalhe que a maioria do público não percebe;
- **significado inesperado:** letra, título ou conceito que parece falar de uma coisa mas tem origem, alvo ou contexto diferente;
- **mito vs. realidade:** uma interpretação popular ou história repetida sobre a música que muda quando as fontes primárias/secundárias confiáveis são examinadas;
- **conflito humano:** artista vs. gravadora, produtor, colaborador, ex-parceiro, família, imprensa, público ou outro personagem relevante;
- **personagem inesperado:** alguém pouco associado à música entra na história e muda seu rumo — músico de sessão, produtor, compositor, executivo, fã, diretor, parceiro, técnico, amigo etc.;
- **transformação:** demo muito diferente → versão final, música pequena → fenômeno, faixa rejeitada → hit, artista desconhecido → virada de carreira;
- **colaboração improvável:** encontro de universos, artistas ou produtores que parecia pouco provável e produziu um resultado marcante;
- **consequência grande a partir de um evento específico:** uma escolha, apresentação, ligação, vazamento, entrevista, acidente, rejeição ou oportunidade que desencadeou algo muito maior;
- **objeto/pista concreta:** fita, carta, mensagem, demo, instrumento, arquivo, contrato, gravação, áudio, foto ou outro elemento físico/digital que permita contar a história por evidências;
- **origem pessoal forte:** experiência real, relacionamento, perda, medo, fama, pressão, rejeição, família ou acontecimento que esteja diretamente ligado à composição e seja sustentado por fonte confiável.

Esses archetypes não são uma checklist nem garantia automática de bom episódio. A prioridade continua sendo a **força específica da história encontrada**.

### Histórias de prioridade média

Podem funcionar muito bem quando houver consequência, personagem ou detalhe memorável:

- bastidor técnico de produção;
- origem da composição;
- mudança de arranjo ou gravação;
- colaboração;
- performance importante;
- contexto cultural;
- sample/interpolação;
- estratégia de lançamento;
- recepção inicial muito diferente do resultado posterior.

Um bastidor técnico só deve ganhar prioridade se puder ser explicado de forma simples e se mudar a maneira como o público entende ou ouve a música.

### Sinais de história fraca

Rebaixe candidatas cuja pesquisa produz principalmente:

- data de lançamento;
- posição em charts;
- número de streams;
- certificações;
- lista de prêmios;
- descrição genérica do gênero/álbum;
- sequência de fatos cronológicos sem conflito, surpresa, decisão ou consequência;
- curiosidades que poderiam ser trocadas por qualquer outra música sem mudar muito o vídeo.

**“Foi lançada em X, chegou a Y e ganhou Z” não é história suficiente por si só.** Esses dados podem reforçar uma narrativa forte, mas não devem ser o coração do episódio.

Se depois de pesquisa real a melhor história continuar fraca, descarte essa candidata e avance. Não force episódio só porque a música é famosa.

### Pesquisa para descobrir o melhor ângulo

Não pesquise apenas `história da música` ou `significado da letra`. Expanda a investigação conforme a candidata:

- entrevistas do artista, compositor, produtor e colaboradores;
- making of, track-by-track e comentários de estúdio;
- créditos oficiais e bancos de composição quando relevantes;
- demos, versões iniciais, remixes e performances antigas;
- samples, interpolações e músicos de sessão;
- matérias da época e entrevistas posteriores olhando para trás;
- controvérsias, recusas, mudanças de gravadora/álbum, conflitos e decisões de lançamento;
- videoclipes, diretores, objetos ou cenas citadas na história;
- relatos de fãs/Reddit como **pistas para investigação**, nunca como confirmação factual isolada;
- falas públicas do fandom que ajudem a descobrir perguntas recorrentes, mitos ou detalhes pouco conhecidos.

Quando uma pista parecer forte, procure confirmação em fonte melhor antes de transformá-la em afirmação no roteiro.

### Critério de escolha entre candidatas

Antes de decidir definitivamente pela música, faça uma comparação mental simples:

`FORÇA DO HOOK | HISTÓRIA HUMANA | SURPRESA | PROGRESSÃO | EVIDÊNCIA | CONSEQUÊNCIA | PAYOFF | POTENCIAL VISUAL`

Não persista esse score como campo novo. Ele serve apenas para evitar que fama da música substitua qualidade narrativa.

Entre duas músicas com popularidade semelhante, prefira a que tiver melhor história. Entre uma música gigantesca com história fraca e uma música ainda reconhecível com história excepcional, a segunda pode ser a melhor candidata editorial.

## Retenção aplicada ao roteiro curto

O objetivo não é escrever o roteiro mais bonito, mais rebuscado ou mais “documental”. O objetivo é produzir o **melhor short possível para TikTok, Reels e YouTube Shorts**. Clareza, curiosidade, progressão e payoff vêm antes de floreio.

### Abertura e promessa

Antes de fechar o roteiro, crie mentalmente pelo menos **3 opções de hook** com ângulos diferentes — por exemplo: conflito/contradição, revelação surpreendente, pergunta intrigante, consequência inesperada ou detalhe escondido — e escolha a mais forte que continue factual. Não persista as alternativas no episódio.

A abertura deve:

- gerar interesse perceptível imediatamente, sem saudação, apresentação do canal ou contexto burocrático;
- deixar claro muito cedo qual é a curiosidade, conflito, descoberta ou promessa narrativa;
- combinar com título/capa/headline e com o que o vídeo realmente entrega; nunca aumente artificialmente a promessa e depois entregue algo menor;
- preferir informação concreta, contraste, consequência, pergunta forte ou imagem mental específica a frases vagas como “essa história é incrível”;
- começar já dentro da história. Se uma frase pode ser removida sem prejudicar entendimento, provavelmente não deve estar antes do primeiro fato interessante.

Para Shorts, não trate CTR de thumbnail como equivalente ao long-form. Quando analytics estiverem disponíveis, dê mais peso à capacidade de **fazer a pessoa permanecer após os segundos iniciais**, além de AVD/AVP e retenção ao longo do vídeo. Não invente métricas quando elas não estiverem disponíveis.

### Progressão, micro-payoffs e re-engagement

Depois do hook, pare de apenas prometer e **comece a entregar**. Cada segmento deve acrescentar pelo menos uma destas coisas: informação nova, consequência, conflito, evidência, contexto indispensável, surpresa, emoção, comparação ou avanço real em direção ao payoff.

Evite duas ou três frases consecutivas que apenas reformulem a mesma ideia. Se um trecho só “prepara” e não entrega nada novo, compacte, una a outro segmento ou corte.

Para vídeos de aproximadamente 60–90s, pense em progressão como uma escada, não como linha reta. Além do payoff final, distribua **micro-payoffs** e momentos de re-engagement ao longo do corpo. Re-engagement significa conteúdo novo que renova a curiosidade — uma virada, pergunta, evidência, frase forte, estatística, contradição, mudança de perspectiva ou novo personagem — e não simplesmente um zoom, SFX ou corte aleatório.

Não transforme isso em cronômetro rígido. Como sanity check, um vídeo longo dentro do formato short não deve atravessar grandes blocos de tempo sem introduzir uma nova razão para continuar assistindo.

### Simplicidade de primeira escuta

Escreva para ser entendido **na primeira vez, em velocidade normal, no celular**.

- uma frase deve ter uma função narrativa principal;
- prefira ordem direta e palavras comuns quando duas formulações dizem a mesma coisa;
- datas, cargos, nomes completos e números só entram quando ajudam a história ou tornam o fato mais convincente;
- se houver muitos nomes, reapresente a relação relevante em vez de exigir memória do espectador;
- não confunda profundidade com excesso de contexto;
- preserve nuance factual, mas explique ideias complexas da forma mais simples possível.

### Sem momentos mortos

Faça uma auditoria negativa de cada segmento: **“por que alguém continuaria assistindo depois desta frase?”** Se a resposta for apenas “porque a próxima frase é melhor”, o segmento atual precisa ser melhorado, fundido ou removido.

“Sem momento morto” não significa gritar, acelerar tudo ou colocar efeito em cada segundo. Um momento calmo pode ser excelente se tiver emoção, tensão, informação ou expectativa. O problema é o trecho sem função.

### Payoff e final

A promessa do hook precisa receber resposta clara. O payoff final deve parecer consequência natural da progressão anterior, não uma conclusão genérica adicionada porque o vídeo acabou.

Sempre que a história permitir, guarde a **melhor formulação da conclusão** para o final, mas não esconda todo o valor até lá: o corpo precisa continuar entregando micro-payoffs.

Depois do payoff, encerre rápido. Evite recapitulação longa, “e é isso”, despedida ou um novo bloco explicativo. Se houver CTA por estratégia do episódio, ele deve ser curto e não atrasar nem substituir o payoff.

### Frescor entre episódios

Quando o histórico recente estiver acessível, confira aproximadamente os últimos 10 episódios e evite repetir em sequência o mesmo molde de abertura ou de progressão. Varie naturalmente entre conflito, origem, bastidor, mito vs. realidade, consequência, pergunta, objeto/pista, citação, comparação, cronologia, revelação etc.

Não varie por obrigação se um formato continuar sendo claramente o melhor. O objetivo é evitar que o canal pareça um template que apenas troca artista e música.

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

1. antes de aprofundar uma candidata, descubra e compare a força real da história dela; fama da música não substitui narrativa;
2. procure primeiro archetypes fortes como quase-não-aconteceu, decisão que mudou tudo, segredo/detalhe escondido, significado inesperado, mito vs. realidade, conflito humano, personagem inesperado, transformação ou consequência marcante;
3. descarte candidatas cuja melhor história continue sendo apenas lançamento/charts/prêmios depois de pesquisa real;
4. escreva um roteiro natural e factual somente depois que a candidata tiver um ângulo forte;
5. gere mentalmente múltiplos hooks, escolha o melhor e alinhe hook, promessa e payoff;
6. identifique a função narrativa de cada segmento e elimine preparação repetitiva ou sem entrega;
7. distribua progressão, micro-payoffs e re-engagements de conteúdo ao longo do roteiro;
8. faça uma auditoria de retenção segmento por segmento, perguntando por que o espectador continuaria;
9. escolha `delivery` somente entre os valores reais da `main`;
10. use mudanças de delivery quando elas reforçarem a curva narrativa;
11. preserve contraste entre momentos fortes e momentos neutros;
12. coordene voz, asset, motion, text FX, SFX e música quando fizerem parte do mesmo beat;
13. encerre logo após um payoff forte, sem recapitulação ou despedida longa;
14. deixe o pipeline determinar a duração e os timings reais.

A voz deve ajudar retenção e emoção sem virar caricatura ou competir com clareza.