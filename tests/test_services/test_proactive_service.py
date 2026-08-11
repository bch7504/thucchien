from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from src.db import session as db_session
from src.db.models import Task
from src.services import chat_service, proactive_service


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


# ---------------------------------------------------------------- confirmation-reply path


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_creates_task_for_confirmer(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AsyncMock(
        content='{"has_commitment": true, "title": "Đi ăn tối", "due_at": "2026-08-11T20:00:00"}'
    )
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    confirmer_id = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    # Task thuộc về người XÁC NHẬN -> chính họ phải cấp quyền, không phải người đề xuất.
    await _grant_ai_permission(client, conversation_id, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "Tối nay 8 giờ đi ăn tối nhé")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    fake_llm.ainvoke.assert_awaited_once()
    prompt_sent = fake_llm.ainvoke.await_args.args[0]
    assert "Tối nay 8 giờ đi ăn tối nhé" in prompt_sent  # dùng đúng prompt có ngữ cảnh, không phải prompt gốc

    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == confirmer_id))).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].source == "proactive"
    assert tasks[0].status == "suggested"


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_uses_context_prompt_even_with_own_signal(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """"ok 8h nhé" tự nó cũng khớp _looks_like_commitment (có "8h") - phải vẫn đi nhánh có ngữ cảnh
    (dùng cả 2 tin) thay vì nhánh gốc chỉ đọc mỗi câu này, vốn không biết đang xác nhận việc gì."""
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AsyncMock(
        content='{"has_commitment": true, "title": "Đi ăn tối", "due_at": "2026-08-11T20:00:00"}'
    )
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    confirmer_id = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "Đi ăn tối nay nhé")

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=confirmer_id, content="ok 8h nhé"
    )

    prompt_sent = fake_llm.ainvoke.await_args.args[0]
    assert "Đi ăn tối nay nhé" in prompt_sent  # ngữ cảnh của tin đề xuất phải có mặt trong prompt
    assert "ok 8h nhé" in prompt_sent


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_skipped_when_no_recent_proposal(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    confirmer_id = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "cho tôi xin cái file báo cáo với")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    fake_llm.ainvoke.assert_not_awaited()
    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == confirmer_id))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_skipped_when_same_sender_as_prior(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    sender_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, sender_id, "8 giờ tối nay vào họp nhé")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=sender_id, content="ok")

    fake_llm.ainvoke.assert_not_awaited()
    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == sender_id))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_skipped_when_confirmer_permission_not_granted(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """Người đề xuất đã cấp quyền AI, nhưng người XÁC NHẬN thì chưa - Task thuộc về người xác nhận
    nên phải dùng đúng quyền của họ, không phải mượn quyền của người đề xuất."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    confirmer_id = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)  # chỉ người đề xuất cấp quyền

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay vào họp nhé")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    fake_llm.ainvoke.assert_not_awaited()
    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == confirmer_id))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_dedup_skips_second_confirmation(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AsyncMock(
        content='{"has_commitment": true, "title": "Đi ăn tối", "due_at": "2026-08-11T20:00:00"}'
    )
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    confirmer_id = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay đi ăn nhé")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")
    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=confirmer_id, content="chốt nhé"
    )

    fake_llm.ainvoke.assert_awaited_once()  # lần xác nhận thứ 2 bị chặn bởi dedup, không gọi LLM nữa
    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == confirmer_id))).scalars().all()
    assert len(tasks) == 1


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_lookback_error_does_not_raise(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    async def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(proactive_service, "_find_recent_proposal", _boom)

    confirmer_id = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()["id"]
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, other_auth_headers)

    # Không raise ra ngoài - lỗi phải bị nuốt bởi try/except gốc, giống mọi lỗi khác trong hàm này.
    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    fake_llm.ainvoke.assert_not_awaited()
    async with db_session.async_session_maker() as db:
        tasks = (await db.execute(select(Task).where(Task.owner_id == confirmer_id))).scalars().all()
    assert tasks == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_group_chain_skips_intermediate_confirmation(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """Group 3 người: A đề xuất, B "ok" (tạo Task cho B), C "ok" ngay sau - lookback của C phải
    nhảy qua tin "ok" của B (không có tín hiệu) để tìm lại đúng đề xuất gốc của A."""
    fake_llm = AsyncMock()
    fake_llm.ainvoke.side_effect = [
        AsyncMock(content='{"has_commitment": true, "title": "Đi ăn tối", "due_at": "2026-08-11T20:00:00"}'),
        AsyncMock(content='{"has_commitment": true, "title": "Đi ăn tối", "due_at": "2026-08-11T20:00:00"}'),
    ]
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    a_id = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["id"]
    b_id = (await client.get("/api/v1/auth/me", headers=other_auth_headers)).json()["id"]
    carol_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "password": "password123", "display_name": "Carol"},
    )
    carol_headers = {"Authorization": f"Bearer {carol_resp.json()['access_token']}"}
    c_id = carol_resp.json()["user"]["id"]

    conv_resp = await client.post(
        "/api/v1/conversations",
        json={"type": "group", "name": "Nhóm ăn tối", "participant_ids": [b_id, c_id]},
        headers=auth_headers,
    )
    conversation_id = conv_resp.json()["id"]

    await _grant_ai_permission(client, conversation_id, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, carol_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, a_id, "8 giờ tối nay đi ăn nhé")
        await chat_service.create_message(db, conversation_id, b_id, "ok")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=b_id, content="ok")

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, c_id, "ok")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=c_id, content="ok")

    assert fake_llm.ainvoke.await_count == 2
    async with db_session.async_session_maker() as db:
        b_tasks = (await db.execute(select(Task).where(Task.owner_id == b_id))).scalars().all()
        c_tasks = (await db.execute(select(Task).where(Task.owner_id == c_id))).scalars().all()
    assert len(b_tasks) == 1
    assert len(c_tasks) == 1
