# Override obrigatório — unicidade visual por conteúdo e segmento

Leia esta regra antes de fechar `assets.json`, `visual_candidates.json` e `timeline.json`.

Esta é a regra vigente e **SOBREPÕE qualquer texto anterior que diga que o mesmo vídeo-fonte nunca pode aparecer em dois shots**.

## Imagens — zero reuso

A mesma imagem principal NUNCA pode ser usada em dois shots do episódio.

Crop, focus, zoom, motion, transition, visual FX, overlay ou qualquer outro tratamento NÃO transforma a mesma imagem em um visual novo.

## Vídeos — mesma fonte pode alimentar takes diferentes

O mesmo vídeo-fonte PODE ser usado em mais de um shot quando cada uso corresponde a um **trecho temporal realmente diferente e não sobreposto**.

Regras obrigatórias:

- use normalmente no máximo **3 shots por vídeo-fonte** no mesmo episódio;
- para cada uso, escolha `source_start_seconds` e, quando fizer sentido, `source_end_seconds` com intenção semântica própria;
- NÃO reutilize o mesmo intervalo nem intervalos que se sobreponham;
- mudar crop, focus, speed, motion, transition, visual FX, overlay ou outro tratamento sobre o MESMO TRECHO não cria um novo take;
- distribua os reaproveitamentos ao longo do episódio e evite shots consecutivos da mesma fonte quando houver alternativa contextual equivalente;
- quando um candidato repetido não trouxer trim explícito, o resolver pode atribuir um baseline temporal distinto automaticamente; ainda assim, um trim editorial explícito e semanticamente escolhido é preferível;
- o Best Segment continua podendo otimizar cada baseline dentro da sua vizinhança conservadora e não deve criar sobreposição com outro shot da mesma fonte;
- ao atingir o limite de usos ou quando não houver outro intervalo seguro, escolha outro vídeo relevante ou uma imagem relevante.

URLs, aliases ou nomes de arquivo diferentes que resolvam para o mesmo vídeo/provider continuam sendo a **mesma fonte** para controle de limite e sobreposição.

## Prioridade editorial

Diversidade de fontes continua desejável, mas não desperdice um vídeo longo e altamente relevante só para obedecer uma regra artificial de um único uso por fonte.

A ordem editorial é:

1. vídeo/trecho realmente relevante para o que está sendo narrado;
2. imagem realmente relevante;
3. vídeo genérico apenas quando ainda tiver função contextual clara;
4. imagem genérica como último recurso.

Nunca escolha um vídeo sem relação com a fala apenas para aumentar a porcentagem de movimento.

## Gate antes da queue

Antes de finalizar o episódio, confirme:

- nenhuma imagem principal foi reutilizada;
- todo vídeo-fonte usado mais de uma vez ficou em no máximo 3 shots;
- os intervalos reutilizados da mesma fonte são distintos e não se sobrepõem;
- o mesmo trecho não foi mascarado como novo take por crop/FX/speed;
- o Best Segment pode operar sem empurrar um take para cima do intervalo de outro shot da mesma fonte;
- a escolha de reutilizar uma fonte preserva ou melhora relevância semântica.

Esta regra substitui a política anterior de `zero reuso do vídeo-fonte`. A política correta agora é: **zero reuso da mesma imagem e zero reuso do mesmo trecho de vídeo; uma mesma fonte de vídeo pode abastecer takes distintos com segmentos não sobrepostos, dentro do limite definido acima**.
