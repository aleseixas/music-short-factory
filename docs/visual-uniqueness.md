# Regra absoluta de unicidade visual

Esta regra é obrigatória para toda autoria de episódio do Music Short Factory e SOBREPÕE qualquer orientação anterior que permita reutilização como fallback.

## ZERO REUSO

Cada shot deve usar um visual principal único dentro do episódio.

- A mesma imagem nunca pode aparecer em dois shots.
- O mesmo vídeo-fonte nunca pode aparecer em dois shots.
- Outro trim do mesmo vídeo continua sendo o mesmo vídeo e não pode reaparecer.
- Crop, focus, speed, motion, transition, visual FX, overlay ou qualquer outro tratamento não transforma o mesmo visual em um asset novo.
- URLs diferentes que resolvam para o mesmo arquivo, upload, provider_id, página-fonte ou conteúdo visual devem ser tratadas como duplicata.
- Quando um visual é escolhido para um shot, ele fica reservado e não pode ser escolhido novamente em outro slot.
- A deduplicação deve ser GLOBAL entre todos os shots finais, não apenas dentro de cada pool de candidatos.
- Se o melhor candidato já foi usado, escolha o próximo melhor candidato válido do mesmo tipo e, se necessário, faça nova busca.

## SEM EXCEÇÃO DE FALLBACK

Não existe exceção para reutilizar imagem ou vídeo por conveniência, falta de tempo, economia de busca, diferença de trim/crop/FX ou porque os shots não são consecutivos.

Se ainda houver visual repetido, o episódio não está pronto. Substitua o visual repetido por outro asset único antes do commit/queue.

Antes da queue, confirme explicitamente que:

1. nenhuma imagem final aparece em mais de um shot;
2. nenhum vídeo-fonte final aparece em mais de um shot;
3. não existem aliases do mesmo visual por URL, file, provider_id ou página-fonte quando esses dados estiverem disponíveis.
