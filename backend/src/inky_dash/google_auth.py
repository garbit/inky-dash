"""One-time Google OAuth setup from the command line. Run on a machine with a
browser:

    uv run python -m inky_dash.google_auth            # calendar
    uv run python -m inky_dash.google_auth photos     # google photos

The config UI can do this itself (Connect Google Photos), which is the easier
route on the Pi; this stays for setup.sh and for minting a token on the dev
machine to copy across.

Prerequisite: create an OAuth client (type "Desktop app") in Google Cloud Console
with the Google Calendar API and/or Photos Picker API enabled, download the
client secret JSON, and save it as data/google_credentials.json. This writes
data/<service>_token.json; copy both files to the Pi's data/ directory.
"""

from __future__ import annotations

import sys

from inky_dash import google, google_oauth

SERVICES = {"calendar": google.CALENDAR, "photos": google.PHOTOS}


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    name = argv[0] if argv else "calendar"
    service = SERVICES.get(name)
    if service is None:
        sys.exit(f"unknown service {name!r}; expected one of {', '.join(SERVICES)}")

    if not google.credentials_path().exists():
        sys.exit(
            f"Missing {google.credentials_path()}.\n"
            "Create an OAuth client (Desktop app) in Google Cloud Console, enable the\n"
            "Google Calendar API / Photos Picker API, download the client secret JSON,\n"
            "and save it there."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = google.SCOPES[service] + google.IDENTITY_SCOPES
    flow = InstalledAppFlow.from_client_secrets_file(str(google.credentials_path()), scopes)
    creds = flow.run_local_server(port=0)
    google.write_token(service, creds, account=google_oauth.email_from(creds))
    print(f"Saved token to {google.token_path(service)}")


if __name__ == "__main__":
    main()
