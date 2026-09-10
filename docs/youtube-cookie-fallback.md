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
YT_DLP_AUTH id=<youtube_id> primary=FAIL reason=<motivo>; fallback=cookies_web_embedded
YT_DLP_AUTH id=<youtube_id> fallback=SUCCESS
```

A normal successful PO Token request never reaches the cookie retry.

Candidate diagnostics use these events:

```text
VIDEO_ATTEMPT slot=<slot> candidate=<n> id=<youtube_id> url=<canonical_url> downloader=yt-dlp
YT_DLP_RESULT id=<youtube_id> status=DOWNLOADED|CACHE_HIT|CACHE_INVALID|FAIL ...
VIDEO_RESULT slot=<slot> candidate=<n> id=<youtube_id> downloader=yt-dlp acquisition=SUCCESS|FAIL status=ELIGIBLE|SCORED_ONLY|FAIL ...
VIDEO_SELECTED slot=<slot> id=<youtube_id> ...
```

On failure, the reason is retained in sanitized form. Cookie contents, tokens,
private query strings and the temporary cookie file are never printed.
