import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import get_settings
from src.db import session as db_session
from src.db.models import Task
from src.services import chat_service, usage_service
from src.services.llm import get_llm
from src.websocket.manager import manager

logger = logging.getLogger(__name__)

# Cheap pre-filter so we don't burn an LLM call on every "ok"/"thanks" message - only messages
# that at least look like they might mention a time/commitment go on to the real (LLM) check.
_SIGNAL_PATTERN = re.compile(
    r"tomorrow|tonight|next (mon|tue|wed|thu|fri|sat|sun)|deadline|due (date|by)|meeting|appointment|"
    r"remind|schedule|\d\s?(am|pm)|"
    r"ngày mai|tối nay|sáng mai|chiều mai|tuần sau|thứ (hai|ba|tư|năm|sáu|bảy)|chủ nhật|hạn chót|"
    r"cuộc họp|họp lúc|hẹn|nhắc (tôi|mình|nhở)|lịch|lúc \d|giờ \d",
    re.IGNORECASE,
)


def _looks_like_commitment(text: str) -> bool:
    return bool(_SIGNAL_PATTERN.search(text))


def _strip_fence(text: str) -> str:
    return text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()


async def maybe_suggest_task(*, conversation_id: str, sender_id: str, content: str) -> None:
    """Best-effort, fire-and-forget: if a new message looks like it contains a personal
    commitment/appointment/deadline, ask the LLM to confirm and, if so, drop a 'suggested' Task
    (same review flow as the manual Extract tasks action) for the sender to Accept/Dismiss.
    Requires the sender to have granted AI permission for this conversation (ai_permissions) -
    silently skips otherwise, same as the explicit /chat endpoint. Never raises - a failure here
    must not affect message delivery.
    """
    if not _looks_like_commitment(content):
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

        settings = get_settings()
        llm = get_llm()
        prompt = (
            "A message was just sent in a team chat app. Decide whether it describes a personal "
            "commitment, appointment, or deadline for the person who sent it - something worth "
            "reminding them about later. Output ONLY JSON, no prose, no markdown code fence, with "
            'exactly these keys: "has_commitment" (boolean), "title" (short string in Vietnamese - '
            'tiếng Việt, only meaningful if has_commitment is true), "due_at" (ISO 8601 datetime '
            "string if a specific date/time was mentioned, otherwise null). If unsure, or it's just "
            'casual conversation with no real commitment, output {"has_commitment": false}.\n\n'
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
