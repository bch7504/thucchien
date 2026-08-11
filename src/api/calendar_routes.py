import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from starlette.concurrency import run_in_threadpool

from src.auth.dependencies import get_current_user
from src.config import get_settings
from src.db.models import User
from src.models.calendar_schemas import (
    CalendarConnectionStatusOut,
    CalendarEventCreateRequest,
    CalendarEventOut,
    CalendarEventUpdateRequest,
)
from src.services import calendar_service, google_credentials
from src.services.google_credentials import CalendarNotConnected

logger = logging.getLogger(__name__)

# Main router: every route needs the app's own login (unchanged from before).
router = APIRouter(dependencies=[Depends(get_current_user)])

# Public router: only Google's OAuth callback. The user's identity travels through the signed
# `state` param, not a Bearer token - Google redirects the browser here directly, with no
# Authorization header to check.
public_router = APIRouter()


def _to_out(event: dict) -> CalendarEventOut:
    return CalendarEventOut(**calendar_service.to_out_dict(event))


def _not_connected() -> HTTPException:
    # 409, not 401/403: the app's own JWT is still valid, the user just hasn't linked Google yet.
    # The frontend keys off this status to show the "Connect Google Calendar" card.
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "calendar_not_connected", "message": "Google Calendar not connected"},
    )


@router.get("/calendar/connection", response_model=CalendarConnectionStatusOut)
async def get_calendar_connection(current_user: User = Depends(get_current_user)) -> CalendarConnectionStatusOut:
    info = await google_credentials.get_connection_info(current_user.id)
    return CalendarConnectionStatusOut(**info)


@router.get("/calendar/oauth/url")
async def calendar_oauth_url(current_user: User = Depends(get_current_user)) -> dict:
    try:
        return {"url": google_credentials.build_authorization_url(current_user.id)}
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from None


@router.delete("/calendar/connection", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_calendar(current_user: User = Depends(get_current_user)) -> None:
    await google_credentials.disconnect(current_user.id)


_CALLBACK_HTML = """<!doctype html><meta charset="utf-8"><title>Google Calendar</title>
<body style="font-family:system-ui;padding:2rem;text-align:center">
<p>{message}</p>
<script>
  if (window.opener) window.opener.postMessage({{type:"calendar_oauth",ok:{ok}}}, "{origin}");
  setTimeout(function(){{ window.close(); }}, 800);
</script></body>"""


@public_router.get("/calendar/oauth/callback", response_class=HTMLResponse)
async def calendar_oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> HTMLResponse:
    settings = get_settings()

    def page(ok: bool, message: str, status_code: int = 200) -> HTMLResponse:
        html = _CALLBACK_HTML.format(message=message, ok="true" if ok else "false", origin=settings.frontend_origin)
        return HTMLResponse(html, status_code=status_code)

    if error or not code or not state:
        return page(False, f"Connection failed: {error or 'missing parameters'}.", 400)

    try:
        user_id = google_credentials.read_oauth_state(state)
    except google_credentials.OAuthStateError:
        return page(False, "This connection attempt has expired. Please try again.", 400)

    try:
        # fetch_token / the discovery build below are both blocking HTTP calls - run in a
        # threadpool so they don't block the event loop, same reason auth_routes.google_auth
        # already does this for verify_google_id_token.
        creds = await run_in_threadpool(google_credentials.exchange_code, code)
        email = await run_in_threadpool(google_credentials.fetch_google_email, creds)
    except Exception:
        logger.exception("Failed to exchange the authorization code")
        return page(False, "Could not exchange the authorization code with Google.", 502)

    if not creds.refresh_token:
        return page(False, "Google did not return a refresh token. Please try connecting again.", 400)

    await google_credentials.save_credentials(user_id, creds, google_email=email)
    return page(True, "Google Calendar connected. You can close this window.")


@router.get("/calendar/events", response_model=list[CalendarEventOut])
async def list_events(
    current_user: User = Depends(get_current_user),
    time_min: str | None = Query(default=None),
    time_max: str | None = Query(default=None),
) -> list[CalendarEventOut]:
    now = datetime.now(UTC)
    time_min = time_min or now.isoformat()
    time_max = time_max or (now + timedelta(days=60)).isoformat()
    try:
        items = await calendar_service.list_events(current_user.id, time_min, time_max, max_results=100)
    except CalendarNotConnected:
        raise _not_connected() from None
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}") from None
    return [_to_out(e) for e in items]


@router.post("/calendar/events", response_model=CalendarEventOut, status_code=status.HTTP_201_CREATED)
async def create_event(
    request: CalendarEventCreateRequest, current_user: User = Depends(get_current_user)
) -> CalendarEventOut:
    try:
        created = await calendar_service.create_event(
            current_user.id,
            summary=request.summary,
            start_iso=request.start_iso,
            end_iso=request.end_iso,
            description=request.description,
            attendees=request.attendees,
            timezone=current_user.timezone,
        )
    except CalendarNotConnected:
        raise _not_connected() from None
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}") from None
    out = _to_out(created)
    await calendar_service.broadcast_change(current_user.id, "calendar_event_created", {"event": out.model_dump()})
    return out


@router.patch("/calendar/events/{event_id}", response_model=CalendarEventOut)
async def update_event(
    event_id: str, request: CalendarEventUpdateRequest, current_user: User = Depends(get_current_user)
) -> CalendarEventOut:
    try:
        updated = await calendar_service.update_event(
            current_user.id,
            event_id=event_id,
            summary=request.summary,
            start_iso=request.start_iso,
            end_iso=request.end_iso,
            description=request.description,
            timezone=current_user.timezone,
        )
    except CalendarNotConnected:
        raise _not_connected() from None
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}") from None
    out = _to_out(updated)
    await calendar_service.broadcast_change(current_user.id, "calendar_event_updated", {"event": out.model_dump()})
    return out


@router.delete("/calendar/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: str, current_user: User = Depends(get_current_user)) -> None:
    try:
        await calendar_service.delete_event(current_user.id, event_id)
    except CalendarNotConnected:
        raise _not_connected() from None
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}") from None
    await calendar_service.broadcast_change(current_user.id, "calendar_event_deleted", {"event_id": event_id})
