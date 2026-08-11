import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from src.config import get_settings
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


@pytest.mark.parametrize(
    "text,expected",
    [
        ("thôi huỷ nhé", True),
        ("thôi hủy đi", True),
        ("đổi giờ nhé", True),
        ("cancel it", True),
        ("haha nice one", False),
    ],
)
def test_might_cancel_or_reschedule(text, expected):
    assert proactive_service._might_cancel_or_reschedule(text) is expected


async def _user_id(client, headers):
    return (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]


async def _create_conversation(client, creator_headers, other_headers):
    other_id = await _user_id(client, other_headers)
    conv = await client.post(
        "/api/v1/conversations", json={"type": "direct", "participant_ids": [other_id]}, headers=creator_headers
    )
    return conv.json()["id"]


async def _create_group(client, creator_headers, *other_headers_list, name="Nhóm"):
    other_ids = [await _user_id(client, h) for h in other_headers_list]
    conv = await client.post(
        "/api/v1/conversations",
        json={"type": "group", "name": name, "participant_ids": other_ids},
        headers=creator_headers,
    )
    return conv.json()["id"]


async def _register(client, *, email, display_name):
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "password123", "display_name": display_name}
    )
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


async def _grant_ai_permission(client, conversation_id, headers):
    await client.put(f"/api/v1/conversations/{conversation_id}/ai-permission", json={"granted": True}, headers=headers)


async def _grant_all(client, conversation_id, *headers_list):
    """Grant AI permission for every participant listed - the default for most tests, since
    _load_window only shows the LLM messages from senders who have granted. Tests that
    specifically exercise the permission boundary (D1) deliberately skip a participant instead."""
    for headers in headers_list:
        await _grant_ai_permission(client, conversation_id, headers)


def _llm_response(commitments: list[dict]) -> AsyncMock:
    # usage_metadata=None explicitly - otherwise AsyncMock auto-generates a mock attribute for it,
    # which usage_service.log_usage then tries to call .get(...) on, producing a coroutine instead
    # of a value and a noisy (harmless, self-caught) "Failed to log LLM usage" log on every call.
    return AsyncMock(content=json.dumps({"commitments": commitments}), usage_metadata=None)


async def _tasks_for(owner_id):
    async with db_session.async_session_maker() as db:
        return (await db.execute(select(Task).where(Task.owner_id == owner_id))).scalars().all()


# ---------------------------------------------------------------- maybe_suggest_task: main flow


