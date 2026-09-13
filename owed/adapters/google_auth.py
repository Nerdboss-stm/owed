"""One OAuth desktop flow shared by Gmail and Calendar. Token cached at GOOGLE_TOKEN_JSON."""
from __future__ import annotations
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from owed.config import ROOT, env

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

_creds: Credentials | None = None


def credentials() -> Credentials:
    global _creds
    if _creds and _creds.valid:
        return _creds
    token_path = ROOT / env("GOOGLE_TOKEN_JSON", "./token.json")
    creds_path = ROOT / env("GOOGLE_CREDENTIALS_JSON", "./credentials.json")
    creds: Credentials | None = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
        creds = flow.run_local_server(port=0)
        Path(token_path).write_text(creds.to_json())
    _creds = creds
    return creds


def gmail():
    return build("gmail", "v1", credentials=credentials(), cache_discovery=False)


def calendar():
    return build("calendar", "v3", credentials=credentials(), cache_discovery=False)
