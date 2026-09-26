"""Shared Google credential handling for the modules that need an account.

One OAuth client (``data/google_credentials.json``, created once in the Google
Cloud Console) serves every service, but each service keeps **its own token
file**: widening an existing token's scope list would invalidate it, so adding
Photos must never disturb a working Calendar setup.

Every google import in here is function-local, matching the rest of the codebase
— the heavy client libraries are only paid for when an account is actually used.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from inky_dash.config import data_dir

log = logging.getLogger(__name__)

CALENDAR = "google"  # keeps the historical data/google_token.json path
PHOTOS = "google_photos"
DRIVE = "google_drive"

SCOPES: dict[str, list[str]] = {
    CALENDAR: ["https://www.googleapis.com/auth/calendar.readonly"],
    # The Library API's broad read scopes were withdrawn on 31 March 2025; the
    # Picker API is the only sanctioned way to reach a user's own photos now.
    PHOTOS: ["https://www.googleapis.com/auth/photospicker.mediaitems.readonly"],
    # Deliberately the broad read scope: drive.file grants no access to the
    # contents of a folder the user selects, so it cannot watch one. Kept in its
    # own token file so it can be revoked on its own. See google_drive.py.
    DRIVE: ["https://www.googleapis.com/auth/drive.readonly"],
}

# Requested alongside the service scope so the UI can name the connected account.
IDENTITY_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]

# What to tell the user to switch on when Google declines to grant a scope —
# almost always because the API is not enabled on their Cloud project.
API_NAMES = {
    CALENDAR: "Google Calendar API",
    PHOTOS: "Photos Picker API",
    DRIVE: "Google Drive API",
}


def credentials_path() -> Path:
    return data_dir() / "google_credentials.json"


def client_config() -> dict[str, Any]:
    """The OAuth client's own details. Which Cloud project the consent screen —
    and therefore which project's enabled APIs — comes from is decided entirely
    by this file, so it is worth being able to see it."""
    import json

    try:
        data = json.loads(credentials_path().read_text())
    except (OSError, ValueError):
        return {}
    return data.get("installed") or data.get("web") or {}


def project_id() -> str | None:
    return client_config().get("project_id")


def token_path(service: str = CALENDAR) -> Path:
    return data_dir() / f"{service}_token.json"


def is_connected(service: str) -> bool:
    return token_path(service).exists()


def disconnect(service: str) -> bool:
    path = token_path(service)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def account(service: str) -> str | None:
    """The email the token was minted for, if the identity scopes were granted."""
    return _read_token(service).get("account") or None


def token_client_id(service: str) -> str | None:
    """Which OAuth client issued this token — tokens do not survive swapping the
    client file for one from another project."""
    return _read_token(service).get("client_id") or None


def _read_token(service: str) -> dict[str, Any]:
    import json

    try:
        data = json.loads(token_path(service).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_token(service: str, creds: Any, *, account: str | None = None) -> None:
    """Persist a token, keeping the account label across refreshes — ``to_json()``
    only knows about the OAuth fields."""
    import json

    data = json.loads(creds.to_json())
    data["account"] = account or _read_token(service).get("account")
    token_path(service).write_text(json.dumps(data, indent=2))


def credentials(service: str) -> Any:
    """Load a service's token, refreshing (and rewriting) it when it has expired.

    Raises if the token is missing or refresh fails — callers that must degrade
    gracefully check ``is_connected()`` first, as the calendar module does.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_info(_read_token(service))
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        write_token(service, creds)
    return creds


def session(service: str) -> Any:
    """An authorised requests session, for APIs with no discovery document (the
    Photos Picker). Blocking — call it from a thread."""
    from google.auth.transport.requests import AuthorizedSession

    return AuthorizedSession(credentials(service))
