# Override obrigatório — unicidade visual

Leia esta regra antes de fechar `assets.json`, `visual_candidates.json` e `timeline.json`.

REGRA ABSOLUTA: NUNCA reutilize a mesma imagem ou o mesmo vídeo-fonte em mais de um shot do episódio.

Outro trim, crop, focus, speed, motion, transition, visual FX ou overlay NÃO transforma o mesmo visual em asset novo.

Quando um visual for escolhido para um shot, reserve-o para aquele shot e remova-o da disputa dos demais slots. Faça deduplicação GLOBAL dos visuais finais.

Se houver duplicata, substitua o shot posterior pelo próximo melhor candidato válido do mesmo tipo e faça nova busca se necessário.

NÃO existe exceção de fallback para reutilização. Se ainda houver imagem ou vídeo-fonte repetido, o episódio não está pronto e a queue não deve ser criada.

Esta regra SOBREPÕE qualquer texto anterior que permita repetição como fallback.
