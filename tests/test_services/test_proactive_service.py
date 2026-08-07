from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.db import session as db_session
from src.db.models import Task
from src.services import proactive_service


@pytest.mark.parametrize(
    "text,expected",
    [
        ("let's meet tomorrow at 3pm", True),
        ("don't forget the deadline is Friday", True),
        ("họp lúc 9 giờ sáng mai nhé", True),
        ("haha nice one", False),
        ("thanks!", False),
    ],
)
def test_looks_like_commitment(text, expected):
    assert proactive_service._looks_like_commitment(text) is expected


async def _create_conversation(client, creator_headers, other_headers):
    other_id = (await client.get("/api/v1/auth/me", headers=other_headers)).json()["id"]
    conv = await client.post(
        "/api/v1/conversations", json={"type": "direct", "participant_ids": [other_id]}, headers=creator_headers
    )
    return conv.json()["id"]


async def _grant_ai_permission(client, conversation_id, headers):
    await client.put(f"/api/v1/conversations/{conversation_id}/ai-permission", json={"granted": True}, headers=headers)


@pytest.mark.asyncio
async def test_maybe_suggest_task_skips_llm_when_no_signal(monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    await proactive_service.maybe_suggest_task(conversation_id="c1", sender_id="u1", content="thanks!")

    fake_llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_suggest_task_skips_when_ai_permission_not_granted(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    sender_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    # Permission was never granted for this conversation - default deny.

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="đừng quên deadline gửi báo cáo thứ hai nhé"
    )

    fake_llm.ainvoke.assert_not_awaited()
    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == sender_id))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_creates_suggested_task(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AsyncMock(
        content='{"has_commitment": true, "title": "Gửi báo cáo", "due_at": "2026-08-10T09:00:00"}'
    )
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    sender_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="đừng quên deadline gửi báo cáo thứ hai nhé"
    )

    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == sender_id))).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].title == "Gửi báo cáo"
    assert tasks[0].source == "proactive"
    assert tasks[0].status == "suggested"


@pytest.mark.asyncio
async def test_maybe_suggest_task_no_op_when_llm_says_no_commitment(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AsyncMock(content='{"has_commitment": false}')
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    sender_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="meeting tomorrow, just kidding"
    )

    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == sender_id))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_skips_llm_when_over_budget(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    async def _over_budget():
        return True

    monkeypatch.setattr(proactive_service.usage_service, "is_over_budget", _over_budget)

    sender_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="đừng quên deadline gửi báo cáo thứ hai nhé"
    )

    fake_llm.ainvoke.assert_not_awaited()
    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == sender_id))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_never_raises_on_llm_error(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    fake_llm.ainvoke.side_effect = RuntimeError("boom")
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    sender_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="meeting tomorrow"
    )
