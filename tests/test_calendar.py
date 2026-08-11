from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError
from sqlalchemy import select

import src.db.session as db_session
from src.auth.crypto import decrypt_secret
from src.auth.security import create_access_token
from src.db.models import GoogleCalendarCredential
from src.services import calendar_service, google_credentials
from src.websocket.manager import manager


async def _user_id(client, headers):
    return (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]


async def _seed_credential(user_id: str, refresh_token: str = "rt-test") -> None:
    async with db_session.async_session_maker() as db:
        db.add(GoogleCalendarCredential(user_id=user_id, refresh_token_enc=google_credentials.encrypt_secret(refresh_token)))
        await db.commit()


async def _get_credential(user_id: str) -> GoogleCalendarCredential | None:
    # NOTE: GoogleCalendarCredential's primary key is its own `id` (auto-generated), not
    # `user_id` (which is only unique+indexed) - db.get(Model, user_id) would silently look up
    # the wrong column and always return None. Must go through a real query instead.
    async with db_session.async_session_maker() as db:
        return (
            await db.execute(select(GoogleCalendarCredential).where(GoogleCalendarCredential.user_id == user_id))
        ).scalar_one_or_none()


def _online(user_id: str):
    """Context manager-ish helper: mark a user "online" in the WebSocket manager for the duration
    of a `with` block, since poll_calendar_changes now only polls users who are online."""

    class _Ctx:
        def __enter__(self):
            manager.active.setdefault(user_id, set()).add(object())
            return self

        def __exit__(self, *exc):
            manager.active.pop(user_id, None)

    return _Ctx()


