from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.auth.dependencies import get_current_user
from src.models.calendar_schemas import CalendarEventCreateRequest, CalendarEventOut, CalendarEventUpdateRequest
from src.services import calendar_service

router = APIRouter(dependencies=[Depends(get_current_user)])


def _to_out(event: dict) -> CalendarEventOut:
    return CalendarEventOut(**calendar_service.to_out_dict(event))


@router.get("/calendar/events", response_model=list[CalendarEventOut])
async def list_events(
    time_min: str | None = Query(default=None),
    time_max: str | None = Query(default=None),
) -> list[CalendarEventOut]:
    now = datetime.now(UTC)
    time_min = time_min or now.isoformat()
    time_max = time_max or (now + timedelta(days=60)).isoformat()
    try:
        items = calendar_service.list_events(time_min, time_max, max_results=100)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}")
    return [_to_out(e) for e in items]


@router.post("/calendar/events", response_model=CalendarEventOut, status_code=status.HTTP_201_CREATED)
async def create_event(request: CalendarEventCreateRequest) -> CalendarEventOut:
    try:
        created = calendar_service.create_event(
            summary=request.summary,
            start_iso=request.start_iso,
            end_iso=request.end_iso,
            description=request.description,
            attendees=request.attendees,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}")
    out = _to_out(created)
    await calendar_service.broadcast_change("calendar_event_created", {"event": out.model_dump()})
    return out


@router.patch("/calendar/events/{event_id}", response_model=CalendarEventOut)
async def update_event(event_id: str, request: CalendarEventUpdateRequest) -> CalendarEventOut:
    try:
        updated = calendar_service.update_event(
            event_id=event_id,
            summary=request.summary,
            start_iso=request.start_iso,
            end_iso=request.end_iso,
            description=request.description,
        )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}")
    out = _to_out(updated)
    await calendar_service.broadcast_change("calendar_event_updated", {"event": out.model_dump()})
    return out


@router.delete("/calendar/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: str) -> None:
    try:
        calendar_service.delete_event(event_id)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Google Calendar error: {e}")
    await calendar_service.broadcast_change("calendar_event_deleted", {"event_id": event_id})
