import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow


load_dotenv()

CLIENT_ID = os.getenv("YOUTUBE_CLIENT_ID")
CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError(
        "YOUTUBE_CLIENT_ID ou YOUTUBE_CLIENT_SECRET não encontrados no .env"
    )

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload"
]

client_config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [
            "http://localhost"
        ]
    }
}

flow = InstalledAppFlow.from_client_config(
    client_config,
    SCOPES
)

credentials = flow.run_local_server(
    port=0,
    access_type="offline",
    prompt="consent"
)

print("\nAutorização concluída.\n")

print("YOUTUBE_REFRESH_TOKEN=")
print(credentials.refresh_token)