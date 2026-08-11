import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import session as db_session
from src.db.models import Message, Task
from src.services import chat_service, usage_service
from src.services.llm import get_llm
from src.websocket.manager import manager

logger = logging.getLogger(__name__)

# Cheap pre-filter so we don't burn an LLM call on every "ok"/"thanks" message - only messages
# that at least look like they might mention a time/commitment go on to the real (LLM) check.
# Deliberately erring toward more false positives (harmless - the LLM step below just answers
# has_commitment: false) rather than false negatives, which silently drop a real commitment with
# no visible error anywhere - e.g. "5 giờ chiều nay" (digit BEFORE "giờ", "chiều nay" not "...mai")
# used to slip through entirely before these patterns were added.
_SIGNAL_PATTERN = re.compile(
    r"tomorrow|tonight|next (mon|tue|wed|thu|fri|sat|sun)|deadline|due (date|by)|meeting|appointment|"
    r"remind|schedule|\d\s?(am|pm)|"
    r"ngày mai|hôm nay|sáng nay|trưa nay|chiều nay|tối nay|sáng mai|chiều mai|tối mai|tuần sau|"
    r"thứ (hai|ba|tư|năm|sáu|bảy)|chủ nhật|hạn chót|"
    r"cuộc họp|họp lúc|hẹn|mời|rủ|nhắc (tôi|mình|nhở)|lịch|lúc \d|giờ \d|\d{1,2}\s?(giờ|h)\d{0,2}\b",
    re.IGNORECASE,
)

# Loose PREFIX (not a whole-string anchor) so it still matches replies that both confirm AND repeat
# a time, e.g. "ok 8h nhé", "ừ chốt lịch nhé" - those already match _SIGNAL_PATTERN too, and are
# exactly the messages that most need the 2-message context prompt below instead of being read in
# isolation (see maybe_suggest_task: has_signal and maybe_confirm are independent, not nested).
_CONFIRMATION_CORE = (
    r"ok(?:ay|ê|ie)?|đc|được(?:\s+rồi)?|đồng\s*ý|nhất\s*trí|ừ+m?|ừa|vâng|dạ|rồi|"
    r"chốt(?:\s+(?:kèo|nhé|vậy))?|"
    r"yes|yeah|yep|sure|agreed|deal|sounds\s+good|works\s+for\s+me|got\s+it|understood"
)
_CONFIRMATION_PREFIX = re.compile(rf"^(?:{_CONFIRMATION_CORE})\b", re.IGNORECASE)

# How far back to look for the proposal a short reply might be confirming - bounded so a chain of
# replies in a group ("ok" / "ok" / "chốt nhé") can still reach the original message, without an
# unbounded scan. Uses the existing index on Message.conversation_id, not a full table scan.
_LOOKBACK_LIMIT = 8


def _looks_like_commitment(text: str) -> bool:
    return bool(_SIGNAL_PATTERN.search(text))


def _might_be_confirmation(text: str) -> bool:
    """Loose signal that `text` might be agreeing to something proposed earlier - only requires the
    message to START WITH an agreement word, not consist entirely of one. False positives (e.g.
    "rồi giờ tính sao đây") are cheap: still needs a matching proposal in _find_recent_proposal, and
    the LLM keeps the final say via has_commitment."""
    stripped = text.strip()
    if not stripped or len(stripped) > 60:  # real confirmations are short; also bounds regex work
        return False
    return bool(_CONFIRMATION_PREFIX.match(stripped))


async def _find_recent_proposal(db: AsyncSession, *, conversation_id: str, sender_id: str) -> Message | None:
    """Scan the last _LOOKBACK_LIMIT messages of this conversation for the most recent one sent by
    someone OTHER than sender_id that itself looks like a commitment - the proposal `sender_id`'s
    short reply is presumably confirming. Doesn't assume anything about which row is "the current
    message" (deliberately - see maybe_suggest_task's caller, where the reply may already have
    other messages after it by the time this background check runs)."""
    rows = (
        await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(_LOOKBACK_LIMIT)
        )
    ).scalars().all()
    for msg in rows:
        if msg.sender_id != sender_id and _looks_like_commitment(msg.content):
            return msg
    return None


def _build_confirmation_prompt(*, prior_content: str, confirmation_content: str, now: datetime, tz_name: str) -> str:
    return (
        "Two messages were just exchanged in a team chat app. The FIRST message below proposed a "
        "personal commitment, appointment, or deadline. The SECOND message is a short reply from a "
        "DIFFERENT person confirming/agreeing to it. Decide whether, by replying that way, the "
        "person who sent the SECOND message now also has that same commitment for themself - "
        "something worth reminding THEM about later. Output ONLY JSON, no prose, no markdown code "
        'fence, with exactly these keys: "has_commitment" (boolean), "title" (short string in '
        "Vietnamese - tiếng Việt, describing the commitment from the confirming person's own point "
        'of view), "due_at" (ISO 8601 datetime string if a specific date/time was mentioned in '
        "either message, otherwise null - resolve relative dates/times against the current date and "
        f"time, which is {now.strftime('%A, %Y-%m-%d %H:%M')} ({tz_name})). If the reply is just a "
        'vague acknowledgement with no real shared commitment, output {"has_commitment": false}.\n\n'
        f"First message (proposal): {prior_content}\nSecond message (reply): {confirmation_content}"
    )


