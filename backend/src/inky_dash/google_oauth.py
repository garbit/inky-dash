"""Browser-driven Google sign-in for the config UI.

Google only accepts an OAuth redirect to HTTPS or to ``localhost``/``127.0.0.1``,
and the Pi serves the UI over plain HTTP at ``http://inky:8000`` — an address
that can never be a registered redirect URI. (The device flow, which would suit a
headless box, supports only Drive, YouTube and profile scopes — not the Photos
Picker.) So the last hop is a copy-paste:

1. The UI opens Google's consent page in a new tab.
2. Google redirects that tab to ``http://localhost:8000/``.
   - On the dev machine the backend *is* localhost:8000, so the frontend loads,
     spots ``?code=`` and completes the flow itself. No paste.
   - On the Pi that address is dead on the user's laptop, but the code sits in
     the address bar; the UI asks them to paste it back.
3. ``complete()`` exchanges the code server-side, which works fine headless.

PKCE means an intercepted code is useless on its own: the verifier never leaves
this process.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from inky_dash import google

log = logging.getLogger(__name__)

# Must match an Authorized redirect URI on the OAuth client. Desktop-app clients
# accept loopback out of the box, so the shared credentials file needs no change.
REDIRECT_URI = "http://localhost:8000/"

PENDING_TTL = 900  # a consent screen left open longer than 15 min can start over
MAX_PENDING = 8


class OAuthError(Exception):
    """Anything the user needs told about: missing client file, bad paste, denial."""


class UnknownFlow(OAuthError):
    """This code belongs to a sign-in some *other* instance started.

    Google always redirects to localhost, so a sign-in begun on the Pi lands on
    whatever answers port 8000 on the user's own machine — often a dev server
    running this same app. It must not swallow the code: the flow's PKCE verifier
    only exists on the instance that started it. Callers turn this into "paste
    this address where you pressed Connect" rather than a dead end.
    """


@dataclass
class _Pending:
    service: str
    flow: Any
    created: float = field(default_factory=time.monotonic)


_pending: dict[str, _Pending] = {}


def _sweep() -> None:
    now = time.monotonic()
    for state in [s for s, p in _pending.items() if now - p.created > PENDING_TTL]:
        del _pending[state]
    while len(_pending) > MAX_PENDING:
        _pending.pop(next(iter(_pending)))


def _flow(service: str, state: str | None = None) -> Any:
    from google_auth_oauthlib.flow import Flow

    if not google.credentials_path().exists():
        raise OAuthError(
            f"Missing {google.credentials_path().name}. Create an OAuth client "
            "(Desktop app) in the Google Cloud Console and save the client secret "
            "JSON there."
        )
    return Flow.from_client_secrets_file(
        str(google.credentials_path()),
        scopes=google.SCOPES[service] + google.IDENTITY_SCOPES,
        redirect_uri=REDIRECT_URI,
        state=state,
        autogenerate_code_verifier=True,
    )


def start(service: str) -> dict[str, str]:
    """Begin a sign-in; returns the consent URL and the state that identifies it."""
    if service not in google.SCOPES:
        raise OAuthError(f"unknown service {service!r}")
    _sweep()
    flow = _flow(service)
    auth_url, state = flow.authorization_url(
        access_type="offline",  # we need a refresh token: the Pi runs unattended
        prompt="consent",  # ...and Google only re-issues one when asked outright
        # Deliberately NOT include_granted_scopes: incremental authorisation
        # returns a token covering everything this OAuth client was ever granted,
        # so connecting Photos on an account that already had Calendar came back
        # as a Calendar token. Each service keeps its own single-scope token.
    )
    _pending[state] = _Pending(service=service, flow=flow)
    return {"auth_url": auth_url, "state": state, "redirect_uri": REDIRECT_URI}


def extract_code(pasted: str) -> str:
    """Pull the authorization code out of whatever the user pasted — the whole
    redirect URL (the normal case) or a bare code."""
    pasted = pasted.strip()
    if not pasted:
        raise OAuthError("Nothing pasted.")
    if "://" not in pasted and "code=" not in pasted:
        return pasted
    query = parse_qs(urlparse(pasted).query if "://" in pasted else pasted)
    if "error" in query:
        raise OAuthError(_explain_refusal(query["error"][0]))
    code = query.get("code", [""])[0]
    if not code:
        raise OAuthError("That address has no authorization code in it.")
    return code


def _explain_refusal(error: str) -> str:
    """Google's redirect carries a bare error code; say what usually causes it.

    ``access_denied`` in particular is ambiguous — it covers both "I pressed
    cancel" and "this app is in Testing and I am not on its test-user list",
    which is the one people actually hit.
    """
    if error == "access_denied":
        return (
            "Google refused the sign-in (access_denied). Either consent was declined, or "
            "the app's publishing status is still “Testing” and this account is not on its "
            "test-user list. In the Cloud Console, under Google Auth platform → Audience, "
            "either publish the app or add the account as a test user."
        )
    if error == "admin_policy_enforced":
        return (
            "A Google Workspace admin policy blocks this app for that account "
            "(admin_policy_enforced). Try a personal account, or have the admin allow it."
        )
    return f"Google refused the request: {error}"


def extract_state(pasted: str) -> str | None:
    """The state from a pasted redirect URL, so a paste can find its own flow even
    if the UI lost track of which sign-in it belongs to."""
    try:
        query = parse_qs(urlparse(pasted.strip()).query)
    except ValueError:
        return None
    return query.get("state", [None])[0]


def complete(state: str, pasted: str) -> dict[str, Any]:
    """Exchange the pasted code for a token and store it."""
    # Validate the paste before hunting for its flow, so "you pasted nothing"
    # never comes back as "this belongs to another device".
    code = extract_code(pasted)
    pending = _pending.get(state) or _pending.get(extract_state(pasted) or "")
    if pending is None:
        raise UnknownFlow(
            "This sign-in was not started here. If you pressed Connect on another device "
            "(the Inky itself, say), copy this whole address and paste it into the Photos "
            "panel there. Otherwise the sign-in expired, or the server restarted — in "
            "which case just start again."
        )
    try:
        _fetch_token(pending.flow, code)
    except Exception as exc:  # oauthlib raises a zoo of subclasses
        raise OAuthError(f"Google rejected the code: {exc}") from None

    creds = pending.flow.credentials
    _check_scopes(pending.service, _granted_scopes(pending.flow, creds))
    email = email_from(creds)
    google.write_token(pending.service, creds, account=email)
    _pending.pop(state, None)
    log.info("connected google service %s", pending.service)
    return {"connected": True, "service": pending.service, "account": email}


def _fetch_token(flow: Any, code: str) -> None:
    """Exchange the code, tolerating a changed scope list at this stage.

    oauthlib aborts the exchange outright if the granted scopes differ from the
    requested ones — including harmless reordering. That turns every scope
    problem into an unreadable library error, so we take the token and judge the
    scopes ourselves in ``_check_scopes``, where we can say something useful.
    """
    import os

    previous = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    try:
        flow.fetch_token(code=code)
    finally:
        if previous is None:
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        else:
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = previous


def _granted_scopes(flow: Any, creds: Any) -> list[str] | None:
    """What Google actually granted, which is not what it was asked for.

    Note ``credentials.scopes`` is the *requested* list copied off the session —
    checking against that would always pass. The granted list only ever comes
    from the token response, exposed as ``granted_scopes`` (and readable from the
    session's token dict as a fallback). None means we could not tell, in which
    case the caller lets it through rather than inventing a failure.
    """
    granted = getattr(creds, "granted_scopes", None)
    if granted:
        return list(granted)
    token = getattr(getattr(flow, "oauth2session", None), "token", None) or {}
    scope = token.get("scope")
    if isinstance(scope, str):
        return scope.split()
    return list(scope) if scope else None


def _check_scopes(service: str, granted: list[str] | None) -> None:
    """Refuse a token that lacks the access the service actually needs.

    Storing it anyway would look like success and then fail later with a bare 403
    from a completely different part of the app. The usual cause is the API not
    being enabled on the Cloud project, so say so.
    """
    required = set(google.SCOPES[service])
    if granted is not None and not required.issubset(set(granted)):
        api = google.API_NAMES.get(service, "the API")
        missing = ", ".join(sorted(required - set(granted)))
        # Name the project: "enable the API" is useless advice if you enable it
        # on a different project from the one this OAuth client belongs to.
        project = google.project_id()
        where = f"project “{project}”" if project else "your project"
        raise OAuthError(
            f"Google did not grant {missing}. Enable the {api} on {where} — the project "
            "this OAuth client belongs to — in the Google Cloud Console (APIs & Services "
            f"→ Library), then connect again. It granted: {', '.join(granted) or 'nothing'}."
        )


def email_from(creds: Any) -> str | None:
    """Best-effort account label from the id_token; not worth failing a
    connection over."""
    try:
        from google.auth.transport import requests
        from google.oauth2 import id_token

        claims = id_token.verify_oauth2_token(
            creds.id_token, requests.Request(), clock_skew_in_seconds=10
        )
        return claims.get("email")
    except Exception:
        log.debug("could not read the account email from the id_token", exc_info=True)
        return None


def _minted_by_another_client(service: str) -> str | None:
    """A message if this token came from a different OAuth client than the one
    currently configured, else None. Both ids missing means we can't tell."""
    current = google.client_config().get("client_id")
    token_client = google.token_client_id(service)
    if not current or not token_client or current == token_client:
        return None
    project = google.project_id()
    return (
        "This sign-in belongs to a different OAuth client than the one now in "
        f"google_credentials.json{f' (project “{project}”)' if project else ''} — "
        "reconnect to sign in again."
    )


def status(service: str) -> dict[str, Any]:
    """What the UI shows next to the Connect button. Never raises."""
    if not google.is_connected(service):
        return {"connected": False}
    stale = _minted_by_another_client(service)
    if stale:
        # Swapping google_credentials.json (e.g. moving to the right Cloud
        # project) leaves every existing token orphaned: they belong to the old
        # client and will fail to refresh. Say so rather than showing "connected"
        # until something 401s.
        return {
            "connected": False,
            "expired": True,
            "account": google.account(service),
            "error": stale,
        }
    try:
        creds = google.credentials(service)
    except Exception as exc:
        # Most often a refresh token that expired — which Google does after 7 days
        # while the OAuth consent screen is still in "Testing" publishing status.
        return {
            "connected": False,
            "expired": True,
            "account": google.account(service),
            "error": str(exc),
        }
    return {
        "connected": bool(creds and creds.valid),
        "account": google.account(service),
    }
