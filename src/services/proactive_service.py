import json
import logging
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import session as db_session
from src.db.models import Message, Task, User
from src.services import chat_service, usage_service
from src.services.llm import get_llm
from src.websocket.manager import manager

logger = logging.getLogger(__name__)

# Cheap pre-filter so we don't burn an LLM call on every message - only messages that at least
# look like they might mention a time/commitment, look like a short reply agreeing to one, or look
# like a cancellation/reschedule, go on to the real (LLM) check. Deliberately erring toward more
# false positives (harmless - the LLM step below just answers with an empty commitments list) over
# false negatives, which
# silently drop a real commitment with no visible error anywhere - e.g. "5 giờ chiều nay" (digit
# BEFORE "giờ", "chiều nay" not "...mai") used to slip through entirely before these patterns were
# added.
_SIGNAL_PATTERN = re.compile(
    r"tomorrow|tonight|next (mon|tue|wed|thu|fri|sat|sun)|deadline|due (date|by)|meeting|appointment|"
    r"remind|schedule|\d\s?(am|pm)|"
    r"ngày mai|hôm nay|sáng nay|trưa nay|chiều nay|tối nay|sáng mai|chiều mai|tối mai|tuần sau|"
    r"thứ (hai|ba|tư|năm|sáu|bảy)|chủ nhật|hạn chót|"
    r"cuộc họp|họp lúc|hẹn|mời|rủ|nhắc (tôi|mình|nhở)|lịch|lúc \d|giờ \d|\d{1,2}\s?(giờ|h)\d{0,2}\b",
    re.IGNORECASE,
)

# Loose PREFIX (not a whole-string anchor) so it still matches replies that both confirm AND repeat
# a time, e.g. "ok 8h nhé", "ừ chốt lịch nhé" - only used as a second pre-filter trigger (see
# maybe_suggest_task), never to decide ownership by itself; the windowed LLM pass below does that.
_CONFIRMATION_CORE = (
    r"ok(?:ay|ê|ie)?|đc|được(?:\s+rồi)?|đồng\s*ý|nhất\s*trí|ừ+m?|ừa|vâng|dạ|rồi|"
    r"chốt(?:\s+(?:kèo|nhé|vậy))?|"
    r"yes|yeah|yep|sure|agreed|deal|sounds\s+good|works\s+for\s+me|got\s+it|understood"
)
_CONFIRMATION_PREFIX = re.compile(rf"^(?:{_CONFIRMATION_CORE})\b", re.IGNORECASE)

# A third pre-filter trigger, separate from the two above: cancelling or rescheduling a commitment
# ("thôi huỷ nhé", "đổi giờ nhé") often doesn't itself look like a new commitment or a plain
# agreement - without this, such a message would never pass the cheap pre-filter at all, so the
# LLM's "cancelled": true handling (retraction, F1/E1) would silently never get a chance to run.
_CANCELLATION_HINT = re.compile(
    # h[uủ][yỷ] covers both accepted spellings of "huỷ/hủy" - Vietnamese allows the diacritic hook
    # on either vowel for this word, and both are common in casual typing.
    r"h[uủ][yỷ]|thôi\s+(khỏi|không)|không\s+(đi|đến|họp|làm)\s+nữa|đổi\s+(giờ|lịch|kế\s*hoạch)|"
    r"dời\s+(giờ|lịch)|cancel|reschedule",
    re.IGNORECASE,
)

# How much conversation context to hand the LLM per check - bounded on 3 independent axes so
# neither a busy group (drowns a proposal in noise within seconds) nor a quiet 1-1 (a reply hours
# or days later) breaks the window in opposite directions.
_WINDOW_MAX_MESSAGES = 30
_WINDOW_MAX_AGE = timedelta(hours=6)
_WINDOW_MAX_CHARS = 4000


def _looks_like_commitment(text: str) -> bool:
    return bool(_SIGNAL_PATTERN.search(text))


def _might_be_confirmation(text: str) -> bool:
    """Loose signal that `text` might be agreeing to something proposed earlier - only requires the
    message to START WITH an agreement word, not consist entirely of one. Cheap and deliberately
    over-eager: the windowed LLM pass and _verify_owner below are what actually decide ownership."""
    stripped = text.strip()
    if not stripped or len(stripped) > 60:  # real confirmations are short; also bounds regex work
        return False
    return bool(_CONFIRMATION_PREFIX.match(stripped))


def _might_cancel_or_reschedule(text: str) -> bool:
    return bool(_CANCELLATION_HINT.search(text))


