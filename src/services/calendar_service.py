import logging
from datetime import UTC, datetime, timedelta

from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from starlette.concurrency import run_in_threadpool

from src.config import get_settings
from src.services import google_credentials
from src.websocket.manager import manager

logger = logging.getLogger(__name__)

# calendarId is always "primary": the credential used to build the service is already this
# specific user's own, so "primary" naturally resolves to their own main calendar. No per-user
# calendar_id needs storing anywhere.
_PRIMARY = "primary"


async def _service(user_id: str) -> Resource:
    creds = await google_credentials.get_credentials(user_id)
    return await run_in_threadpool(build, "calendar", "v3", credentials=creds)


async def list_events(user_id: str, time_min_iso: str, time_max_iso: str, max_results: int = 50) -> list[dict]:
    service = await _service(user_id)

    def _call():
        return (
            service.events()
            .list(
                calendarId=_PRIMARY,
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

    resp = await run_in_threadpool(_call)
    return resp.get("items", [])


async def create_event(
    user_id: str,
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    attendees: list[str] | None = None,
    timezone: str | None = None,
) -> dict:
    tz = timezone or get_settings().calendar_timezone
    service = await _service(user_id)
    body = {
        "summary": summary,
        "description": description,
        "start": {"dateTime": start_iso, "timeZone": tz},
        "end": {"dateTime": end_iso, "timeZone": tz},
        "attendees": [{"email": a} for a in (attendees or [])],
    }

    def _call():
        return service.events().insert(calendarId=_PRIMARY, body=body).execute()

    return await run_in_threadpool(_call)


async def update_event(
    user_id: str,
    event_id: str,
    summary: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    description: str | None = None,
    timezone: str | None = None,
) -> dict:
    """Patch an existing Google Calendar event - only the given fields change."""
    tz = timezone or get_settings().calendar_timezone
    service = await _service(user_id)
    body: dict = {}
    if summary is not None:
        body["summary"] = summary
    if description is not None:
        body["description"] = description
    if start_iso is not None:
        body["start"] = {"dateTime": start_iso, "timeZone": tz}
    if end_iso is not None:
        body["end"] = {"dateTime": end_iso, "timeZone": tz}

    def _call():
        return service.events().patch(calendarId=_PRIMARY, eventId=event_id, body=body).execute()

    return await run_in_threadpool(_call)


async def delete_event(user_id: str, event_id: str) -> None:
    service = await _service(user_id)

    def _call():
        service.events().delete(calendarId=_PRIMARY, eventId=event_id).execute()

    await run_in_threadpool(_call)


async def broadcast_change(user_id: str, event_type: str, payload: dict) -> None:
    """Push a calendar change to the owning user only. Each user now connects their own Google
    Calendar, so broadcasting any wider than that would leak one person's event titles to
    everyone else online - this used to fan out to every connected user back when Calendar was
    one shared account for the whole app."""
    await manager.broadcast_to_users([user_id], {"type": event_type, **payload})


def to_out_dict(event: dict) -> dict:
    """Shared shape for a Google Calendar event, used by both the REST response (CalendarEventOut)
    and the WebSocket push (REST route + agent tool both need to notify the same way)."""
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event["id"],
        "title": event.get("summary", "(No title)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "url": event.get("htmlLink"),
    }


async def _fetch_changes(user_id: str, sync_token: str | None) -> tuple[list[dict], str | None]:
    """One full page-through of events().list() for this user's calendar. With no sync_token,
    this is a bootstrap sync (events from the last day onward) that just establishes a fresh
    nextSyncToken; with one, it's an incremental diff - Google returns only what changed since
    that token, deletions included (as items with status="cancelled")."""
    service = await _service(user_id)
    kwargs: dict = {"calendarId": _PRIMARY, "singleEvents": True}
    if sync_token:
        kwargs["syncToken"] = sync_token
    else:
        kwargs["timeMin"] = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    items: list[dict] = []
    next_sync_token = None
    page_token = None
    while True:
        resp = await run_in_threadpool(lambda: service.events().list(**kwargs, pageToken=page_token).execute())
        items.extend(resp.get("items", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            next_sync_token = resp.get("nextSyncToken")
            break
    return items, next_sync_token


async def _poll_one_user(user_id: str) -> None:
    """One connected user's incremental sync + broadcast, isolated so a failure here (expired/
    revoked credential, transient Google error, ...) never aborts another user's poll in the same
    tick - see poll_calendar_changes."""
    sync_token = await google_credentials.get_sync_token(user_id)
    try:
        items, next_sync_token = await _fetch_changes(user_id, sync_token)
    except google_credentials.CalendarNotConnected:
        return  # revoked mid-poll - get_credentials already cleaned up the row
    except HttpError as e:
        if sync_token and e.resp.status == 410:
            # Token expired/invalid (e.g. calendar untouched too long) - Google requires starting
            # over with a full sync rather than resuming.
            logger.warning("Calendar sync token expired for user %s, resyncing from scratch", user_id)
            try:
                items, next_sync_token = await _fetch_changes(user_id, None)
            except Exception:
                logger.exception("Calendar poll full resync failed for user %s", user_id)
                return
        else:
            logger.exception("Calendar poll failed for user %s", user_id)
            return
    except Exception:
        logger.exception("Calendar poll failed for user %s", user_id)
        return

    for event in items:
        if event.get("status") == "cancelled":
            await broadcast_change(user_id, "calendar_event_deleted", {"event_id": event["id"]})
        else:
            await broadcast_change(user_id, "calendar_event_updated", {"event": to_out_dict(event)})

    await google_credentials.set_sync_token(user_id, next_sync_token)


async def poll_calendar_changes() -> None:
    """Periodic APScheduler job (one job for the whole app, id "calendar_poll", unchanged): Google
    Calendar push notifications need a public HTTPS callback URL, which this project doesn't have
    (local dev only, no deployment yet), so this polls with an incremental syncToken instead to
    catch changes made directly in Google Calendar (outside the app).

    Only polls users who are currently online (connected over WebSocket): a 20-second job times N
    connected users is N*4320 Google API requests/day, and an offline user's calendar changes don't
    need realtime delivery - their frontend just re-fetches /calendar/events next time they open
    the page. Never raises: a failed poll must not crash the scheduler, just retry next interval,
    and one user's failure must not block any other user's poll in the same tick."""
    connected = set(await google_credentials.list_connected_user_ids())
    online = [uid for uid in list(manager.active.keys()) if uid in connected]
    for user_id in online:
        try:
            await _poll_one_user(user_id)
        except Exception:
            logger.exception("Calendar poll failed for user %s", user_id)