@pytest.mark.asyncio
async def test_maybe_suggest_task_confirmation_creates_task_for_confirmer(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """1-1: A đề xuất, B "ok" -> B có task, source_message_id = id tin đề xuất của A."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        proposal = await chat_service.create_message(db, conversation_id, proposer_id, "Tối nay 8 giờ đi ăn tối nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Đi ăn tối",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Bob", "evidence": "confirmed", "message_index": 2}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    tasks = await _tasks_for(confirmer_id)
    assert len(tasks) == 1
    assert tasks[0].source == "proactive"
    assert tasks[0].status == "suggested"
    assert tasks[0].source_message_id == proposal.id


@pytest.mark.asyncio
async def test_maybe_suggest_task_rejects_owner_when_message_index_points_to_wrong_sender(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """★ C3: LLM claims owner "Bob" but message_index actually points at Carol's message - the
    sender-match check in _verify_owner must catch the lie regardless of the claimed name."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    carol_headers, carol_id = await _register(client, email="carol@example.com", display_name="Carol")
    proposer_id = await _user_id(client, auth_headers)
    bob_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_group(client, auth_headers, other_auth_headers, carol_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers, carol_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay đi ăn nhé")
        await chat_service.create_message(db, conversation_id, carol_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Đi ăn tối",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                # message_index 2 was actually sent by Carol, not Bob.
                "owners": [{"name": "Bob", "evidence": "confirmed", "message_index": 2}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=carol_id, content="ok")

    assert await _tasks_for(bob_id) == []
    assert await _tasks_for(carol_id) == []  # claimed name was "Bob", not Carol - no fallback guess either


@pytest.mark.asyncio
async def test_maybe_suggest_task_rejects_owner_when_confirmation_message_sent_by_someone_else(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """★ C3 variant: owner claimed as "Chi" but the cited message was sent by Bob - "B ok hộ Chi"
    must never count as Chi's own evidence."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    chi_headers, chi_id = await _register(client, email="chi@example.com", display_name="Chi")
    proposer_id = await _user_id(client, auth_headers)
    bob_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_group(client, auth_headers, other_auth_headers, chi_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers, chi_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "Chi ơi mai 3h em gửi báo cáo nhé")
        await chat_service.create_message(db, conversation_id, bob_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Gửi báo cáo",
                "due_at": "2026-08-12T15:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Chi", "evidence": "confirmed", "message_index": 2}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=chi_id, content="ok")

    assert await _tasks_for(chi_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_rejects_owner_name_not_in_roster(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Zzz Không Tồn Tại", "evidence": "self", "message_index": 1}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    assert await _tasks_for(proposer_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_rejects_when_display_name_is_ambiguous(client, auth_headers, other_auth_headers, monkeypatch):
    """Hai thành viên cùng display_name "Alice" - roster loại cả 2, không ai nhận được task dù
    message_index/sender khớp thật, vì không cách nào phân giải an toàn tên nào ứng với ai."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    twin_headers, twin_id = await _register(client, email="alice2@example.com", display_name="Alice")
    alice_id = await _user_id(client, auth_headers)
    conversation_id = await _create_group(client, auth_headers, twin_headers, name="Trùng tên")
    await _grant_all(client, conversation_id, auth_headers, twin_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, alice_id, "8 giờ tối nay họp nhé")
        await chat_service.create_message(db, conversation_id, twin_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Alice", "evidence": "confirmed", "message_index": 2}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=twin_id, content="ok")

    assert await _tasks_for(alice_id) == []
    assert await _tasks_for(twin_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_rejects_confirmed_evidence_not_after_proposal(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                # "confirmed" but pointing at the proposal message itself (index <= proposal_idx).
                "owners": [{"name": "Alice", "evidence": "confirmed", "message_index": 1}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    assert await _tasks_for(proposer_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_rejects_unknown_evidence_value(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Bob", "evidence": "assigned", "message_index": 2}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    assert await _tasks_for(confirmer_id) == []


def test_verify_owner_rejects_when_owner_has_not_granted_permission():
    """★ bảo mật: unit test trực tiếp _verify_owner (không qua maybe_suggest_task) - trong pipeline
    thật, _load_window đã tự loại tin của người chưa cấp quyền nên trường hợp này không thể phát
    sinh qua toàn luồng; test này bảo vệ tầng phòng thủ độc lập trong _verify_owner, phòng khi
    _load_window đổi hành vi sau này mà quên đồng bộ."""

    class _Msg:
        sender_id = "bob-id"
        content = "8 giờ tối nay họp nhé"

    class _User:
        display_name = "Bob"

    window = [(_Msg(), _User())]
    claim = {"name": "Bob", "evidence": "self", "message_index": 1}

    result = proactive_service._verify_owner(
        claim, window=window, roster={"Bob": "bob-id"}, granted_ids=set(), proposal_idx=1
    )
    assert result is None


@pytest.mark.asyncio
async def test_maybe_suggest_task_prompt_excludes_messages_from_non_granted_participants(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """★ D1: nội dung tin nhắn của người CHƯA cấp quyền AI không được xuất hiện trong prompt gửi
    ra LLM, kể cả khi tin đó có tín hiệu lịch trình rõ ràng."""
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = _llm_response([])
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    carol_headers, carol_id = await _register(client, email="ungranted@example.com", display_name="Carol")
    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_group(client, auth_headers, other_auth_headers, carol_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)
    # Carol never grants - her message must stay invisible to the LLM.

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, carol_id, "3 giờ chiều nay tôi họp riêng")
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay đi ăn tối nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    prompt_sent = fake_llm.ainvoke.await_args.args[0]
    assert "3 giờ chiều nay tôi họp riêng" not in prompt_sent
    assert "8 giờ tối nay đi ăn tối nhé" in prompt_sent
    assert "] Bob" in prompt_sent  # confirmer's "ok" line is present


@pytest.mark.asyncio
async def test_maybe_suggest_task_rejects_out_of_range_message_index(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Bob", "evidence": "confirmed", "message_index": 999}],
            }
        ]
    )

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    assert await _tasks_for(confirmer_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_is_idempotent_on_overlapping_windows(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    response = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Bob", "evidence": "confirmed", "message_index": 2}],
            }
        ]
    )
    fake_llm.ainvoke.side_effect = [response, response]

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")
    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    assert len(await _tasks_for(confirmer_id)) == 1


@pytest.mark.asyncio
async def test_maybe_suggest_task_cancelled_retracts_suggested_task(client, auth_headers, other_auth_headers, monkeypatch):
    """★ E1: đề xuất bị huỷ -> Task còn "suggested" sinh ra từ nó chuyển "dismissed"."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")
        await chat_service.create_message(db, conversation_id, confirmer_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Bob", "evidence": "confirmed", "message_index": 2}],
            }
        ]
    )
    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")
    tasks = await _tasks_for(confirmer_id)
    assert tasks[0].status == "suggested"

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "thôi huỷ nhé")

    broadcasts = []
    monkeypatch.setattr(
        proactive_service.manager,
        "broadcast_to_users",
        AsyncMock(side_effect=lambda ids, payload: broadcasts.append((ids, payload))),
    )
    fake_llm.ainvoke.return_value = _llm_response([{"proposal_message_index": 1, "cancelled": True, "owners": []}])
    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=proposer_id, content="thôi huỷ nhé")

    tasks = await _tasks_for(confirmer_id)
    assert len(tasks) == 1
    assert tasks[0].status == "dismissed"
    assert any(payload["type"] == "task_updated" and confirmer_id in ids for ids, payload in broadcasts)


@pytest.mark.asyncio
async def test_maybe_suggest_task_renegotiation_keeps_only_final_time(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """★ F1: A "8h nhé" -> B "9h được không" -> A "ok" phải chỉ còn 1 task active ở giờ cuối, task
    8h ban đầu bị rút lại (dismissed), không phải 2 task chồng lên nhau."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    a_id = await _user_id(client, auth_headers)
    b_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, a_id, "8h nhé")
    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Họp",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [{"name": "Alice", "evidence": "self", "message_index": 1}],
            }
        ]
    )
    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=a_id, content="8h nhé")

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, b_id, "9h được không")
        await chat_service.create_message(db, conversation_id, a_id, "ok")
    fake_llm.ainvoke.return_value = _llm_response(
        [
            {"proposal_message_index": 1, "cancelled": True, "owners": []},
            {
                "title": "Họp",
                "due_at": "2026-08-11T21:00:00",
                "proposal_message_index": 2,
                "cancelled": False,
                "owners": [{"name": "Alice", "evidence": "confirmed", "message_index": 3}],
            },
        ]
    )
    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=a_id, content="ok")

    tasks = await _tasks_for(a_id)
    active = [t for t in tasks if t.status == "suggested"]
    assert len(active) == 1
    # due_at comes back UTC from the DB - convert to local before checking the wall-clock hour.
    tz = ZoneInfo(get_settings().calendar_timezone)
    assert active[0].due_at.astimezone(tz).hour == 21
    assert any(t.status == "dismissed" for t in tasks)


