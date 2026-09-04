from __future__ import annotations

from http.cookiejar import MozillaCookieJar
import json
import os
from pathlib import Path

import requests

import engine.visual_search_web as web_engine
import resolve_visual_candidates_web as resolver
import resolve_visual_candidates_web_auth as youtube_auth


DEFAULT_URL = "https://www.youtube.com/watch?v=yszWh_7xYrs"
ACCOUNT_SWITCHER_URL = "https://www.youtube.com/getAccountSwitcherEndpoint"


def _text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    simple = value.get("simpleText")
    if isinstance(simple, str):
        return simple.strip()
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(
            str(run.get("text") or "")
            for run in runs
            if isinstance(run, dict)
        ).strip()
    return ""


def _load_cookie_jar(cookie_file: Path) -> MozillaCookieJar:
    jar = MozillaCookieJar(str(cookie_file))
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def _parse_xssi_json(raw: str) -> dict:
    text = raw.lstrip("\ufeff")
    if text.startswith(")]}'"):
        newline = text.find("\n")
        text = text[newline + 1 :] if newline >= 0 else text[4:]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("YouTube account switcher returned a non-object JSON response")
    return data


def _account_sections(payload: dict) -> list[dict]:
    root = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    actions = root.get("actions") if isinstance(root, dict) else None
    if not isinstance(actions, list):
        return []

    for action in actions:
        if not isinstance(action, dict):
            continue
        menu = (
            action.get("getMultiPageMenuAction", {})
            .get("menu", {})
            .get("multiPageMenuRenderer", {})
        )
        sections = menu.get("sections") if isinstance(menu, dict) else None
        if not isinstance(sections, list):
            continue
        return [
            section["accountSectionListRenderer"]
            for section in sections
            if isinstance(section, dict)
            and isinstance(section.get("accountSectionListRenderer"), dict)
        ]
    return []


def _extract_account_report(payload: dict) -> tuple[list[dict], list[dict]]:
    google_accounts: list[dict] = []
    identities: list[dict] = []

    for index, section in enumerate(_account_sections(payload), start=1):
        header = section.get("header") if isinstance(section.get("header"), dict) else {}
        google_header = header.get("googleAccountHeaderRenderer") if isinstance(header, dict) else None
        section_header = header.get("accountItemSectionHeaderRenderer") if isinstance(header, dict) else None

        google_name = ""
        if isinstance(google_header, dict):
            google_name = _text(google_header.get("name"))
        elif isinstance(section_header, dict):
            google_name = _text(section_header.get("title"))

        account = {
            "index": index,
            "name": google_name,
            "identity_count": 0,
        }

        contents = section.get("contents")
        if not isinstance(contents, list):
            contents = []

        for content in contents:
            if not isinstance(content, dict):
                continue
            item_section = content.get("accountItemSectionRenderer")
            if not isinstance(item_section, dict):
                continue
            items = item_section.get("contents")
            if not isinstance(items, list):
                continue

            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                item = raw_item.get("accountItem")
                if not isinstance(item, dict):
                    continue

                identity = {
                    "google_account_index": index,
                    "name": _text(item.get("accountName")),
                    "handle": _text(item.get("channelHandle")),
                    "selected": bool(item.get("isSelected")),
                    "has_channel": item.get("hasChannel"),
                }
                identities.append(identity)
                account["identity_count"] += 1

        # Ignore any malformed/empty non-account section.
        if account["identity_count"]:
            google_accounts.append(account)

    return google_accounts, identities


def _print_cookie_identity_report() -> None:
    cookie_file = youtube_auth._prepare_cookie_file()
    if cookie_file is None:
        print("::warning::Nao foi possivel preparar YOUTUBE_COOKIES para verificar a identidade.")
        return

    session = requests.Session()
    session.cookies = _load_cookie_jar(cookie_file)
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/152.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://www.youtube.com/",
        }
    )

    response = session.get(ACCOUNT_SWITCHER_URL, timeout=20)
    response.raise_for_status()
    payload = _parse_xssi_json(response.text)
    google_accounts, identities = _extract_account_report(payload)

    active = next((item for item in identities if item["selected"]), None)
    print("YouTube cookie session identity report:")
    print(f"  Google accounts in cookie session: {len(google_accounts)}")
    print(f"  YouTube identities/channels visible: {len(identities)}")

    if active:
        active_label = active["name"] or "<sem nome>"
        active_handle = active["handle"] or "<sem handle>"
        print(f"  Active YouTube identity: {active_label} {active_handle}")
        print(f"  Active identity belongs to Google account slot: {active['google_account_index']}")
    else:
        print("  Active YouTube identity: <nao identificada>")

    for account in google_accounts:
        # Do not print e-mail addresses from the account switcher. The active
        # channel name/handle is enough to identify which YouTube identity is in use.
        print(
            f"  Google account slot {account['index']}: "
            f"{account['identity_count']} YouTube identity/identities"
        )
        for identity in identities:
            if identity["google_account_index"] != account["index"]:
                continue
            marker = "ACTIVE" if identity["selected"] else "available"
            name = identity["name"] or "<sem nome>"
            handle = identity["handle"] or "<sem handle>"
            print(f"    - [{marker}] {name} {handle}")


def main() -> int:
    target_url = str(os.getenv("YOUTUBE_AUTH_TEST_URL") or DEFAULT_URL).strip()
    cookies_configured = bool(str(os.getenv("YOUTUBE_COOKIES") or "").strip())
    print(f"YouTube auth smoke test: cookies secret configured={'yes' if cookies_configured else 'no'}")
    if not cookies_configured:
        print("::error::YOUTUBE_COOKIES nao esta configurado; teste de fallback nao pode ser concluido.")
        return 2

    try:
        _print_cookie_identity_report()
    except Exception as exc:
        # Identity reporting is diagnostic only; do not expose cookie contents and
        # do not make it a hard dependency for the actual YouTube download fallback.
        print(f"::warning::Nao foi possivel ler o seletor de contas do YouTube: {exc}")

    # This installs the existing primary PO-token path first and then the cookie
    # fallback wrapper. No episode is rendered and no platform publishing code runs.
    resolver._install_patches()
    YoutubeDL, DownloadError = web_engine._yt_dlp_api()

    params = {
        "noplaylist": True,
        "skip_download": True,
        "quiet": False,
        "no_warnings": False,
    }

    try:
        with YoutubeDL(params) as ydl:
            info = ydl.extract_info(target_url, download=False)
    except DownloadError as exc:
        print(f"::error::YouTube auth smoke test falhou depois dos fallbacks: {exc}")
        return 1

    title = str((info or {}).get("title") or "<sem titulo>")
    video_id = str((info or {}).get("id") or "<sem id>")
    print(f"YOUTUBE_AUTH_TEST_OK id={video_id} title={title}")
    print("Nenhum video foi baixado e nenhuma publicacao foi executada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
