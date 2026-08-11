import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.config import get_settings
from src.db.models import Task, User
from src.db.session import get_db
from src.models.task_schemas import TaskCreateRequest, TaskOut, UpdateTaskStatusRequest
from src.services import calendar_service, reminder_service
from src.services.google_credentials import CalendarNotConnected
from src.websocket.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()

_PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


def _to_out(task: Task) -> TaskOut:
    return TaskOut(
        id=task.id,
        conversation_id=task.conversation_id,
        title=task.title,
        due_at=task.due_at,
        priority=task.priority,
        status=task.status,
        source=task.source,
        created_at=task.created_at,
    )


async def _get_own_task_or_404(task_id: str, current_user: User, db: AsyncSession) -> Task:
    task = (
        await db.execute(select(Task).where(Task.id == task_id, Task.owner_id == current_user.id))
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return task


@router.get("/tasks", response_model=list[TaskOut])
async def list_tasks(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[TaskOut]:
    tasks = (await db.execute(select(Task).where(Task.owner_id == current_user.id))).scalars().all()
    def _sort_key(t: Task) -> tuple[bool, float, int]:
        # .timestamp() gives a plain float to sort by - fine for a relative sort ordering.
        return (t.due_at is None, t.due_at.timestamp() if t.due_at else 0.0, _PRIORITY_RANK.get(t.priority, 1))

    tasks.sort(key=_sort_key)
    return [_to_out(t) for t in tasks]


@router.post("/tasks", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    request: TaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    due_at = request.due_at
    if due_at is not None and due_at.tzinfo is None:
        # Same ambiguity reminder_service/proactive_service already guard against: a naive due_at
        # (no UTC offset - e.g. AIPanel's "Extract tasks" posting the LLM's raw due_at straight
        # here) would otherwise let Postgres/asyncpg interpret it using the DB server's own session
        # timezone, which only happens to match calendar_timezone by coincidence on a given machine
        # (verified: local Postgres here defaults to Asia/Bangkok, not something this app controls) -
        # explicit is correct everywhere, not just on this machine.
        due_at = due_at.replace(tzinfo=ZoneInfo(get_settings().calendar_timezone))

    task = Task(
        owner_id=current_user.id,
        conversation_id=request.conversation_id,
        title=request.title,
        due_at=due_at,
        priority=request.priority,
        source=request.source,
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)
    out = _to_out(task)
    await manager.broadcast_to_users([current_user.id], {"type": "task_created", "task": out.model_dump(mode="json")})
    return out


async def _add_to_calendar_and_reminder(task: Task, owner_id: str) -> None:
    """A proactively-detected task with a due date, once explicitly Accepted, also gets a real
    Google Calendar event and a real Reminder - the Accept click is the human confirmation, so
    neither needs its own interrupt() step. Best-effort: the task itself must stay accepted even
    if Calendar/Reminder creation fails."""
    start_iso = task.due_at.isoformat()
    end_iso = (task.due_at + timedelta(minutes=30)).isoformat()
    try:
        event = await calendar_service.create_event(owner_id, summary=task.title, start_iso=start_iso, end_iso=end_iso)
        await calendar_service.broadcast_change(
            owner_id, "calendar_event_created", {"event": calendar_service.to_out_dict(event)}
        )
    except CalendarNotConnected:
        logger.info("Skipped auto-create calendar event for task %s - owner hasn't connected Calendar", task.id)
    except Exception:  # noqa: BLE001 - best-effort, must not block the task Accept
        logger.exception("Auto-create calendar event for accepted task %s failed", task.id)

    try:
        await reminder_service.schedule_reminder(
            owner_id=owner_id, title=task.title, due_at_iso=start_iso, lead_minutes=30, source="proactive"
        )
    except Exception:  # noqa: BLE001 - best-effort, must not block the task Accept
        logger.exception("Auto-create reminder for accepted task %s failed", task.id)


@router.patch("/tasks/{task_id}/status", response_model=TaskOut)
async def update_task_status(
    task_id: str,
    request: UpdateTaskStatusRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await _get_own_task_or_404(task_id, current_user, db)
    is_accepting_proactive_schedule = (
        task.status == "suggested"
        and request.status == "pending"
        and task.source == "proactive"
        and task.due_at is not None
    )
    task.status = request.status
    await db.commit()
    await db.refresh(task)
    out = _to_out(task)
    await manager.broadcast_to_users([current_user.id], {"type": "task_updated", "task": out.model_dump(mode="json")})

    if is_accepting_proactive_schedule:
        await _add_to_calendar_and_reminder(task, current_user.id)

    return out


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    task = await _get_own_task_or_404(task_id, current_user, db)
    await db.delete(task)
    await db.commit()
    await manager.broadcast_to_users([current_user.id], {"type": "task_deleted", "task_id": task_id})