def _strip_fence(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


async def maybe_suggest_task(*, conversation_id: str, sender_id: str, content: str) -> None:
    """Best-effort, fire-and-forget: if a new message looks like it contains a personal
    commitment/appointment/deadline, ask the LLM to confirm and, if so, drop a 'suggested' Task
    (same review flow as the manual Extract tasks action) for the sender to Accept/Dismiss.
    Requires the sender to have granted AI permission for this conversation (ai_permissions) -
    silently skips otherwise, same as the explicit /chat endpoint. Never raises - a failure here
    must not affect message delivery.

    Also handles the shared-commitment case: if `content` itself has no signal but looks like a
    short confirmation reply ("ok", "chốt nhé", "ok 8h nhé"...), looks back a few messages for a
    commitment someone else proposed and, if found, asks the LLM whether the confirmer now also
    has that commitment for themself - still gated on the CONFIRMER's own ai_permissions grant
    (same per-viewer model as everywhere else), not the original proposer's.
    """
    has_signal = _looks_like_commitment(content)
    maybe_confirm = _might_be_confirmation(content)
    if not has_signal and not maybe_confirm:
        return

    try:
        async with db_session.async_session_maker() as db:
            permission = await chat_service.get_ai_permission(db, conversation_id, sender_id)
        if permission is None or not permission.granted:
            return

        # Ràng buộc đề bài: tối ưu chi phí - đây là lệnh gọi LLM tự động chạy nền trên MỌI tin
        # nhắn mới (không phải người dùng chủ động bấm), nên là nơi cần chặn trước tiên khi đã
        # vượt ngân sách; bỏ qua lặng lẽ giống các điều kiện guard khác ở trên, không phải lỗi.
        if await usage_service.is_over_budget():
            return

        # Chỉ đọc nội dung các tin nhắn khác (để tìm đề xuất đang được xác nhận) SAU KHI đã xác
        # nhận sender_id có quyền AI cho conversation này - không đảo thứ tự, dù chưa gửi gì ra
        # LLM ở bước này.
        prior_message: Message | None = None
        if maybe_confirm:
            async with db_session.async_session_maker() as db:
                prior_message = await _find_recent_proposal(db, conversation_id=conversation_id, sender_id=sender_id)
        if prior_message is None and not has_signal:
            return

        # Dedup neo vào đề xuất đang được xác nhận, không phải 1 cửa sổ đồng hồ cố định: đúng cả
        # khi người này xác nhận cùng 1 đề xuất 2 lần ("ok" rồi "chốt nhé"), và không chặn nhầm khi
        # họ sau đó xác nhận 1 đề xuất KHÁC (Task cũ luôn có created_at cũ hơn đề xuất mới).
        if prior_message is not None:
            async with db_session.async_session_maker() as db:
                recent_dup = (
                    await db.execute(
                        select(Task)
                        .where(
                            Task.owner_id == sender_id,
                            Task.conversation_id == conversation_id,
                            Task.source == "proactive",
                            Task.status == "suggested",
                            Task.created_at >= prior_message.created_at,
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if recent_dup is not None:
                return

        settings = get_settings()
        llm = get_llm()
        # Without today's date, the LLM has to guess the current date from its training data when
        # resolving relative expressions ("hôm nay", "tối nay", "ngày mai") - observed in practice
        # to land on the wrong YEAR half the time (e.g. resolving "tối nay" to 2023 instead of the
        # real current year), silently producing a due_at years in the past. Same fix already
        # applied in planner_node.py/task_tool.py for the manual Extract tasks flow - mirror it here.
        now = datetime.now(ZoneInfo(settings.calendar_timezone))
        if prior_message is not None:
            prompt = _build_confirmation_prompt(
                prior_content=prior_message.content,
                confirmation_content=content,
                now=now,
                tz_name=settings.calendar_timezone,
            )
        else:
            prompt = (
                "A message was just sent in a team chat app. Decide whether it describes a personal "
                "commitment, appointment, or deadline for the person who sent it - something worth "
                "reminding them about later. Output ONLY JSON, no prose, no markdown code fence, with "
                'exactly these keys: "has_commitment" (boolean), "title" (short string in Vietnamese - '
                'tiếng Việt, only meaningful if has_commitment is true), "due_at" (ISO 8601 datetime '
                'string if a specific date/time was mentioned, otherwise null - resolve relative dates/'
                'times ("hôm nay", "ngày mai", "tuần sau") against the current date and time, which is '
                f"{now.strftime('%A, %Y-%m-%d %H:%M')} ({settings.calendar_timezone})). If unsure, or "
                "it's just casual conversation with no real commitment, output "
                '{"has_commitment": false}.\n\n'
                f"Message: {content}"
            )
        result = await llm.ainvoke(prompt)
        await usage_service.log_usage(
            provider=settings.llm_provider, model=settings.model_name, usage_metadata=result.usage_metadata
        )
        data = json.loads(_strip_fence(result.content))
        if not data.get("has_commitment"):
            return

        due_at = None
        if data.get("due_at"):
            try:
                due_at = datetime.fromisoformat(data["due_at"])
                if due_at.tzinfo is None:
                    # LLM output has no UTC offset - treat it as Hanoi time, not naive/ambiguous.
                    due_at = due_at.replace(tzinfo=ZoneInfo(settings.calendar_timezone))
            except ValueError:
                due_at = None

        async with db_session.async_session_maker() as db:
            task = Task(
                owner_id=sender_id,
                conversation_id=conversation_id,
                title=(data.get("title") or content)[:200],
                due_at=due_at,
                priority="Medium",
                source="proactive",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)

        await manager.broadcast_to_users(
            [sender_id],
            {
                "type": "task_suggested",
                "task": {
                    "id": task.id,
                    "conversation_id": task.conversation_id,
                    "title": task.title,
                    "due_at": task.due_at.isoformat() if task.due_at else None,
                    "priority": task.priority,
                    "status": task.status,
                    "source": task.source,
                    "created_at": task.created_at.isoformat(),
                },
            },
        )
    except Exception:  # noqa: BLE001 - background detection must never break message delivery
        logger.exception("Proactive commitment detection failed")
