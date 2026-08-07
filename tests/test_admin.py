import pytest

from src.services import reminder_service
from src.services.scheduler import scheduler


@pytest.mark.asyncio
async def test_me_includes_role(client, auth_headers):
    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


@pytest.mark.asyncio
async def test_non_admin_cannot_access_admin_routes(client, auth_headers):
    resp = await client.get("/api/v1/admin/stats", headers=auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_view_stats(client, admin_auth_headers, auth_headers):
    resp = await client.get("/api/v1/admin/stats", headers=admin_auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_users"] >= 2
    assert "total_conversations" in body
    assert "total_messages" in body
    assert "new_users_last_7_days" in body


@pytest.mark.asyncio
async def test_admin_can_list_users(client, admin_auth_headers, auth_headers):
    resp = await client.get("/api/v1/admin/users", headers=admin_auth_headers)
    assert resp.status_code == 200
    emails = {u["email"] for u in resp.json()}
    assert "admin@example.com" in emails
    assert "alice@example.com" in emails


@pytest.mark.asyncio
async def test_admin_can_promote_and_demote_other_user(client, admin_auth_headers, auth_headers):
    users_resp = await client.get("/api/v1/admin/users", headers=admin_auth_headers)
    alice = next(u for u in users_resp.json() if u["email"] == "alice@example.com")

    resp = await client.patch(
        f"/api/v1/admin/users/{alice['id']}/role", json={"role": "admin"}, headers=admin_auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"

    resp = await client.patch(
        f"/api/v1/admin/users/{alice['id']}/role", json={"role": "user"}, headers=admin_auth_headers
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


@pytest.mark.asyncio
async def test_admin_cannot_change_own_role(client, admin_auth_headers):
    me = await client.get("/api/v1/auth/me", headers=admin_auth_headers)
    resp = await client.patch(
        f"/api/v1/admin/users/{me.json()['id']}/role", json={"role": "user"}, headers=admin_auth_headers
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_own_account(client, admin_auth_headers):
    me = await client.get("/api/v1/auth/me", headers=admin_auth_headers)
    resp = await client.patch(
        f"/api/v1/admin/users/{me.json()['id']}/status", json={"is_active": False}, headers=admin_auth_headers
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_deactivated_user_loses_access(client, admin_auth_headers, auth_headers):
    users_resp = await client.get("/api/v1/admin/users", headers=admin_auth_headers)
    alice = next(u for u in users_resp.json() if u["email"] == "alice@example.com")

    resp = await client.patch(
        f"/api/v1/admin/users/{alice['id']}/status", json={"is_active": False}, headers=admin_auth_headers
    )
    assert resp.status_code == 200

    resp = await client.get("/api/v1/auth/me", headers=auth_headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_list_and_view_conversations(client, admin_auth_headers, auth_headers, other_auth_headers):
    bob = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()
    conv = (
        await client.post(
            "/api/v1/conversations", json={"type": "direct", "participant_ids": [bob["id"]]}, headers=auth_headers
        )
    ).json()

    resp = await client.get("/api/v1/admin/conversations", headers=admin_auth_headers)
    assert resp.status_code == 200
    listed = next(c for c in resp.json() if c["id"] == conv["id"])
    assert listed["participant_count"] == 2

    resp = await client.get(f"/api/v1/admin/conversations/{conv['id']}/messages", headers=admin_auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []

    resp = await client.delete(f"/api/v1/admin/conversations/{conv['id']}", headers=admin_auth_headers)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_non_admin_cannot_access_new_admin_routes(client, auth_headers):
    for path in ("/api/v1/admin/tasks", "/api/v1/admin/reminders", "/api/v1/admin/memories"):
        resp = await client.get(path, headers=auth_headers)
        assert resp.status_code == 403, path


@pytest.mark.asyncio
async def test_admin_can_list_and_delete_tasks(client, admin_auth_headers, auth_headers):
    alice = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    created = (
        await client.post("/api/v1/tasks", json={"title": "Admin-visible task"}, headers=auth_headers)
    ).json()

    resp = await client.get("/api/v1/admin/tasks", headers=admin_auth_headers)
    assert resp.status_code == 200
    listed = next(t for t in resp.json() if t["id"] == created["id"])
    assert listed["owner_id"] == alice["id"]
    assert listed["owner_email"] == "alice@example.com"
    assert listed["owner_display_name"] == alice["display_name"]
    assert listed["conversation_label"] is None

    resp = await client.delete(f"/api/v1/admin/tasks/{created['id']}", headers=admin_auth_headers)
    assert resp.status_code == 204

    resp = await client.get("/api/v1/tasks", headers=auth_headers)
    assert created["id"] not in [t["id"] for t in resp.json()]


@pytest.mark.asyncio
async def test_admin_tasks_owner_filter(client, admin_auth_headers, auth_headers, other_auth_headers):
    alice = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    await client.post("/api/v1/tasks", json={"title": "Alice task"}, headers=auth_headers)
    await client.post("/api/v1/tasks", json={"title": "Bob task"}, headers=other_auth_headers)

    resp = await client.get(f"/api/v1/admin/tasks?owner_id={alice['id']}", headers=admin_auth_headers)
    assert resp.status_code == 200
    owners = {t["owner_id"] for t in resp.json()}
    assert owners == {alice["id"]}


@pytest.mark.asyncio
async def test_admin_can_list_and_delete_memories(client, admin_auth_headers, auth_headers):
    alice = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    created = (
        await client.post(
            "/api/v1/memories", json={"category": "Work", "title": "Admin-visible memory"}, headers=auth_headers
        )
    ).json()

    resp = await client.get("/api/v1/admin/memories", headers=admin_auth_headers)
    assert resp.status_code == 200
    listed = next(m for m in resp.json() if m["id"] == created["id"])
    assert listed["owner_id"] == alice["id"]
    assert listed["owner_email"] == "alice@example.com"

    resp = await client.delete(f"/api/v1/admin/memories/{created['id']}", headers=admin_auth_headers)
    assert resp.status_code == 204

    resp = await client.get("/api/v1/memories", headers=auth_headers)
    assert created["id"] not in [m["id"] for m in resp.json()]


@pytest.mark.asyncio
async def test_admin_can_list_and_delete_reminders_and_cancels_scheduler_job(
    client, admin_auth_headers, auth_headers
):
    alice = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
    created = (
        await client.post(
            "/api/v1/reminders",
            json={"title": "Admin-visible reminder", "due_at_iso": "2026-08-10T15:00:00"},
            headers=auth_headers,
        )
    ).json()
    assert scheduler.get_job(created["id"]) is not None

    resp = await client.get("/api/v1/admin/reminders", headers=admin_auth_headers)
    assert resp.status_code == 200
    listed = next(r for r in resp.json() if r["id"] == created["id"])
    assert listed["owner_id"] == alice["id"]
    assert listed["owner_email"] == "alice@example.com"
    assert listed["status"] == "scheduled"

    resp = await client.delete(f"/api/v1/admin/reminders/{created['id']}", headers=admin_auth_headers)
    assert resp.status_code == 204

    # Hard-deleted (unlike the user-facing DELETE, which only soft-cancels) and the underlying
    # APScheduler job must actually be gone, not just the DB row.
    assert scheduler.get_job(created["id"]) is None
    resp = await client.get("/api/v1/reminders", headers=auth_headers)
    assert created["id"] not in [r["id"] for r in resp.json()]


@pytest.mark.asyncio
async def test_admin_delete_reminder_404_when_missing(client, admin_auth_headers):
    resp = await client.delete("/api/v1/admin/reminders/does-not-exist", headers=admin_auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_reminders_include_ownerless_reminders(client, admin_auth_headers):
    reminder = await reminder_service.schedule_reminder(
        owner_id=None, title="Agent reminder", due_at_iso="2026-08-10T15:00:00", source="agent"
    )

    resp = await client.get("/api/v1/admin/reminders", headers=admin_auth_headers)
    assert resp.status_code == 200
    listed = next(r for r in resp.json() if r["id"] == reminder.id)
    assert listed["owner_id"] is None
    assert listed["owner_email"] is None
    assert listed["owner_display_name"] is None
