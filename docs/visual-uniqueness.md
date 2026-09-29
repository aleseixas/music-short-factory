# Unicidade visual no pipeline

<!-- pipeline-contract: config/pipeline-contract.json -->

A seleção para publicação e os dois preflights compartilham a regra estrita de
`check_episode_media_batch._collect_visual_structure_errors`: cada shot principal
usa uma imagem ou fonte de vídeo diferente. A regra preserva a proteção mais
forte do validator original; uma alteração de trim não torna a fonte inédita.

- Imagens repetidas são rejeitadas mesmo com crop, foco, movimento ou efeitos.
- Vídeos da mesma fonte são rejeitados mesmo com intervalos diferentes.
- URLs/aliases, IDs YouTube e arquivos equivalentes compartilham a mesma reserva.
- O pool deve permitir uma atribuição de fontes distintas a todos os slots.
- O primeiro take editorial precisa de vídeo real; uma imagem não é fallback
  aceito quando o vídeo falha.

O resolver CLI valida o pool, inspeciona/downloads os candidatos e reserva as
fontes conforme a seleção. Candidatos com problemas técnicos ou de identidade
não são promovidos a fallback no modo de publicação. A inspeção exploratória de
segmentos continua disponível para pesquisa, mas não equivale a PASS do pipeline.

Best Segment ainda pode melhorar o trim de uma fonte reservada ao seu shot.
Nenhum tratamento visual autoriza duplicar a fonte em outro shot.

Antes do pedido externo, execute o controlador descrito no
[contrato do pipeline](pipeline-contract.md). Se faltarem fontes acessíveis,
complete o pool do mesmo episódio; o gate nunca deve ser desativado para publicar.
