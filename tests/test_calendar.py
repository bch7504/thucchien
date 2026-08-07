from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from googleapiclient.errors import HttpError

import src.db.session as db_session
from src.db.models import CalendarSyncState
from src.services import calendar_service


@pytest.mark.asyncio
async def test_list_events_requires_auth(client):
    resp = await client.get("/api/v1/calendar/events")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_list_events_maps_google_events(client, auth_headers, monkeypatch):
    monkeypatch.setattr(
        calendar_service,
        "list_events",
        lambda time_min, time_max, max_results=50: [
            {
                "id": "evt-1",
                "summary": "Team sync",
                "start": {"dateTime": "2026-08-10T10:00:00+07:00"},
                "end": {"dateTime": "2026-08-10T10:30:00+07:00"},
                "htmlLink": "https://calendar.google.com/event?eid=evt-1",
            }
        ],
    )

    resp = await client.get("/api/v1/calendar/events", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "evt-1"
    assert body[0]["title"] == "Team sync"
    assert body[0]["start"] == "2026-08-10T10:00:00+07:00"
    assert body[0]["url"] == "https://calendar.google.com/event?eid=evt-1"


@pytest.mark.asyncio
async def test_list_events_upstream_error_returns_502(client, auth_headers, monkeypatch):
    def _boom(time_min, time_max, max_results=50):
        raise RuntimeError("token expired")

    monkeypatch.setattr(calendar_service, "list_events", _boom)

    resp = await client.get("/api/v1/calendar/events", headers=auth_headers)
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_create_event(client, auth_headers, monkeypatch):
    captured = {}

    def _fake_create(summary, start_iso, end_iso, description="", attendees=None):
        captured.update(
            summary=summary, start_iso=start_iso, end_iso=end_iso, description=description, attendees=attendees
        )
        return {
            "id": "evt-2",
            "summary": summary,
            "start": {"dateTime": start_iso},
            "end": {"dateTime": end_iso},
            "htmlLink": "https://calendar.google.com/event?eid=evt-2",
        }

    monkeypatch.setattr(calendar_service, "create_event", _fake_create)

    resp = await client.post(
        "/api/v1/calendar/events",
        json={"summary": "Design sync", "start_iso": "2026-08-11T09:00:00+07:00", "end_iso": "2026-08-11T09:30:00+07:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "evt-2"
    assert body["title"] == "Design sync"
    assert captured["summary"] == "Design sync"


@pytest.mark.asyncio
async def test_poll_calendar_changes_bootstrap_broadcasts_and_stores_token(client, monkeypatch):
    """No stored sync token yet: should do a bootstrap listing, broadcast whatever it finds, and
    save the returned nextSyncToken for the next (incremental) poll."""
    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "evt-1",
                "summary": "Team sync",
                "start": {"dateTime": "2026-08-10T10:00:00+07:00"},
                "end": {"dateTime": "2026-08-10T10:30:00+07:00"},
            }
        ],
        "nextSyncToken": "token-123",
    }
    monkeypatch.setattr(calendar_service, "get_calendar_service", lambda: fake_service)
    broadcast = AsyncMock()
    monkeypatch.setattr(calendar_service, "broadcast_change", broadcast)

    await calendar_service.poll_calendar_changes()

    call_kwargs = fake_service.events.return_value.list.call_args.kwargs
    assert "syncToken" not in call_kwargs
    assert "timeMin" in call_kwargs
    broadcast.assert_awaited_once_with("calendar_event_updated", {"event": {
        "id": "evt-1", "title": "Team sync",
        "start": "2026-08-10T10:00:00+07:00", "end": "2026-08-10T10:30:00+07:00", "url": None,
    }})

    async with db_session.async_session_maker() as db:
        state = await db.get(CalendarSyncState, "default")
        assert state.sync_token == "token-123"


@pytest.mark.asyncio
async def test_poll_calendar_changes_incremental_handles_deletion(client, monkeypatch):
    """With a stored sync token, should request an incremental diff and treat a cancelled item
    as a deletion rather than trying to render it as an event."""
    async with db_session.async_session_maker() as db:
        db.add(CalendarSyncState(id="default", sync_token="old-token"))
        await db.commit()

    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "evt-2", "status": "cancelled"}],
        "nextSyncToken": "new-token",
    }
    monkeypatch.setattr(calendar_service, "get_calendar_service", lambda: fake_service)
    broadcast = AsyncMock()
    monkeypatch.setattr(calendar_service, "broadcast_change", broadcast)

    await calendar_service.poll_calendar_changes()

    call_kwargs = fake_service.events.return_value.list.call_args.kwargs
    assert call_kwargs["syncToken"] == "old-token"
    broadcast.assert_awaited_once_with("calendar_event_deleted", {"event_id": "evt-2"})

    async with db_session.async_session_maker() as db:
        state = await db.get(CalendarSyncState, "default")
        assert state.sync_token == "new-token"


@pytest.mark.asyncio
async def test_poll_calendar_changes_expired_token_falls_back_to_full_resync(client, monkeypatch):
    """A 410 from Google means the stored token is no longer valid - the poller must drop it and
    resync from scratch instead of erroring out forever on every future poll."""
    async with db_session.async_session_maker() as db:
        db.add(CalendarSyncState(id="default", sync_token="stale-token"))
        await db.commit()

    calls = []

    def _list(**kwargs):
        calls.append(kwargs)
        if "syncToken" in kwargs:
            raise HttpError(
                SimpleNamespace(status=410, reason="Gone"), b'{"error": {"message": "Sync token is no longer valid"}}'
            )
        execute_mock = MagicMock()
        execute_mock.execute.return_value = {"items": [], "nextSyncToken": "fresh-token"}
        return execute_mock

    fake_service = MagicMock()
    fake_service.events.return_value.list.side_effect = _list
    monkeypatch.setattr(calendar_service, "get_calendar_service", lambda: fake_service)
    monkeypatch.setattr(calendar_service, "broadcast_change", AsyncMock())

    await calendar_service.poll_calendar_changes()

    assert len(calls) == 2
    assert "syncToken" in calls[0]
    assert "syncToken" not in calls[1]

    async with db_session.async_session_maker() as db:
        state = await db.get(CalendarSyncState, "default")
        assert state.sync_token == "fresh-token"
