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

Esta é a regra vigente e **SOBREPÕE qualquer texto anterior que diga que o mesmo vídeo-fonte nunca pode aparecer em dois shots OU que permita vídeo `contextual`, `generic` ou sem `semantic_fit` como fallback**.

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
- distribua os reaproveitamentos ao longo do episódio e evite shots consecutivos da mesma fonte quando houver alternativa equivalente;
- quando um candidato repetido não trouxer trim explícito, o resolver pode atribuir um baseline temporal distinto automaticamente; ainda assim, um trim editorial explícito e semanticamente escolhido é preferível;
- o Best Segment continua podendo otimizar cada baseline dentro da sua vizinhança conservadora e não deve criar sobreposição com outro shot da mesma fonte;
- ao atingir o limite de usos ou quando não houver outro intervalo seguro, escolha outro vídeo relevante ou uma imagem relevante.

URLs, aliases ou nomes de arquivo diferentes que resolvam para o mesmo vídeo/provider continuam sendo a **mesma fonte** para controle de limite e sobreposição.

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

O primeiro visual editorial continua obrigatoriamente sendo VÍDEO. Portanto, o primeiro slot deve ter candidatos `direct/exact` reais e redundantes. Se um download falhar, tente os próximos vídeos `direct/exact`; não substitua a abertura por imagem nem por vídeo genérico. Monte o pool de abertura já com redundância suficiente para o auto-repair resolver sozinho.

## Prioridade editorial

A ordem editorial é:

1. vídeo `exact`;
2. vídeo `direct`;
3. imagem `exact` ou `direct`;
4. imagem `contextual`;
5. imagem `generic` apenas como último recurso real.

Vídeo `contextual`, `generic` ou sem classificação não entra nessa ordem porque é inelegível.

## Pool de candidatos — diversidade antes do resolver

Esta seção **SOBREPÕE o alvo antigo de 4–5 candidatos por slot** quando o tema tiver material visual suficiente. O resolver só consegue escolher entre o que recebeu; portanto, a qualidade e a diversidade do `visual_candidates.json` são responsabilidade editorial obrigatória.

Para slots visualmente ricos, mire normalmente em **8 candidatos reais por slot**, com a composição preferencial de **até 5 vídeos `exact/direct` de IDs/fontes distintos + até 3 imagens**. Quando a disponibilidade real não permitir isso, aceite um pool menor, mas tente manter **pelo menos 5 candidatos úteis** antes de desistir da busca. Não complete quantidade com material genérico ou irrelevante.

Regras obrigatórias para montar o pool:

- cada slot deve pesquisar a partir do seu `visual_intent`, e não apenas pelo nome do artista ou da música;
- quando a primeira busca trouxer vídeos repetidos, genéricos ou pouco ligados à fala, descarte-os e faça novas consultas semanticamente diferentes antes de fechar o slot;
- um mesmo YouTube `provider_id` conta como **uma única fonte** para diversidade do pool, mesmo que apareça com títulos, URLs ou trims diferentes;
- não deixe 2–3 IDs populares dominarem candidatos de muitos slots sem relação direta entre si;
- se a mesma fonte começar a aparecer em vários slots, continue pesquisando alternativas antes de aceitá-la novamente;
- todo candidato de vídeo deve declarar `semantic_fit` e ele deve ser `exact` ou `direct`;
- `contextual` e `generic` continuam permitidos somente para IMAGENS, respeitando a prioridade editorial acima;
- para pessoas, colaborações, bastidores, eventos ou locais citados na narração, faça buscas específicas com esses nomes/contextos em vez de substituir por um clipe musical genérico do artista;
- preserve diversidade entre fontes, eventos e momentos: performance, entrevista, bastidor, arquivo histórico, gravação, premiação e contexto documental podem coexistir quando fizerem sentido para a história;
- não trate cinco trims do mesmo vídeo como cinco bons candidatos de vídeo para o slot;
- antes de fechar `visual_candidates.json`, revise os IDs de vídeo do episódio inteiro. Se poucos IDs estiverem aparecendo repetidamente em muitos slots, reabra as buscas dos slots mais fracos;
- no primeiro slot, tenha redundância real de vídeos `direct/exact` para que falha de um provider/download não exija intervenção humana.

O objetivo não é maximizar contagem. É entregar ao resolver **opções semanticamente fortes e realmente diferentes** para que download, semantic gates, Best Segment, motion/static checks e ranking técnico tenham matéria-prima suficiente.

## Gate antes da queue

Antes de finalizar o episódio, confirme:

- nenhuma imagem principal foi reutilizada;
- todo vídeo-fonte usado mais de uma vez ficou em no máximo 3 shots;
- os intervalos reutilizados da mesma fonte são distintos e não se sobrepõem;
- o mesmo trecho não foi mascarado como novo take por crop/FX/speed;
- o Best Segment pode operar sem empurrar um take para cima do intervalo de outro shot da mesma fonte;
- a escolha de reutilizar uma fonte preserva ou melhora relevância semântica;
- slots visualmente ricos receberam variedade real de candidatos, em vez de pequenas variações dos mesmos poucos IDs;
- **todo vídeo usado é `exact` ou `direct`; nenhum vídeo `contextual`, `generic` ou sem `semantic_fit` foi usado**;
- o primeiro shot tem vídeo `exact/direct` e o pool de abertura possui alternativas reais para auto-recovery.

Esta regra substitui a política anterior de `zero reuso do vídeo-fonte` e qualquer fallback que aceitasse vídeo genérico. A política correta agora é: **zero reuso da mesma imagem, zero reuso do mesmo trecho de vídeo e zero vídeo genérico; uma mesma fonte relevante pode abastecer takes distintos com segmentos não sobrepostos, dentro do limite definido acima**.
