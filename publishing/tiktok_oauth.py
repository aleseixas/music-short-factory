from __future__ import annotations

import hashlib
import json
import secrets
import string
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import requests

from .credentials import CredentialStore


AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
DEFAULT_SCOPES = ("user.info.basic", "video.publish")
CALLBACK_TIMEOUT_SECONDS = 300


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _random_verifier(length: int = 64) -> str:
    alphabet = string.ascii_letters + string.digits + "-._~"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _code_challenge(verifier: str) -> str:
    # TikTok Desktop Login Kit specifically requires HEX-encoded SHA256.
    return hashlib.sha256(verifier.encode("utf-8")).hexdigest()


def _build_authorize_url(
    client_key: str,
    redirect_uri: str,
    state: str,
    verifier: str,
    scopes: tuple[str, ...] = DEFAULT_SCOPES,
) -> str:
    query = urllib.parse.urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": ",".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _code_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


class _CallbackResult:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.code = ""
        self.state = ""
        self.error = ""
        self.error_description = ""


def _handler_factory(expected_path: str, result: _CallbackResult):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != expected_path:
                self.send_response(404)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            result.code = params.get("code", [""])[0]
            result.state = params.get("state", [""])[0]
            result.error = params.get("error", [""])[0]
            result.error_description = params.get("error_description", [""])[0]

            ok = bool(result.code) and not result.error
            body = (
                "<html><body style='font-family:Arial;padding:40px'>"
                "<h2>TikTok autorizado.</h2>"
                "<p>Pode fechar esta aba e voltar ao terminal.</p>"
                "</body></html>"
                if ok
                else
                "<html><body style='font-family:Arial;padding:40px'>"
                "<h2>Nao foi possivel concluir a autorizacao.</h2>"
                "<p>Volte ao terminal para ver o erro.</p>"
                "</body></html>"
            )
            encoded = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            result.event.set()

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return CallbackHandler


def _exchange_code(
    client_key: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> dict[str, Any]:
    try:
        response = requests.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "code": urllib.parse.unquote(code),
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            timeout=60,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Falha de conexao com o OAuth do TikTok: {exc.__class__.__name__}"
        ) from exc

    try:
        payload = response.json()
    except (ValueError, requests.JSONDecodeError) as exc:
        raise RuntimeError(
            f"TikTok retornou HTTP {response.status_code} com JSON invalido."
        ) from exc

    if not response.ok:
        error = str(payload.get("error") or "oauth_error")
        description = str(payload.get("error_description") or "")[:300]
        raise RuntimeError(
            f"TikTok OAuth HTTP {response.status_code}: {error}"
            + (f" - {description}" if description else "")
        )

    if not isinstance(payload, dict):
        raise RuntimeError("Resposta inesperada do OAuth do TikTok.")
    return payload


def main() -> int:
    root = _project_root()
    credentials = CredentialStore.load(root)

    client_key = credentials.get("TIKTOK_CLIENT_KEY")
    client_secret = credentials.get("TIKTOK_CLIENT_SECRET")
    redirect_uri = credentials.get("TIKTOK_REDIRECT_URI")
    missing = [
        name
        for name, value in (
            ("TIKTOK_CLIENT_KEY", client_key),
            ("TIKTOK_CLIENT_SECRET", client_secret),
            ("TIKTOK_REDIRECT_URI", redirect_uri),
        )
        if not value
    ]
    if missing:
        raise SystemExit(
            "Credenciais ausentes no .env: " + ", ".join(missing)
        )

    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme not in {"http", "https"}:
        raise SystemExit("TIKTOK_REDIRECT_URI precisa usar http ou https.")
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Para Desktop, use localhost ou 127.0.0.1 no redirect URI.")
    if parsed.port is None:
        raise SystemExit("TIKTOK_REDIRECT_URI precisa incluir uma porta.")
    if parsed.query or parsed.fragment:
        raise SystemExit("TIKTOK_REDIRECT_URI nao pode conter query string ou fragmento.")

    state = secrets.token_urlsafe(32)
    verifier = _random_verifier(64)
    auth_url = _build_authorize_url(client_key, redirect_uri, state, verifier)

    result = _CallbackResult()
    expected_path = parsed.path or "/"
    server = HTTPServer(
        (parsed.hostname, parsed.port),
        _handler_factory(expected_path, result),
    )
    server.timeout = 1

    print("Abrindo o TikTok no navegador para autorizacao...")
    print("Autorize a conta Target User do Sandbox.")
    opened = webbrowser.open(auth_url, new=1, autoraise=True)
    if not opened:
        print("O navegador nao abriu automaticamente. Abra esta URL manualmente:")
        print(auth_url)

    deadline = __import__("time").monotonic() + CALLBACK_TIMEOUT_SECONDS
    while not result.event.is_set() and __import__("time").monotonic() < deadline:
        server.handle_request()
    server.server_close()

    if not result.event.is_set():
        raise SystemExit("Timeout: nenhum callback do TikTok foi recebido em 5 minutos.")
    if result.error:
        detail = result.error_description[:300]
        raise SystemExit(
            f"TikTok recusou a autorizacao: {result.error}"
            + (f" - {detail}" if detail else "")
        )
    if not secrets.compare_digest(result.state, state):
        raise SystemExit("OAuth interrompido: state do callback nao confere.")
    if not result.code:
        raise SystemExit("OAuth interrompido: callback sem authorization code.")

    payload = _exchange_code(
        client_key,
        client_secret,
        redirect_uri,
        result.code,
        verifier,
    )
    refresh_token = str(payload.get("refresh_token", "")).strip()
    access_token = str(payload.get("access_token", "")).strip()
    if not refresh_token or not access_token:
        raise SystemExit("OAuth concluido, mas o TikTok nao retornou os tokens esperados.")

    # Keep the long-lived refresh token. We intentionally leave ACCESS_TOKEN blank:
    # TikTokPublisher will mint a fresh access token from the refresh token on each run,
    # avoiding a stale 24-hour access token in .env.
    credentials.persist_env_values(
        {
            "TIKTOK_REFRESH_TOKEN": refresh_token,
            "TIKTOK_ACCESS_TOKEN": "",
        }
    )

    scopes = str(payload.get("scope", "")).strip() or "(nao informado)"
    expires_in = payload.get("expires_in")
    refresh_expires_in = payload.get("refresh_expires_in")
    print("OAuth do TikTok concluido com sucesso.")
    print(f"Scopes concedidos: {scopes}")
    if expires_in is not None:
        print(f"Access token: valido por {expires_in} segundos (nao exibido).")
    if refresh_expires_in is not None:
        print(
            f"Refresh token: valido por {refresh_expires_in} segundos e salvo no .env (nao exibido)."
        )
    print("Agora voce pode executar o publish.py para TikTok em modo live.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
