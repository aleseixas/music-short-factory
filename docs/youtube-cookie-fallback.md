# YouTube cookie fallback

The web visual resolver uses this authentication order for YouTube candidates:

1. normal/PO Token request first;
2. only when YouTube returns a bot/login authentication error, retry that candidate with cookies;
3. if the cookie retry also fails, keep the existing non-blocking visual fallback.

Cookies are optional. When the `YOUTUBE_COOKIES` GitHub Actions secret is absent, behavior stays the same as before.

## Secret format

Create a repository Actions secret named `YOUTUBE_COOKIES` containing the complete contents of a Netscape-format `cookies.txt` export from a secondary YouTube/Google account.

Do not commit `cookies.txt` to the repository. Do not use the main channel/account for this automation.

The resolver writes the secret to a temporary file only for the visual-resolution process, does not print its contents, and removes the temporary file when the process exits.

## Expected logs

When cookies are available, startup prints:

```text
YouTube web: cookies de fallback configurados; PO/default continua sendo a primeira tentativa.
```

Cookies are actually used only after an authentication/bot failure, which prints:

```text
YouTube web: tentativa primaria bloqueada; repetindo candidato com cookies de fallback.
```

A normal successful PO Token request never reaches the cookie retry.
