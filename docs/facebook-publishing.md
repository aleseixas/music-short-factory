# Publicacao de Reels no Facebook

O publisher envia o MP4 local para uma Pagina do Facebook pelo fluxo oficial de
Reels: inicia a sessao, faz upload binario no `rupload.facebook.com`, finaliza com
`video_state=PUBLISHED` e acompanha o status ate a fase de publicacao terminar.

## Secrets do GitHub Actions

Configure em **Settings > Secrets and variables > Actions**:

- `FACEBOOK_PAGE_ACCESS_TOKEN`: token de acesso da Pagina;
- `FACEBOOK_PAGE_ID`: ID numerico da Pagina.

O app Meta que emite o token precisa das permissoes `pages_show_list`,
`pages_read_engagement` e `pages_manage_posts`. O workflow fixa
`META_GRAPH_API_VERSION=v26.0`, assim como o publisher do Instagram.

Nao grave tokens em `post.json`, `.env` versionado, logs ou arquivos do
repositorio. Tokens de usuario e de Pagina nao sao equivalentes; use o token da
mesma Pagina identificada por `FACEBOOK_PAGE_ID`.

## Uso local

Valide sem chamar a API:

```bash
python publish.py <slug> --platform facebook --dry-run
```

Publique de verdade somente depois de configurar as credenciais:

```bash
python publish.py <slug> --platform facebook --live
```

Novos `post.json` recebem um bloco `facebook` com `title`, `caption` e
`hashtags`. Episodios antigos continuam validos: quando esse bloco nao existe,
os metadados sao derivados de `youtube.title` e do bloco `instagram` em memoria.
