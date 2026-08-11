from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from src.config import get_settings
from src.db import session as db_session
from src.db.models import Conversation, ConversationParticipant, Message, User
from src.models.schemas import MessageScope
from src.services import chat_service


async def _get_user_id(email: str) -> str:
    async with db_session.async_session_maker() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        return user.id


async def _seed_conversation(
    alice_id: str, bob_id: str, messages: list[tuple[str, str, datetime]], last_read_ats: dict[str, datetime] | None = None
) -> str:
    """Seeds a direct conversation between alice/bob with messages at explicit `created_at`
    timestamps (bypassing create_message's "always now" default) - needed to test date/time-scoped
    queries deterministically instead of racing the real clock."""
    last_read_ats = last_read_ats or {}
    async with db_session.async_session_maker() as db:
        conversation = Conversation(type="direct", name=None, created_by=alice_id)
        db.add(conversation)
        await db.flush()
        for uid in (alice_id, bob_id):
            db.add(
                ConversationParticipant(
                    conversation_id=conversation.id,
                    user_id=uid,
                    last_read_at=last_read_ats.get(uid, datetime.now(UTC)),
                )
            )
        for sender_id, content, created_at in messages:
            db.add(Message(conversation_id=conversation.id, sender_id=sender_id, content=content, created_at=created_at))
        await db.commit()
        return conversation.id


