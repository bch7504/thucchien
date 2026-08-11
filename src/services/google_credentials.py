import logging
import os
from datetime import UTC, datetime, timedelta

import jwt
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy import select

from src.auth.crypto import CredentialCryptoError, decrypt_secret, encrypt_secret
from src.config import get_settings
from src.db import session as db_session
from src.db.models import GoogleCalendarCredential

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
_TOKEN_URI = "https://oauth2.googleapis.com/token"
_STATE_PURPOSE = "calendar_oauth"
_STATE_TTL_MINUTES = 10

# oauthlib refuses to exchange a code (or refresh a token) unless every URL involved is HTTPS -
# raises oauthlib.oauth2.rfc6749.errors.InsecureTransportError otherwise. Correct default for
# production, but GOOGLE_CALENDAR_REDIRECT_URI is necessarily http://localhost during local dev
# (Google doesn't accept https://localhost as a redirect URI), so this would always fail the
# token exchange with a generic-looking error unless we opt in to the http exception explicitly -
# scoped to only when the configured redirect really is http://, never in production.
if get_settings().google_calendar_redirect_uri.startswith("http://"):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")


class CalendarNotConnected(Exception):  # noqa: N818 - matches docs/PER_USER_CALENDAR.md's exact name
    """This user hasn't connected Google Calendar, or Google access was revoked on their end.
    Route layer turns this into a 409 (the app's own JWT is still fine - just no Calendar link),
    agent tools turn it into a friendly message instead of a crash."""


class OAuthStateError(Exception):
    """The `state` round-tripped from Google isn't valid - expired, bad signature, or a token of
    the wrong kind (e.g. someone tried to pass a regular app access token as state)."""


# ---------------------------------------------------------------- OAuth flow

def _client_config() -> dict:
    settings = get_settings()
    if not settings.google_calendar_client_id or not settings.google_calendar_client_secret:
        raise RuntimeError("GOOGLE_CALENDAR_CLIENT_ID/SECRET are not set in .env")
    return {
        "web": {
            "client_id": settings.google_calendar_client_id,
            "client_secret": settings.google_calendar_client_secret,
            "auth_uri": _AUTH_URI,
            "token_uri": _TOKEN_URI,
        }
    }


def _build_flow(state: str | None = None) -> Flow:
    settings = get_settings()
    return Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=settings.google_calendar_redirect_uri,
        state=state,
        # google-auth-oauthlib defaults to PKCE (autogenerate_code_verifier=True): it would
        # generate a code_verifier here and send its code_challenge to Google, but that verifier
        # lives only on this in-memory Flow object - build_authorization_url() and exchange_code()
        # each create their OWN Flow in two completely separate requests (URL generation vs. the
        # later /calendar/oauth/callback), so the verifier from the first Flow is gone by the time
        # the second needs it, and Google rejects the exchange with "invalid_grant: Missing code
        # verifier." We're a confidential client (Client Secret, not a public/mobile app) where
        # PKCE isn't required, and `state` (a signed JWT, see make_oauth_state) already carries the
        # user identity + CSRF protection across that gap, so disable it instead of adding a store
        # for a verifier we don't otherwise need.
        autogenerate_code_verifier=False,
    )


