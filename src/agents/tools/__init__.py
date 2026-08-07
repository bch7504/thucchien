from src.agents.tools.calendar_tool import (
    create_calendar_event,
    delete_calendar_event,
    list_calendar_events,
    update_calendar_event,
)
from src.agents.tools.reminder_tool import create_reminder, list_reminders
from src.agents.tools.summarize_tool import summarize_conversation
from src.agents.tools.task_tool import extract_tasks

# example_tool.py's search_knowledge/calculate are left as unwired reference examples,
# not part of the production tool set.
ALL_TOOLS = [
    summarize_conversation,
    extract_tasks,
    create_calendar_event,
    list_calendar_events,
    update_calendar_event,
    delete_calendar_event,
    create_reminder,
    list_reminders,
]

__all__ = [
    "ALL_TOOLS",
    "summarize_conversation",
    "extract_tasks",
    "create_calendar_event",
    "list_calendar_events",
    "update_calendar_event",
    "delete_calendar_event",
    "create_reminder",
    "list_reminders",
]
