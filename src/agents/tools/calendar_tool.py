from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from langgraph.types import interrupt

from src.agents.state import AgentState
from src.services import calendar_service
from src.services.google_credentials import CalendarNotConnected

_NOT_CONNECTED_MSG = (
    "This account hasn't connected Google Calendar yet - tell them to go to the Calendar page and "
    "click 'Connect Google Calendar' first."
)


@tool
async def create_calendar_event(
    summary: str,
    start_iso: str,
    end_iso: str,
    description: str = "",
    attendees: list[str] | None = None,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Draft a Google Calendar event. Requires the user's explicit confirmation before it is
    actually created.

    Args:
        summary: Event title.
        start_iso: Event start time as an ISO 8601 datetime string.
        end_iso: Event end time as an ISO 8601 datetime string.
        description: Optional event details.
        attendees: Optional list of attendee email addresses.
    """
    draft = {
        "summary": summary,
        "start": start_iso,
        "end": end_iso,
        "description": description,
        "attendees": attendees or [],
    }
    decision = interrupt({"type": "calendar_event", "draft": draft})
    if not decision or not decision.get("approved"):
        return "Calendar event was not created (user declined)."

    user_id = (state or {}).get("user_id")
    draft.update(decision.get("edits") or {})
    try:
        created = await calendar_service.create_event(
            user_id,
            summary=draft["summary"],
            start_iso=draft["start"],
            end_iso=draft["end"],
            description=draft["description"],
            attendees=draft["attendees"],
        )
    except CalendarNotConnected:
        return _NOT_CONNECTED_MSG
    await calendar_service.broadcast_change(
        user_id, "calendar_event_created", {"event": calendar_service.to_out_dict(created)}
    )
    return f"Event created: {created.get('htmlLink', created.get('id'))}"


@tool
async def list_calendar_events(
    time_min_iso: str,
    time_max_iso: str,
    max_results: int = 10,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """List existing calendar events in a time range. Read-only, no confirmation needed.

    Args:
        time_min_iso: Start of the range as an ISO 8601 datetime string.
        time_max_iso: End of the range as an ISO 8601 datetime string.
        max_results: Maximum number of events to return.
    """
    user_id = (state or {}).get("user_id")
    try:
        items = await calendar_service.list_events(user_id, time_min_iso, time_max_iso, max_results)
    except CalendarNotConnected:
        return _NOT_CONNECTED_MSG
    if not items:
        return "No events found in that range."
    return "\n".join(
        f"- {e.get('summary')} (id={e.get('id')}, {e['start'].get('dateTime', e['start'].get('date'))})"
        for e in items
    )


@tool
async def update_calendar_event(
    event_id: str,
    summary: str | None = None,
    start_iso: str | None = None,
    end_iso: str | None = None,
    description: str | None = None,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Draft changes to an existing Google Calendar event (found via list_calendar_events).
    Requires the user's explicit confirmation before they take effect. Only pass the fields
    that should change; the rest stay as-is.

    Args:
        event_id: The id of the event to update, from list_calendar_events.
        summary: New title, if changing.
        start_iso: New start time as an ISO 8601 datetime string, if changing.
        end_iso: New end time as an ISO 8601 datetime string, if changing.
        description: New details, if changing.
    """
    draft = {"event_id": event_id, "summary": summary, "start": start_iso, "end": end_iso, "description": description}
    decision = interrupt({"type": "calendar_event_update", "draft": draft})
    if not decision or not decision.get("approved"):
        return "Calendar event was not updated (user declined)."

    user_id = (state or {}).get("user_id")
    draft.update(decision.get("edits") or {})
    try:
        updated = await calendar_service.update_event(
            user_id,
            event_id=draft["event_id"],
            summary=draft["summary"],
            start_iso=draft["start"],
            end_iso=draft["end"],
            description=draft["description"],
        )
    except CalendarNotConnected:
        return _NOT_CONNECTED_MSG
    await calendar_service.broadcast_change(
        user_id, "calendar_event_updated", {"event": calendar_service.to_out_dict(updated)}
    )
    return f"Event updated: {updated.get('htmlLink', updated.get('id'))}"


@tool
async def delete_calendar_event(
    event_id: str, state: Annotated[AgentState, InjectedState] = None  # type: ignore[assignment]
) -> str:
    """Draft the deletion of an existing Google Calendar event (found via list_calendar_events).
    Requires the user's explicit confirmation before it is actually deleted.

    Args:
        event_id: The id of the event to delete, from list_calendar_events.
    """
    decision = interrupt({"type": "calendar_event_delete", "draft": {"event_id": event_id}})
    if not decision or not decision.get("approved"):
        return "Calendar event was not deleted (user declined)."

    user_id = (state or {}).get("user_id")
    try:
        await calendar_service.delete_event(user_id, event_id)
    except CalendarNotConnected:
        return _NOT_CONNECTED_MSG
    await calendar_service.broadcast_change(user_id, "calendar_event_deleted", {"event_id": event_id})
    return "Event deleted."
