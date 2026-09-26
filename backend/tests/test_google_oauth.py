import json

import pytest

from inky_dash import google, google_oauth


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("INKY_DASH_DATA", str(tmp_path))
    google_oauth._pending.clear()
    return tmp_path


class FakeCreds:
    """Enough of google.oauth2.credentials.Credentials for the token writer.

    Faithful on the point that matters: `scopes` is what was *requested* (copied
    off the session) and `granted_scopes` is what Google actually gave back.
    Conflating them is how a wrong-scope token would slip through.
    """

    id_token = None

    def __init__(self, granted=None):
        self.scopes = list(google.SCOPES[google.PHOTOS]) + google.IDENTITY_SCOPES
        self.granted_scopes = granted if granted is not None else list(self.scopes)

    def to_json(self):
        return json.dumps(
            {
                "token": "at",
                "refresh_token": "rt",
                "client_id": "cid",
                "client_secret": "cs",
                "scopes": self.scopes,
            }
        )


class FakeFlow:
    def __init__(self, granted=None):
        self.credentials = FakeCreds(granted)
        self.code = None

    def fetch_token(self, code):
        self.code = code


# ------------------------------------------------------ pasted redirect parsing


def test_extract_code_from_a_full_redirect_url():
    url = "http://localhost:8000/?state=xyz&code=4/abc123&scope=email"
    assert google_oauth.extract_code(url) == "4/abc123"
    assert google_oauth.extract_state(url) == "xyz"


def test_extract_code_from_a_bare_code():
    assert google_oauth.extract_code("  4/abc123  ") == "4/abc123"


def test_extract_code_reports_a_denied_consent():
    """access_denied covers both "I cancelled" and the far more common "the app
    is in Testing and I am not a test user" — say both."""
    with pytest.raises(google_oauth.OAuthError) as err:
        google_oauth.extract_code("http://localhost:8000/?error=access_denied")
    assert "test-user" in str(err.value) and "Testing" in str(err.value)


def test_a_workspace_policy_block_is_named():
    with pytest.raises(google_oauth.OAuthError, match="admin policy"):
        google_oauth.extract_code("http://localhost:8000/?error=admin_policy_enforced")


def test_an_unknown_refusal_still_reports_the_code():
    with pytest.raises(google_oauth.OAuthError, match="invalid_scope"):
        google_oauth.extract_code("http://localhost:8000/?error=invalid_scope")


def test_extract_code_rejects_an_address_without_one():
    with pytest.raises(google_oauth.OAuthError):
        google_oauth.extract_code("http://localhost:8000/?state=xyz")
    with pytest.raises(google_oauth.OAuthError):
        google_oauth.extract_code("   ")


# ------------------------------------------------------------------- lifecycle


def test_start_without_a_client_file_explains_itself():
    with pytest.raises(google_oauth.OAuthError, match="google_credentials.json"):
        google_oauth.start(google.PHOTOS)


def test_complete_writes_a_token_and_records_the_account(monkeypatch):
    flow = FakeFlow()
    google_oauth._pending["st"] = google_oauth._Pending(service=google.PHOTOS, flow=flow)
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: "me@example.com")

    result = google_oauth.complete("st", "http://localhost:8000/?state=st&code=4/abc")

    assert result == {"connected": True, "service": google.PHOTOS, "account": "me@example.com"}
    assert flow.code == "4/abc"
    assert google.is_connected(google.PHOTOS)
    assert google.account(google.PHOTOS) == "me@example.com"
    assert "st" not in google_oauth._pending  # single use


def test_complete_finds_the_flow_from_the_pasted_state(monkeypatch):
    """The UI can lose track of which sign-in a paste belongs to; the state in the
    pasted URL is authoritative."""
    flow = FakeFlow()
    google_oauth._pending["st"] = google_oauth._Pending(service=google.PHOTOS, flow=flow)
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: None)

    assert google_oauth.complete("", "http://localhost:8000/?state=st&code=4/abc")["connected"]