@pytest.mark.asyncio
async def test_maybe_suggest_task_group_multiple_confirmers_in_one_pass(
    client, auth_headers, other_auth_headers, monkeypatch
):
    """Nhóm 3 người: B và C cùng "ok" -> 2 task, mỗi người 1, xử lý trong đúng 1 lệnh gọi LLM."""
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    carol_headers, carol_id = await _register(client, email="carol2@example.com", display_name="Carol")
    a_id = await _user_id(client, auth_headers)
    b_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_group(client, auth_headers, other_auth_headers, carol_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers, carol_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, a_id, "8 giờ tối nay đi ăn nhé")
        await chat_service.create_message(db, conversation_id, b_id, "ok")
        await chat_service.create_message(db, conversation_id, carol_id, "ok")

    fake_llm.ainvoke.return_value = _llm_response(
        [
            {
                "title": "Đi ăn tối",
                "due_at": "2026-08-11T20:00:00",
                "proposal_message_index": 1,
                "cancelled": False,
                "owners": [
                    {"name": "Bob", "evidence": "confirmed", "message_index": 2},
                    {"name": "Carol", "evidence": "confirmed", "message_index": 3},
                ],
            }
        ]
    )
    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=carol_id, content="ok")

    assert fake_llm.ainvoke.await_count == 1
    assert len(await _tasks_for(b_id)) == 1
    assert len(await _tasks_for(carol_id)) == 1


