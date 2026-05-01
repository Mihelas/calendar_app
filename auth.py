"""Google OAuth helpers for the Streamlit app.

Uses the OAuth 2.0 Web application flow. The user is redirected to Google,
returns to the app with a `?code=...` query parameter, and we exchange that
code for credentials. Credentials live in `st.session_state` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.readonly",
]


@dataclass
class UserSession:
    email: str
    credentials: Credentials


def _client_config() -> dict:
    """Build the OAuth client config dict expected by `Flow.from_client_config`."""
    oauth = st.secrets["google_oauth"]
    redirect_uri = st.secrets["redirect_uri"]
    return {
        "web": {
            "client_id": oauth["client_id"],
            "client_secret": oauth["client_secret"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _build_flow() -> Flow:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES)
    flow.redirect_uri = st.secrets["redirect_uri"]
    # Disable PKCE. We use a confidential Web client (client_secret kept on the
    # server) so PKCE is optional. Streamlit's session state is not reliably
    # preserved across the OAuth redirect, which would otherwise lose the
    # generated `code_verifier` between steps and cause `invalid_grant: Missing
    # code verifier` at token exchange time.
    flow.autogenerate_code_verifier = False
    flow.code_verifier = None
    return flow


def _is_email_allowed(email: str) -> bool:
    allowed = st.secrets.get("allowed_emails", [])
    if not allowed:
        return True
    return email.lower() in {e.lower() for e in allowed}


def _fetch_email(credentials: Credentials) -> str:
    """Use the OAuth2 userinfo endpoint to get the signed-in user's email."""
    service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    info = service.userinfo().get().execute()
    return info.get("email", "")


def get_login_url() -> str:
    """Return the Google authorization URL the user should be redirected to."""
    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return auth_url


def handle_oauth_callback(code: str) -> Optional[UserSession]:
    """Exchange the authorization `code` for credentials and verify allowlist.

    Returns a `UserSession` on success, or raises `PermissionError` if the
    signed-in email is not in the allowlist.
    """
    flow = _build_flow()
    flow.fetch_token(code=code)
    credentials = flow.credentials

    email = _fetch_email(credentials)
    if not email:
        raise RuntimeError("Could not read email from Google account.")

    if not _is_email_allowed(email):
        raise PermissionError(
            f"The Google account '{email}' is not authorized for this app."
        )

    return UserSession(email=email, credentials=credentials)


def get_current_session() -> Optional[UserSession]:
    """Return the active `UserSession` if logged in, else `None`.

    Refreshes the access token transparently if it expired.
    """
    session: Optional[UserSession] = st.session_state.get("user_session")
    if session is None:
        return None

    creds = session.credentials
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        st.session_state["user_session"] = UserSession(
            email=session.email, credentials=creds
        )
    return st.session_state["user_session"]


def logout() -> None:
    st.session_state.pop("user_session", None)