def make_oauth_state(user_id: str) -> str:
    """A popup/redirect OAuth round-trip can't carry an Authorization header, so the user's
    identity has to travel in the `state` param and come back intact at the callback. Signed as a
    JWT (not a bare user_id) so no one can forge a state pointing at someone else's account.
    `purpose` stops a regular app access token from being reused here (or vice versa). Short TTL
    since consent only takes a few seconds in practice."""
    settings = get_settings()
    payload = {
        "sub": user_id,
        "purpose": _STATE_PURPOSE,
        "exp": datetime.now(UTC) + timedelta(minutes=_STATE_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def read_oauth_state(state: str) -> str:
    settings = get_settings()
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise OAuthStateError("state is invalid or expired") from exc
    if payload.get("purpose") != _STATE_PURPOSE:
        raise OAuthStateError("state has the wrong purpose")
    return payload["sub"]


def build_authorization_url(user_id: str) -> str:
    """access_type=offline: request a refresh token (without it, access dies after ~1 hour).
    prompt=consent: MANDATORY - Google only returns refresh_token on a user's FIRST consent for
    this Client+scope by default; reconnecting without forcing this would get a response with no
    refresh_token, and save_credentials would silently keep the old (possibly dead) one."""
    flow = _build_flow(state=make_oauth_state(user_id))
    url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    return url


def exchange_code(code: str) -> Credentials:
    """Blocking (HTTP call to Google). Callers in an async route must go through run_in_threadpool."""
    flow = _build_flow()
    flow.fetch_token(code=code)
    return flow.credentials


def fetch_google_email(creds: Credentials) -> str:
    """Best-effort: the connected account's email, for display in the UI. Uses the Calendar API
    itself (calendarList.get("primary").id IS the email address) instead of requesting the extra
    userinfo.email scope - fewer scopes means a lighter consent screen and avoids Google's "Scope
    has changed" warning during token exchange."""
    from googleapiclient.discovery import build

    try:
        service = build("calendar", "v3", credentials=creds)
        return service.calendarList().get(calendarId="primary").execute().get("id", "")
    except Exception:
        logger.warning("Could not fetch the email of the newly-connected calendar", exc_info=True)
        return ""


# ---------------------------------------------------------------- Store / read / remove


async def save_credentials(user_id: str, creds: Credentials, google_email: str = "") -> None:
    async with db_session.async_session_maker() as db:
        row = (
            await db.execute(select(GoogleCalendarCredential).where(GoogleCalendarCredential.user_id == user_id))
        ).scalar_one_or_none()
        if row is None:
            row = GoogleCalendarCredential(user_id=user_id)
            db.add(row)
        if creds.refresh_token:
            # Only overwrite when Google actually returned a refresh_token - see the prompt=consent
            # note above.
            row.refresh_token_enc = encrypt_secret(creds.refresh_token)
            row.sync_token = None  # the connected account may have changed - old cursor is meaningless
        row.access_token_enc = encrypt_secret(creds.token) if creds.token else None
        row.token_expiry = creds.expiry.replace(tzinfo=UTC) if creds.expiry else None
        row.scopes = " ".join(creds.scopes or SCOPES)
        if google_email:
            row.google_email = google_email
        await db.commit()


async def get_connection_info(user_id: str) -> dict:
    async with db_session.async_session_maker() as db:
        row = (
            await db.execute(select(GoogleCalendarCredential).where(GoogleCalendarCredential.user_id == user_id))
        ).scalar_one_or_none()
    if row is None:
        return {"connected": False, "google_email": None, "connected_at": None}
    return {"connected": True, "google_email": row.google_email or None, "connected_at": row.created_at}


async def disconnect(user_id: str) -> None:
    """Revoke on Google's side before deleting the row - deleting alone would leave the app
    dangling in the user's Google Account "Third-party access" list even though it's no longer
    used. A failed revoke must not block removing the local row."""
    import httpx

    async with db_session.async_session_maker() as db:
        row = (
            await db.execute(select(GoogleCalendarCredential).where(GoogleCalendarCredential.user_id == user_id))
        ).scalar_one_or_none()
        if row is None:
            return
        try:
            token = decrypt_secret(row.refresh_token_enc)
            async with httpx.AsyncClient(timeout=10) as http:
                await http.post("https://oauth2.googleapis.com/revoke", data={"token": token})
        except Exception:  # noqa: BLE001 - includes CredentialCryptoError if the key ever changed
            logger.warning("Revoking the Google token failed, deleting the local row anyway", exc_info=True)
        await db.delete(row)
        await db.commit()


async def get_credentials(user_id: str) -> Credentials:
    """Build ready-to-use Credentials for this user, auto-refreshing (and persisting the new
    access token) if expired. Raises CalendarNotConnected if never connected or revoked."""
    settings = get_settings()
    async with db_session.async_session_maker() as db:
        row = (
            await db.execute(select(GoogleCalendarCredential).where(GoogleCalendarCredential.user_id == user_id))
        ).scalar_one_or_none()
        if row is None:
            raise CalendarNotConnected(f"User {user_id} has not connected Google Calendar")
        try:
            refresh_token = decrypt_secret(row.refresh_token_enc)
            access_token = decrypt_secret(row.access_token_enc) if row.access_token_enc else None
        except CredentialCryptoError as exc:
            raise CalendarNotConnected("Stored credential could not be decrypted") from exc
        expiry = row.token_expiry

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=_TOKEN_URI,
        client_id=settings.google_calendar_client_id,
        client_secret=settings.google_calendar_client_secret,
        scopes=(row.scopes or " ".join(SCOPES)).split(),
        # google-auth compares expiry against datetime.utcnow() (naive) - passing a tz-aware
        # datetime here blows up with "can't compare offset-naive and offset-aware datetimes".
        expiry=expiry.astimezone(UTC).replace(tzinfo=None) if expiry else None,
    )

    if not creds.valid:
        try:
            creds.refresh(GoogleRequest())
        except RefreshError as exc:
            # The refresh_token is truly dead: user revoked access in their Google Account,
            # changed their Google password, or the app is in Testing mode past the 7-day limit.
            # Delete the row so the UI shows "connect" again instead of retrying forever.
            logger.info("User %s's refresh token no longer works, dropping the connection", user_id)
            await disconnect(user_id)
            raise CalendarNotConnected("Google Calendar access was revoked") from exc
        await save_credentials(user_id, creds)

    return creds


async def list_connected_user_ids() -> list[str]:
    async with db_session.async_session_maker() as db:
        rows = (await db.execute(select(GoogleCalendarCredential.user_id))).scalars().all()
    return list(rows)


async def get_sync_token(user_id: str) -> str | None:
    async with db_session.async_session_maker() as db:
        row = (
            await db.execute(select(GoogleCalendarCredential).where(GoogleCalendarCredential.user_id == user_id))
        ).scalar_one_or_none()
    return row.sync_token if row else None


async def set_sync_token(user_id: str, sync_token: str | None) -> None:
    async with db_session.async_session_maker() as db:
        row = (
            await db.execute(select(GoogleCalendarCredential).where(GoogleCalendarCredential.user_id == user_id))
        ).scalar_one_or_none()
        if row is not None:
            row.sync_token = sync_token
            await db.commit()