@pytest.mark.asyncio
async def test_load_window_excludes_messages_older_than_max_age(client, auth_headers, other_auth_headers):
    """D4: tin cũ hơn _WINDOW_MAX_AGE (6 giờ) bị loại khỏi cửa sổ."""
    sender_id = await _user_id(client, auth_headers)
    other_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        old = await chat_service.create_message(db, conversation_id, sender_id, "tin rất cũ")
        old.created_at = datetime.now(UTC) - timedelta(hours=7)
        await db.commit()
        await chat_service.create_message(db, conversation_id, other_id, "tin mới")

        window, _roster, _granted = await proactive_service._load_window(db, conversation_id=conversation_id)

    assert [m.content for m, _ in window] == ["tin mới"]


@pytest.mark.asyncio
async def test_load_window_caps_message_count(client, auth_headers, other_auth_headers):
    """B6: cửa sổ giới hạn _WINDOW_MAX_MESSAGES tin, giữ lại các tin MỚI NHẤT."""
    sender_id = await _user_id(client, auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    async with db_session.async_session_maker() as db:
        for i in range(proactive_service._WINDOW_MAX_MESSAGES + 5):
            await chat_service.create_message(db, conversation_id, sender_id, f"tin {i}")

        window, _roster, _granted = await proactive_service._load_window(db, conversation_id=conversation_id)

    assert len(window) == proactive_service._WINDOW_MAX_MESSAGES
    assert window[-1][0].content == f"tin {proactive_service._WINDOW_MAX_MESSAGES + 4}"


@pytest.mark.asyncio
async def test_maybe_suggest_task_invalid_json_does_not_raise(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AsyncMock(content="not json at all", usage_metadata=None)
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    assert await _tasks_for(confirmer_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_missing_commitments_key_does_not_raise(
    client, auth_headers, other_auth_headers, monkeypatch
):
    fake_llm = AsyncMock()
    fake_llm.ainvoke.return_value = AsyncMock(content=json.dumps({"unexpected": "shape"}), usage_metadata=None)
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    proposer_id = await _user_id(client, auth_headers)
    confirmer_id = await _user_id(client, other_auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_all(client, conversation_id, auth_headers, other_auth_headers)

    async with db_session.async_session_maker() as db:
        await chat_service.create_message(db, conversation_id, proposer_id, "8 giờ tối nay họp nhé")

    await proactive_service.maybe_suggest_task(conversation_id=conversation_id, sender_id=confirmer_id, content="ok")

    assert await _tasks_for(confirmer_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_skips_llm_when_over_budget(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    async def _over_budget():
        return True

    monkeypatch.setattr(proactive_service.usage_service, "is_over_budget", _over_budget)

    sender_id = await _user_id(client, auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="đừng quên deadline gửi báo cáo thứ hai nhé"
    )

    fake_llm.ainvoke.assert_not_awaited()
    assert await _tasks_for(sender_id) == []


@pytest.mark.asyncio
async def test_maybe_suggest_task_never_raises_on_db_error(client, auth_headers, other_auth_headers, monkeypatch):
    fake_llm = AsyncMock()
    monkeypatch.setattr(proactive_service, "get_llm", lambda: fake_llm)

    async def _boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(proactive_service, "_load_window", _boom)

    sender_id = await _user_id(client, auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    await _grant_ai_permission(client, conversation_id, auth_headers)

    # Không raise ra ngoài - lỗi phải bị nuốt bởi try/except gốc.
    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="8 giờ tối nay họp nhé"
    )

    fake_llm.ainvoke.assert_not_awaited()
    assert await _tasks_for(sender_id) == []


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

    sender_id = await _user_id(client, auth_headers)
    conversation_id = await _create_conversation(client, auth_headers, other_auth_headers)
    # Permission was never granted for this conversation - default deny.

    await proactive_service.maybe_suggest_task(
        conversation_id=conversation_id, sender_id=sender_id, content="đừng quên deadline gửi báo cáo thứ hai nhé"
    )

    fake_llm.ainvoke.assert_not_awaited()
    assert await _tasks_for(sender_id) == []