def test_a_token_without_the_service_scope_is_refused(monkeypatch):
    """The bug this guards: connecting Photos on an account that already had
    Calendar came back as a Calendar token, which would then 403 much later from
    somewhere unrelated. Refuse it, and name the API to switch on."""
    calendar_instead = google.SCOPES[google.CALENDAR] + google.IDENTITY_SCOPES
    google_oauth._pending["st"] = google_oauth._Pending(
        service=google.PHOTOS, flow=FakeFlow(granted=calendar_instead)
    )
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: None)

    with pytest.raises(google_oauth.OAuthError) as err:
        google_oauth.complete("st", "4/abc")

    assert "Photos Picker API" in str(err.value)
    assert "photospicker" in str(err.value)
    # nothing half-connected is left behind
    assert not google.is_connected(google.PHOTOS)


def test_extra_granted_scopes_are_fine(monkeypatch):
    """Google reorders and sometimes adds scopes; only a *missing* one matters."""
    generous = [
        *google.SCOPES[google.PHOTOS],
        *google.IDENTITY_SCOPES,
        "https://www.googleapis.com/auth/something.else",
    ]
    google_oauth._pending["st"] = google_oauth._Pending(
        service=google.PHOTOS, flow=FakeFlow(granted=generous)
    )
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: None)
    assert google_oauth.complete("st", "4/abc")["connected"] is True


def test_granted_scopes_fall_back_to_the_token_response(monkeypatch):
    """Older google-auth-oauthlib builds don't set granted_scopes; the raw token
    response still carries what Google gave, and it must still be checked."""

    class NoGrantedAttr(FakeCreds):
        def __init__(self):
            super().__init__()
            self.granted_scopes = None  # instance attr, or the base class wins

    class SessionFlow(FakeFlow):
        def __init__(self):
            super().__init__()
            self.credentials = NoGrantedAttr()
            self.oauth2session = type(
                "S", (), {"token": {"scope": " ".join(google.SCOPES[google.CALENDAR])}}
            )()

    google_oauth._pending["st"] = google_oauth._Pending(service=google.PHOTOS, flow=SessionFlow())
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: None)
    with pytest.raises(google_oauth.OAuthError, match="Photos Picker API"):
        google_oauth.complete("st", "4/abc")


def test_an_unknowable_scope_list_is_allowed_through(monkeypatch):
    """If neither source reports scopes we cannot judge — better to connect than
    to invent a failure."""

    class Blind(FakeCreds):
        def __init__(self):
            super().__init__()
            self.granted_scopes = None

    class BlindFlow(FakeFlow):
        def __init__(self):
            super().__init__()
            self.credentials = Blind()

    google_oauth._pending["st"] = google_oauth._Pending(service=google.PHOTOS, flow=BlindFlow())
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: None)
    assert google_oauth.complete("st", "4/abc")["connected"] is True


def test_the_relax_flag_does_not_leak_into_the_process(monkeypatch):
    """It is set only for the exchange: leaving it on would silently relax every
    later token refresh too."""
    import os

    monkeypatch.delenv("OAUTHLIB_RELAX_TOKEN_SCOPE", raising=False)
    google_oauth._pending["st"] = google_oauth._Pending(service=google.PHOTOS, flow=FakeFlow())
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: None)
    google_oauth.complete("st", "4/abc")
    assert "OAUTHLIB_RELAX_TOKEN_SCOPE" not in os.environ


def test_consent_does_not_ask_for_previously_granted_scopes(monkeypatch, tmp_path):
    """include_granted_scopes is what dragged Calendar into the Photos flow."""
    captured = {}

    class RecordingFlow(FakeFlow):
        def authorization_url(self, **kwargs):
            captured.update(kwargs)
            return ("https://accounts.google.com/o/oauth2/auth?x=1", "st")

    monkeypatch.setattr(google_oauth, "_flow", lambda service, state=None: RecordingFlow())
    google_oauth.start(google.PHOTOS)
    assert "include_granted_scopes" not in captured
    assert captured["access_type"] == "offline" and captured["prompt"] == "consent"


