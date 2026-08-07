import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from src.config import get_settings
from src.db import session as db_session
from src.db.models import UsageLog, User
from src.websocket.manager import manager

logger = logging.getLogger(__name__)

# Edge-triggered alert thresholds (percent of daily_token_budget). "Edge-triggered" so admins get
# one push per crossing per day, not one on every single request once already over the line.
_WARNING_PCT = 80
_EXCEEDED_PCT = 100


def _midnight_local_as_utc() -> datetime:
    tz = ZoneInfo(get_settings().calendar_timezone)
    return datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


async def log_usage(*, provider: str, model: str, usage_metadata: dict | None) -> None:
    """Best-effort token usage logging. Never lets a logging failure break the chat turn."""
    if not usage_metadata:
        return
    try:
        tokens = usage_metadata.get("total_tokens", 0)
        async with db_session.async_session_maker() as db:
            before_result = await db.execute(
                select(func.coalesce(func.sum(UsageLog.total_tokens), 0)).where(
                    UsageLog.created_at >= _midnight_local_as_utc()
                )
            )
            before_tokens = before_result.scalar_one()
            db.add(
                UsageLog(
                    provider=provider,
                    model=model,
                    prompt_tokens=usage_metadata.get("input_tokens", 0),
                    completion_tokens=usage_metadata.get("output_tokens", 0),
                    total_tokens=tokens,
                )
            )
            await db.commit()
        await _maybe_alert_budget(before_tokens=before_tokens, after_tokens=before_tokens + tokens)
    except Exception:  # noqa: BLE001 - usage tracking must never break the agent turn
        logger.exception("Failed to log LLM usage")


async def _maybe_alert_budget(*, before_tokens: int, after_tokens: int) -> None:
    """Push a WebSocket alert to every connected admin the moment today's usage crosses 80% or
    100% of daily_token_budget - so it surfaces wherever an admin already is in the app, not only
    when they happen to open the Admin dashboard (see ROADMAP.md, mục 'Cảnh báo token/chi phí')."""
    budget = get_settings().daily_token_budget
    if not budget:
        return
    before_pct = before_tokens / budget * 100
    after_pct = after_tokens / budget * 100
    if before_pct < _EXCEEDED_PCT <= after_pct:
        level = "exceeded"
    elif before_pct < _WARNING_PCT <= after_pct:
        level = "warning"
    else:
        return

    async with db_session.async_session_maker() as db:
        admin_ids = (await db.execute(select(User.id).where(User.role == "admin"))).scalars().all()
    if not admin_ids:
        return
    await manager.broadcast_to_users(
        list(admin_ids),
        {
            "type": "usage_budget_alert",
            "level": level,
            "tokens_used_today": after_tokens,
            "daily_token_budget": budget,
            "used_pct": round(after_pct, 1),
        },
    )


async def get_usage_today() -> dict:
    since = _midnight_local_as_utc()
    async with db_session.async_session_maker() as db:
        result = await db.execute(
            select(func.coalesce(func.sum(UsageLog.total_tokens), 0), func.count(UsageLog.id)).where(
                UsageLog.created_at >= since
            )
        )
        total_tokens, request_count = result.one()
    return {"total_tokens": total_tokens, "request_count": request_count, "since": since}


async def is_over_budget() -> bool:
    """True once today's usage has reached (not just approached) daily_token_budget. Used to
    block *new* LLM calls - never to interrupt one already in flight or a human-approved action
    that's just completing (see routes.py::resume_chat for why resume is exempt)."""
    budget = get_settings().daily_token_budget
    if not budget:
        return False
    usage = await get_usage_today()
    return usage["total_tokens"] >= budget
