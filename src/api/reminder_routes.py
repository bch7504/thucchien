from fastapi import APIRouter, Depends, HTTPException, status

from src.auth.dependencies import get_current_user
from src.db.models import User
from src.models.reminder_schemas import ReminderCreateRequest, ReminderOut
from src.services import reminder_service

router = APIRouter(dependencies=[Depends(get_current_user)])


@router.get("/reminders", response_model=list[ReminderOut])
async def list_reminders(current_user: User = Depends(get_current_user)) -> list[ReminderOut]:
    reminders = await reminder_service.list_reminders(owner_id=current_user.id)
    return [ReminderOut.model_validate(r, from_attributes=True) for r in reminders]


@router.post("/reminders", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    request: ReminderCreateRequest, current_user: User = Depends(get_current_user)
) -> ReminderOut:
    reminder = await reminder_service.schedule_reminder(
        owner_id=current_user.id,
        title=request.title,
        due_at_iso=request.due_at_iso,
        lead_minutes=request.lead_minutes,
        message=request.message,
        source="manual",
    )
    return ReminderOut.model_validate(reminder, from_attributes=True)


@router.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_reminder(reminder_id: str, current_user: User = Depends(get_current_user)) -> None:
    cancelled = await reminder_service.cancel_reminder(reminder_id, owner_id=current_user.id)
    if not cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found")