def _strip_fence(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


async def _load_window(
    db: AsyncSession, *, conversation_id: str
) -> tuple[list[tuple[Message, User]], dict[str, str], set[str]]:
    """Load the recent slice of this conversation the LLM is allowed to reason over.

    Returns (window oldest->newest, roster {display_name: user_id}, granted_ids).

    window ONLY contains messages sent by someone who has granted AI permission for this
    conversation (ai_permissions.granted) - the only way this background pass avoids sending
    a non-consenting participant's message content out to Gemini/Groq. Deliberate consequence:
    a proposal from someone who hasn't granted permission is invisible to the LLM, so nobody -
    not even a consenting person who later confirms it - gets a suggestion from it.

    roster lists EVERY participant of the conversation (not just ones with a message in window),
    so the LLM can recognize real member names - but two participants sharing the same
    display_name are BOTH dropped from it: there's no safe way to resolve which one a name refers
    to, so neither can ever become an owner (fail-closed) rather than guessing.
    """
    participant_ids = await chat_service.get_participant_ids(db, conversation_id)
    granted_ids = await chat_service.get_granted_user_ids(db, conversation_id)

    roster: dict[str, str] = {}
    if participant_ids:
        name_counts: dict[str, int] = {}
        id_to_name: dict[str, str] = {}
        rows = (await db.execute(select(User.id, User.display_name).where(User.id.in_(participant_ids)))).all()
        for user_id, display_name in rows:
            id_to_name[user_id] = display_name
            name_counts[display_name] = name_counts.get(display_name, 0) + 1
        roster = {name: uid for uid, name in id_to_name.items() if name_counts[name] == 1}

    cutoff = datetime.now(UTC) - _WINDOW_MAX_AGE
    rows = (
        await db.execute(
            select(Message, User)
            .join(User, User.id == Message.sender_id)
            .where(
                Message.conversation_id == conversation_id,
                Message.sender_id.in_(granted_ids),
                Message.created_at >= cutoff,
            )
            .order_by(Message.created_at.desc())
            .limit(_WINDOW_MAX_MESSAGES)
        )
    ).all()
    window = list(reversed(rows))  # oldest -> newest, so message_index matches reading order

    total_chars = sum(len(m.content) for m, _ in window)
    while total_chars > _WINDOW_MAX_CHARS and len(window) > 1:
        dropped, _sender = window.pop(0)  # drop the OLDEST first - the just-sent message must survive
        total_chars -= len(dropped.content)

    return window, roster, granted_ids


def _format_window(window: list[tuple[Message, User]], tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    lines = [
        f"[{idx}] {sender.display_name} ({message.created_at.astimezone(tz).strftime('%H:%M')}): {message.content}"
        for idx, (message, sender) in enumerate(window, start=1)
    ]
    return "\n".join(lines)


def _build_window_prompt(window: list[tuple[Message, User]], *, now: datetime, tz_name: str) -> str:
    return (
        "Below is a recent excerpt of a group or direct chat in a team chat app. Identify personal "
        "commitments, appointments, or deadlines mentioned in it, and for each one, determine WHO "
        "is bound by it. Output ONLY JSON, no prose, no markdown code fence, with exactly this "
        'shape: {"commitments": [{"title": string (tiếng Việt, short), "due_at": ISO 8601 datetime '
        'string or null, "proposal_message_index": int (the [N] of the message that first proposed '
        'this commitment), "cancelled": boolean, "owners": [{"name": string, "evidence": "self" | '
        '"confirmed", "message_index": int}]}]}. If nothing qualifies, output {"commitments": []}.\n\n'
        "Rules:\n"
        "- A person is an owner ONLY IF a message they themselves sent shows it: either they "
        'stated their own plan (evidence "self") or they explicitly agreed to someone else\'s '
        'proposal (evidence "confirmed"). Point at that exact message with message_index.\n'
        "- NEVER list someone as owner because they were addressed, invited, or assigned. Being "
        'told "Chi ơi mai 3h gửi báo cáo nhé" does NOT make Chi an owner unless Chi replied '
        "agreeing herself.\n"
        "- Someone who assigns work to another person is NOT an owner of it themself.\n"
        "- Silence is not agreement. Nobody who did not send a message is ever an owner.\n"
        '- Merely reporting unavailability ("tối nay tôi bận"), asking a question ("mai họp mấy '
        'giờ thế?"), recounting the past ("hôm nay tôi mất ví"), or joking is NOT a commitment.\n'
        "- If a later message cancels a commitment, emit it again with \"cancelled\": true (owners "
        "can be empty). If a later message changes its time, emit ONE commitment with the final "
        "time, AND a separate cancelled:true entry for the proposal it replaced.\n"
        '- due_at: resolve relative dates/times ("hôm nay", "ngày mai", "tuần sau") against the '
        f"current date and time, which is {now.strftime('%A, %Y-%m-%d %H:%M')} ({tz_name}).\n\n"
        "Examples:\n"
        "[1] An (09:00): tối nay 8h tôi đi họp\n[2] Binh (09:01): ok\n"
        '-> {"commitments":[{"title":"Họp tối nay","due_at":"...T20:00:00",'
        '"proposal_message_index":1,"cancelled":false,'
        '"owners":[{"name":"An","evidence":"self","message_index":1}]}]}\n'
        "(Binh only acknowledged - \"ok\" alone is not clear agreement here, so no owner for Binh)\n\n"
        "[1] An (09:00): Chi ơi mai 3h em gửi báo cáo nhé\n"
        '-> {"commitments":[{"title":"Gửi báo cáo","due_at":"...",'
        '"proposal_message_index":1,"cancelled":false,"owners":[]}]}\n'
        "(An assigned it to Chi but is not the owner themself; Chi hasn't replied, so no owners at all)\n\n"
        "[1] An (09:00): 8h họp nhé\n[2] An (09:05): thôi huỷ nhé\n"
        '-> {"commitments":[{"proposal_message_index":1,"cancelled":true,"owners":[]}]}\n\n'
        f"Chat excerpt:\n{_format_window(window, tz_name)}"
    )


def _is_plain_int(value: object) -> bool:
    # bool is a subclass of int in Python - json.loads("true") -> True, which would otherwise pass
    # an isinstance(x, int) check and be silently treated as message_index 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _verify_owner(
    claim: object,
    *,
    window: list[tuple[Message, User]],
    roster: dict[str, str],
    granted_ids: set[str],
    proposal_idx: int,
) -> str | None:
    """Returns the verified user_id for an owner claim the LLM made, or None if it doesn't hold up.
    Every check here is fail-closed - a claim that can't be fully verified against real data is
    dropped, never guessed at. The LLM proposes; this function is what actually decides."""
    if not isinstance(claim, dict):
        return None
    user_id = roster.get((claim.get("name") or "").strip())
    if user_id is None:  # name not a known, unambiguous participant
        return None
    idx = claim.get("message_index")
    if not _is_plain_int(idx) or not (1 <= idx <= len(window)):
        return None
    message, _sender = window[idx - 1]
    if message.sender_id != user_id:  # the cited message wasn't actually sent by this person -
        return None  # blocks "B said ok on Chi's behalf" from ever counting as Chi's evidence
    evidence = claim.get("evidence")
    if evidence not in ("self", "confirmed"):
        return None
    if evidence == "confirmed" and idx <= proposal_idx:  # a confirmation must come AFTER the proposal
        return None
    if user_id not in granted_ids:
        return None
    return user_id


def _parse_due_at(raw: object, tz_name: str) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        due_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if due_at.tzinfo is None:
        # LLM output has no UTC offset - treat it as the app's configured timezone, not naive/ambiguous.
        due_at = due_at.replace(tzinfo=ZoneInfo(tz_name))
    return due_at


async def _task_exists(db: AsyncSession, *, owner_id: str, source_message_id: str) -> bool:
    existing = (
        await db.execute(
            select(Task.id)
            .where(
                Task.owner_id == owner_id,
                Task.source_message_id == source_message_id,
                Task.source == "proactive",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return existing is not None


def _task_payload(task: Task) -> dict:
    return {
        "id": task.id,
        "conversation_id": task.conversation_id,
        "title": task.title,
        "due_at": task.due_at.isoformat() if task.due_at else None,
        "priority": task.priority,
        "status": task.status,
        "source": task.source,
        "created_at": task.created_at.isoformat(),
    }


async def _retract_tasks_for_source(db: AsyncSession, *, source_message_id: str) -> None:
    """Dismiss every still-"suggested" proactive Task spawned from this exact proposal message -
    used when a later message in the window cancels it or supersedes it with a new time. Tasks
    already acted on (status != "suggested") are left alone: their owner already confirmed a real
    action (possibly a real Calendar event/Reminder via Accept), which this background heuristic
    must never silently undo."""
    tasks = (
        await db.execute(
            select(Task).where(
                Task.source_message_id == source_message_id,
                Task.source == "proactive",
                Task.status == "suggested",
            )
        )
    ).scalars().all()
    if not tasks:
        return
    for task in tasks:
        task.status = "dismissed"
    await db.commit()
    for task in tasks:
        await db.refresh(task)
        # Reuses the existing "task_updated" event - TaskPage.jsx/TaskInboxPage.jsx already upsert
        # on it, and AppLayout.jsx only toasts on "task_suggested", so retracting a task produces
        # no spurious notification, just the row disappearing/changing status in the UI.
        await manager.broadcast_to_users([task.owner_id], {"type": "task_updated", "task": _task_payload(task)})


async def maybe_suggest_task(*, conversation_id: str, sender_id: str, content: str) -> None:
    """Best-effort, fire-and-forget: if a new message might contain or confirm a personal
    commitment/appointment/deadline, run a single LLM pass over a bounded, named window of this
    conversation's recent history and create a 'suggested' Task (same review flow as the manual
    Extract tasks action, Accept/Dismiss) for each participant the LLM found solid evidence for.

    Every owner the LLM proposes is re-verified against real data before anything is written -
    see _verify_owner. Requires each such participant to have granted AI permission for this
    conversation (ai_permissions) - both to have their own messages readable at all (_load_window)
    and to receive a Task built from them. Never raises - a failure here must not affect message
    delivery.
    """
    if not (_looks_like_commitment(content) or _might_be_confirmation(content) or _might_cancel_or_reschedule(content)):
        return

    try:
        async with db_session.async_session_maker() as db:
            permission = await chat_service.get_ai_permission(db, conversation_id, sender_id)
        if permission is None or not permission.granted:
            return

        # Ràng buộc đề bài: tối ưu chi phí - đây là lệnh gọi LLM tự động chạy nền trên mọi tin
        # nhắn có tín hiệu, nên là nơi cần chặn trước tiên khi đã vượt ngân sách; bỏ qua lặng lẽ
        # giống các điều kiện guard khác ở trên, không phải lỗi.
        if await usage_service.is_over_budget():
            return

        async with db_session.async_session_maker() as db:
            window, roster, granted_ids = await _load_window(db, conversation_id=conversation_id)
        if not window:
            return

        settings = get_settings()
        llm = get_llm()
        # Without today's date, the LLM has to guess the current date from its training data when
        # resolving relative expressions ("hôm nay", "tối nay", "ngày mai") - observed in practice
        # to land on the wrong YEAR half the time. Same fix as planner_node.py/task_tool.py.
        now = datetime.now(ZoneInfo(settings.calendar_timezone))
        prompt = _build_window_prompt(window, now=now, tz_name=settings.calendar_timezone)
        result = await llm.ainvoke(prompt)
        await usage_service.log_usage(
            provider=settings.llm_provider, model=settings.model_name, usage_metadata=result.usage_metadata
        )
        data = json.loads(_strip_fence(result.content))
        commitments = data.get("commitments")
        if not isinstance(commitments, list):
            return

        async with db_session.async_session_maker() as db:
            for commitment in commitments:
                if not isinstance(commitment, dict):
                    continue
                proposal_idx = commitment.get("proposal_message_index")
                if not _is_plain_int(proposal_idx) or not (1 <= proposal_idx <= len(window)):
                    continue
                source_message_id = window[proposal_idx - 1][0].id

                if commitment.get("cancelled"):
                    await _retract_tasks_for_source(db, source_message_id=source_message_id)
                    continue

                owners = commitment.get("owners")
                if not isinstance(owners, list):
                    continue
                title = (commitment.get("title") or "").strip()[:200] or "Cam kết mới"
                due_at = _parse_due_at(commitment.get("due_at"), settings.calendar_timezone)

                for claim in owners:
                    owner_id = _verify_owner(
                        claim, window=window, roster=roster, granted_ids=granted_ids, proposal_idx=proposal_idx
                    )
                    if owner_id is None:
                        continue
                    if await _task_exists(db, owner_id=owner_id, source_message_id=source_message_id):
                        continue  # dedup anchored to the proposal - idempotent on overlapping windows

                    task = Task(
                        owner_id=owner_id,
                        conversation_id=conversation_id,
                        title=title,
                        due_at=due_at,
                        priority="Medium",
                        source="proactive",
                        source_message_id=source_message_id,
                    )
                    db.add(task)
                    await db.commit()
                    await db.refresh(task)
                    await manager.broadcast_to_users(
                        [owner_id], {"type": "task_suggested", "task": _task_payload(task)}
                    )
    except Exception:  # noqa: BLE001 - background detection must never break message delivery
        logger.exception("Proactive commitment detection failed")