@pytest.mark.asyncio
async def test_list_events_requires_auth(client):
    resp = await client.get("/api/v1/calendar/events")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_events_returns_409_when_not_connected(client, auth_headers):
    """No GoogleCalendarCredential row for this user - real (unmocked) credential-resolution path."""
    resp = await client.get("/api/v1/calendar/events", headers=auth_headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "calendar_not_connected"


@pytest.mark.asyncio
async def test_list_events_maps_google_events(client, auth_headers, monkeypatch):
    async def _fake_service(user_id):
        assert user_id  # route must actually pass the caller's id down
        fake = MagicMock()
        fake.events.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "id": "evt-1",
                    "summary": "Team sync",
                    "start": {"dateTime": "2026-08-10T10:00:00+07:00"},
                    "end": {"dateTime": "2026-08-10T10:30:00+07:00"},
                    "htmlLink": "https://calendar.google.com/event?eid=evt-1",
                }
            ]
        }
        return fake

    monkeypatch.setattr(calendar_service, "_service", _fake_service)

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
    async def _boom(user_id):
        raise RuntimeError("token expired")

    monkeypatch.setattr(calendar_service, "_service", _boom)

    resp = await client.get("/api/v1/calendar/events", headers=auth_headers)
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_create_event(client, auth_headers, monkeypatch):
    captured = {}
    fake_service = MagicMock()

    def _insert(calendarId=None, body=None):  # noqa: N803 - matches googleapiclient's own kwarg name
        captured.update(calendarId=calendarId, body=body)
        return MagicMock(
            execute=MagicMock(
                return_value={
                    "id": "evt-2",
                    "summary": body["summary"],
                    "start": body["start"],
                    "end": body["end"],
                    "htmlLink": "https://calendar.google.com/event?eid=evt-2",
                }
            )
        )

    fake_service.events.return_value.insert.side_effect = _insert

    async def _fake_service(user_id):
        return fake_service

    monkeypatch.setattr(calendar_service, "_service", _fake_service)

    resp = await client.post(
        "/api/v1/calendar/events",
        json={"summary": "Design sync", "start_iso": "2026-08-11T09:00:00+07:00", "end_iso": "2026-08-11T09:30:00+07:00"},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "evt-2"
    assert body["title"] == "Design sync"
    assert captured["body"]["summary"] == "Design sync"
    assert captured["calendarId"] == "primary"


# ---------------------------------------------------------------------------
# Connection management (OAuth redirect + callback flow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_calendar_connection_not_connected(client, auth_headers):
    resp = await client.get("/api/v1/calendar/connection", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == {"connected": False, "google_email": None, "connected_at": None}


@pytest.mark.asyncio
async def test_oauth_url_requires_auth(client):
    resp = await client.get("/api/v1/calendar/oauth/url")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_callback_rejects_bad_state(client):
    resp = await client.get("/api/v1/calendar/oauth/callback", params={"code": "x", "state": "not-a-jwt"})
    assert resp.status_code == 400
    async with db_session.async_session_maker() as db:
        assert (await db.execute(select(GoogleCalendarCredential))).scalars().all() == []


@pytest.mark.asyncio
async def test_callback_rejects_app_access_token_as_state(client, auth_headers):
    """A regular app JWT (from login) must not work as OAuth `state` - it lacks the
    purpose=calendar_oauth claim, so someone can't hijack it to write to another user's row."""
    user_id = await _user_id(client, auth_headers)
    app_token = create_access_token(user_id)

    resp = await client.get("/api/v1/calendar/oauth/callback", params={"code": "x", "state": app_token})
    assert resp.status_code == 400
    assert await _get_credential(user_id) is None


@pytest.mark.asyncio
async def test_callback_missing_params_fails(client):
    resp = await client.get("/api/v1/calendar/oauth/callback", params={"error": "access_denied"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_disconnect_calendar_removes_credential(client, auth_headers, monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient.post", AsyncMock())  # don't actually call Google's revoke endpoint
    user_id = await _user_id(client, auth_headers)
    await _seed_credential(user_id)

    resp = await client.delete("/api/v1/calendar/connection", headers=auth_headers)
    assert resp.status_code == 204
    assert await _get_credential(user_id) is None

    # Idempotent - disconnecting again with nothing to remove is not an error.
    resp2 = await client.delete("/api/v1/calendar/connection", headers=auth_headers)
    assert resp2.status_code == 204


@pytest.mark.asyncio
async def test_refresh_token_is_encrypted_at_rest(client, auth_headers):
    """The most security-relevant test in this file: a stored refresh_token must not be readable
    as plaintext directly from the database row."""
    user_id = await _user_id(client, auth_headers)
    plaintext = "1//0g-super-secret-refresh-token"
    creds = Credentials(token="access-tok", refresh_token=plaintext, scopes=google_credentials.SCOPES)

    await google_credentials.save_credentials(user_id, creds, google_email="alice@gmail.com")

    row = await _get_credential(user_id)
    assert row is not None
    assert row.refresh_token_enc != plaintext
    assert plaintext not in row.refresh_token_enc
    assert decrypt_secret(row.refresh_token_enc) == plaintext
    assert row.google_email == "alice@gmail.com"


@pytest.mark.asyncio
async def test_broadcast_only_reaches_owner(monkeypatch):
    """This is the bug the whole per-user refactor exists to fix: a calendar change must only be
    pushed to the owning user, never fanned out to everyone online."""
    broadcast = AsyncMock()
    monkeypatch.setattr(manager, "broadcast_to_users", broadcast)

    await calendar_service.broadcast_change("owner-123", "calendar_event_created", {"event": {"id": "e1"}})

    broadcast.assert_awaited_once_with(["owner-123"], {"type": "calendar_event_created", "event": {"id": "e1"}})


# ---------------------------------------------------------------------------
# _poll_one_user - incremental sync for a single already-connected user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_one_user_not_connected_is_a_quiet_noop(client, auth_headers):
    user_id = await _user_id(client, auth_headers)
    await calendar_service._poll_one_user(user_id)  # no GoogleCalendarCredential row - must not raise


@pytest.mark.asyncio
async def test_poll_one_user_bootstrap_broadcasts_and_stores_token(client, auth_headers, monkeypatch):
    """No stored sync cursor yet: should do a bootstrap listing, broadcast whatever it finds, and
    save the returned nextSyncToken for the next poll."""
    user_id = await _user_id(client, auth_headers)
    await _seed_credential(user_id)

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

    async def _fake_service(uid):
        return fake_service

    monkeypatch.setattr(calendar_service, "_service", _fake_service)
    broadcast = AsyncMock()
    monkeypatch.setattr(calendar_service, "broadcast_change", broadcast)

    await calendar_service._poll_one_user(user_id)

    call_kwargs = fake_service.events.return_value.list.call_args.kwargs
    assert "syncToken" not in call_kwargs
    assert "timeMin" in call_kwargs
    broadcast.assert_awaited_once_with(user_id, "calendar_event_updated", {"event": {
        "id": "evt-1", "title": "Team sync",
        "start": "2026-08-10T10:00:00+07:00", "end": "2026-08-10T10:30:00+07:00", "url": None,
    }})

    row = await _get_credential(user_id)
    assert row.sync_token == "token-123"


@pytest.mark.asyncio
async def test_poll_one_user_incremental_handles_deletion(client, auth_headers, monkeypatch):
    """With a stored sync cursor, should request an incremental diff and treat a cancelled item
    as a deletion rather than trying to render it as an event."""
    user_id = await _user_id(client, auth_headers)
    await _seed_credential(user_id)
    await google_credentials.set_sync_token(user_id, "old-token")

    fake_service = MagicMock()
    fake_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "evt-2", "status": "cancelled"}],
        "nextSyncToken": "new-token",
    }

    async def _fake_service(uid):
        return fake_service

    monkeypatch.setattr(calendar_service, "_service", _fake_service)
    broadcast = AsyncMock()
    monkeypatch.setattr(calendar_service, "broadcast_change", broadcast)

    await calendar_service._poll_one_user(user_id)

    call_kwargs = fake_service.events.return_value.list.call_args.kwargs
    assert call_kwargs["syncToken"] == "old-token"
    broadcast.assert_awaited_once_with(user_id, "calendar_event_deleted", {"event_id": "evt-2"})

    assert await google_credentials.get_sync_token(user_id) == "new-token"


@pytest.mark.asyncio
async def test_poll_one_user_expired_token_falls_back_to_full_resync(client, auth_headers, monkeypatch):
    """A 410 from Google means the stored cursor is no longer valid - the poller must drop it and
    resync from scratch instead of erroring out forever on every future poll."""
    user_id = await _user_id(client, auth_headers)
    await _seed_credential(user_id)
    await google_credentials.set_sync_token(user_id, "stale-token")

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

    async def _fake_service(uid):
        return fake_service

    monkeypatch.setattr(calendar_service, "_service", _fake_service)
    monkeypatch.setattr(calendar_service, "broadcast_change", AsyncMock())

    await calendar_service._poll_one_user(user_id)

    assert len(calls) == 2
    assert "syncToken" in calls[0]
    assert "syncToken" not in calls[1]
    assert await google_credentials.get_sync_token(user_id) == "fresh-token"


# ---------------------------------------------------------------------------
# poll_calendar_changes - fans out over connected users who are currently online
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_calendar_changes_only_polls_online_connected_users(client, auth_headers, other_auth_headers, monkeypatch):
    online_id = await _user_id(client, auth_headers)
    offline_id = await _user_id(client, other_auth_headers)
    await _seed_credential(online_id)
    await _seed_credential(offline_id)

    polled = []

    async def _fake_poll_one(user_id):
        polled.append(user_id)

    monkeypatch.setattr(calendar_service, "_poll_one_user", _fake_poll_one)

    with _online(online_id):
        await calendar_service.poll_calendar_changes()

    assert polled == [online_id]


@pytest.mark.asyncio
async def test_poll_calendar_changes_no_connected_users_is_a_noop(client):
    await calendar_service.poll_calendar_changes()  # nothing seeded, must not raise


@pytest.mark.asyncio
async def test_poll_calendar_changes_one_user_failure_does_not_block_another(client, auth_headers, other_auth_headers, monkeypatch):
    good_id = await _user_id(client, auth_headers)
    bad_id = await _user_id(client, other_auth_headers)
    await _seed_credential(good_id)
    await _seed_credential(bad_id)

    polled = []

    async def _fake_poll_one(user_id):
        if user_id == bad_id:
            raise RuntimeError("boom")
        polled.append(user_id)

    monkeypatch.setattr(calendar_service, "_poll_one_user", _fake_poll_one)

    with _online(good_id), _online(bad_id):
        await calendar_service.poll_calendar_changes()  # must not raise

    assert polled == [good_id]