@pytest.mark.asyncio
async def test_latest_n_returns_last_n_in_chronological_order(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    msgs = [(alice_id, f"msg{i}", now - timedelta(minutes=10 - i)) for i in range(10)]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    async with db_session.async_session_maker() as db:
        result = await chat_service.get_scoped_messages(db, conv_id, alice_id, MessageScope(kind="latest_n", count=3))

    assert [m.content for m in result] == ["msg7", "msg8", "msg9"]


@pytest.mark.asyncio
async def test_today_excludes_earlier_days(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    today_midnight = chat_service._local_midnight_utc()
    msgs = [
        (alice_id, "today", today_midnight + timedelta(hours=1)),
        (alice_id, "yesterday", today_midnight - timedelta(hours=1)),
        (alice_id, "two days ago", today_midnight - timedelta(days=2)),
    ]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    async with db_session.async_session_maker() as db:
        result = await chat_service.get_scoped_messages(db, conv_id, alice_id, MessageScope(kind="today"))

    assert [m.content for m in result] == ["today"]


@pytest.mark.asyncio
async def test_yesterday_only_includes_yesterday(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    today_midnight = chat_service._local_midnight_utc()
    msgs = [
        (alice_id, "today", today_midnight + timedelta(hours=1)),
        (alice_id, "yesterday", today_midnight - timedelta(hours=1)),
        (alice_id, "two days ago", today_midnight - timedelta(days=1, hours=1)),
    ]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    async with db_session.async_session_maker() as db:
        result = await chat_service.get_scoped_messages(db, conv_id, alice_id, MessageScope(kind="yesterday"))

    assert [m.content for m in result] == ["yesterday"]


@pytest.mark.asyncio
async def test_this_week_excludes_last_week(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    week_start = chat_service._local_week_start_utc()
    msgs = [
        (alice_id, "this week", week_start + timedelta(hours=1)),
        (alice_id, "last week", week_start - timedelta(hours=1)),
    ]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    async with db_session.async_session_maker() as db:
        result = await chat_service.get_scoped_messages(db, conv_id, alice_id, MessageScope(kind="this_week"))

    assert [m.content for m in result] == ["this week"]


@pytest.mark.asyncio
async def test_rolling_hours_window(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    msgs = [
        (alice_id, "30 min ago", now - timedelta(minutes=30)),
        (alice_id, "2 hours ago", now - timedelta(hours=2)),
        (alice_id, "4 hours ago", now - timedelta(hours=4)),
    ]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    async with db_session.async_session_maker() as db:
        one_hour = await chat_service.get_scoped_messages(
            db, conv_id, alice_id, MessageScope(kind="rolling_hours", hours=1)
        )
        five_hours = await chat_service.get_scoped_messages(
            db, conv_id, alice_id, MessageScope(kind="rolling_hours", hours=5)
        )

    assert [m.content for m in one_hour] == ["30 min ago"]
    assert [m.content for m in five_hours] == ["4 hours ago", "2 hours ago", "30 min ago"]


@pytest.mark.asyncio
async def test_custom_range_filters_by_since_and_until(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    tz = ZoneInfo(get_settings().calendar_timezone)
    base = datetime(2026, 1, 10, 12, 0, tzinfo=tz)
    msgs = [
        (alice_id, "too early", base - timedelta(hours=2)),
        (alice_id, "in range", base),
        (alice_id, "too late", base + timedelta(hours=2)),
    ]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    # Naive strings, no UTC offset - same shape the frontend sends (datetime-local + ":00"),
    # exercising _parse_scope_datetime's "assume calendar_timezone" branch.
    scope = MessageScope(kind="custom_range", since="2026-01-10T11:00:00", until="2026-01-10T13:00:00")
    async with db_session.async_session_maker() as db:
        result = await chat_service.get_scoped_messages(db, conv_id, alice_id, scope)

    assert [m.content for m in result] == ["in range"]


@pytest.mark.asyncio
async def test_unread_excludes_own_messages_and_messages_before_last_read(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    alice_last_read = now - timedelta(hours=1)
    msgs = [
        (bob_id, "before alice read", now - timedelta(hours=2)),
        (bob_id, "after alice read", now - timedelta(minutes=10)),
        (alice_id, "own message after read", now - timedelta(minutes=5)),
    ]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs, last_read_ats={alice_id: alice_last_read})

    async with db_session.async_session_maker() as db:
        result = await chat_service.get_scoped_messages(db, conv_id, alice_id, MessageScope(kind="unread"))

    assert [m.content for m in result] == ["after alice read"]


@pytest.mark.asyncio
async def test_today_is_not_capped_at_fifty(client, auth_headers, other_auth_headers):
    """The bug this feature fixes: the old frontend-only filtering never loaded more than the last
    50 messages (Frontend/src/hooks/useMessages.js), so any scope beyond that silently missed
    data. Prove the backend query has no such cap."""
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    today_midnight = chat_service._local_midnight_utc()
    msgs = [(alice_id, f"msg{i}", today_midnight + timedelta(minutes=i)) for i in range(60)]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    async with db_session.async_session_maker() as db:
        result = await chat_service.get_scoped_messages(db, conv_id, alice_id, MessageScope(kind="today"))

    assert len(result) == 60


@pytest.mark.asyncio
async def test_search_messages_case_insensitive_substring_match(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    conv_id = await _seed_conversation(alice_id, bob_id, [(alice_id, "the Deadline is Friday", now)])

    async with db_session.async_session_maker() as db:
        result = await chat_service.search_messages(db, conv_id, "deadline")

    assert [m.content for m in result] == ["the Deadline is Friday"]


@pytest.mark.asyncio
async def test_search_messages_no_match_returns_empty(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    conv_id = await _seed_conversation(alice_id, bob_id, [(alice_id, "hello there", now)])

    async with db_session.async_session_maker() as db:
        result = await chat_service.search_messages(db, conv_id, "deadline")

    assert result == []


@pytest.mark.asyncio
async def test_search_messages_orders_newest_first_then_chronological_and_respects_limit(
    client, auth_headers, other_auth_headers
):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    msgs = [(alice_id, f"deadline msg{i}", now - timedelta(minutes=30 - i)) for i in range(30)]
    conv_id = await _seed_conversation(alice_id, bob_id, msgs)

    async with db_session.async_session_maker() as db:
        result = await chat_service.search_messages(db, conv_id, "deadline", limit=20)

    assert len(result) == 20
    # The 20 most recent matches (msg10..msg29), in ascending chronological order.
    assert [m.content for m in result] == [f"deadline msg{i}" for i in range(10, 30)]


@pytest.mark.asyncio
async def test_search_messages_scoped_to_conversation(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    await _seed_conversation(alice_id, bob_id, [(alice_id, "secret codename deadline", now)])
    other_conv_id = await _seed_conversation(alice_id, bob_id, [(alice_id, "unrelated chit chat", now)])

    async with db_session.async_session_maker() as db:
        result = await chat_service.search_messages(db, other_conv_id, "deadline")

    assert result == []


@pytest.mark.asyncio
async def test_search_messages_blank_query_returns_empty(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    conv_id = await _seed_conversation(alice_id, bob_id, [(alice_id, "anything", now)])

    async with db_session.async_session_maker() as db:
        result = await chat_service.search_messages(db, conv_id, "   ")

    assert result == []


@pytest.mark.asyncio
async def test_search_messages_escapes_percent_in_query(client, auth_headers, other_auth_headers):
    alice_id = await _get_user_id("alice@example.com")
    bob_id = await _get_user_id("bob@example.com")
    now = datetime.now(UTC)
    conv_id = await _seed_conversation(
        alice_id,
        bob_id,
        [
            (alice_id, "discount is 10% today", now - timedelta(minutes=1)),
            (alice_id, "discount is 10x today", now),
        ],
    )

    async with db_session.async_session_maker() as db:
        result = await chat_service.search_messages(db, conv_id, "10%")

    assert [m.content for m in result] == ["discount is 10% today"]