def test_complete_without_a_pending_flow():
    """Google always redirects to localhost, so a sign-in started on the Pi lands
    on whatever answers port 8000 on the user's own machine — frequently a dev
    server running this same app. It must not swallow a code it cannot finish
    (the PKCE verifier lives only on the originating instance); it says whose it
    is so the address can be pasted there."""
    with pytest.raises(google_oauth.UnknownFlow) as err:
        google_oauth.complete("gone", "4/abc")
    assert "not started here" in str(err.value)
    # a subclass of OAuthError, so existing handlers still catch it
    assert isinstance(err.value, google_oauth.OAuthError)


def test_expired_pending_flows_are_swept(monkeypatch):
    google_oauth._pending["old"] = google_oauth._Pending(
        service=google.PHOTOS, flow=FakeFlow(), created=-10_000
    )
    google_oauth._sweep()
    assert "old" not in google_oauth._pending


def test_status_of_a_service_that_was_never_connected():
    assert google_oauth.status(google.PHOTOS) == {"connected": False}


def test_status_reports_an_unusable_token_as_expired(monkeypatch):
    google.token_path(google.PHOTOS).write_text(json.dumps({"account": "me@example.com"}))
    status = google_oauth.status(google.PHOTOS)
    assert status["connected"] is False and status["expired"] is True


def test_write_token_keeps_the_account_across_refreshes():
    google.write_token(google.PHOTOS, FakeCreds(), account="me@example.com")
    google.write_token(google.PHOTOS, FakeCreds())  # a refresh, with no account passed
    assert google.account(google.PHOTOS) == "me@example.com"


def test_disconnect_removes_the_token():
    google.write_token(google.PHOTOS, FakeCreds(), account="me@example.com")
    assert google.disconnect(google.PHOTOS) is True
    assert google.is_connected(google.PHOTOS) is False
    assert google.disconnect(google.PHOTOS) is False


def test_calendar_and_photos_keep_separate_tokens():
    assert google.token_path(google.CALENDAR).name == "google_token.json"
    assert google.token_path(google.PHOTOS).name == "google_photos_token.json"


def test_status_flags_a_token_from_a_different_oauth_client(monkeypatch):
    """Swapping google_credentials.json for one from another Cloud project
    orphans every existing token; saying "connected" until something 401s is
    worse than saying so."""
    google.credentials_path().write_text(
        json.dumps({"installed": {"client_id": "new-client", "project_id": "inky-display"}})
    )
    google.token_path(google.PHOTOS).write_text(
        json.dumps({"client_id": "old-client", "account": "me@example.com"})
    )
    status = google_oauth.status(google.PHOTOS)
    assert status["connected"] is False
    assert "different OAuth client" in status["error"]
    assert "inky-display" in status["error"]


def test_status_is_content_when_the_client_matches(monkeypatch):
    google.credentials_path().write_text(json.dumps({"installed": {"client_id": "same"}}))
    google.token_path(google.PHOTOS).write_text(json.dumps({"client_id": "same"}))
    # falls through to the real credential load, which fails on a stub token —
    # the point is that it is not reported as a client mismatch
    assert "different OAuth client" not in str(google_oauth.status(google.PHOTOS).get("error"))


def test_the_project_is_read_from_the_client_file():
    google.credentials_path().write_text(
        json.dumps({"installed": {"client_id": "c", "project_id": "inky-display-503521"}})
    )
    assert google.project_id() == "inky-display-503521"


def test_a_missing_client_file_has_no_project():
    assert google.project_id() is None


def test_scope_error_names_the_project(monkeypatch):
    google.credentials_path().write_text(
        json.dumps({"installed": {"client_id": "c", "project_id": "inky-display-503521"}})
    )
    google_oauth._pending["st"] = google_oauth._Pending(
        service=google.PHOTOS,
        flow=FakeFlow(granted=google.SCOPES[google.CALENDAR]),
    )
    monkeypatch.setattr(google_oauth, "email_from", lambda creds: None)
    with pytest.raises(google_oauth.OAuthError, match="inky-display-503521"):
        google_oauth.complete("st", "4/abc")
