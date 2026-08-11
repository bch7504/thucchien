from src.agents.tools.calendar_tool import (
    create_calendar_event,
    delete_calendar_event,
    list_calendar_events,
    update_calendar_event,
)
from src.agents.tools.reminder_tool import create_reminder, list_reminders
from src.agents.tools.search_tool import search_messages
from src.agents.tools.summarize_tool import summarize_conversation
from src.agents.tools.task_tool import extract_tasks

# example_tool.py's search_knowledge/calculate are left as unwired reference examples, not part of
# the production tool set. search_knowledge is a vector-store stub (TODO, never implemented) - NOT
# the same thing as search_messages below, which does plain Postgres keyword search over real
# message history and IS wired in.
ALL_TOOLS = [
    summarize_conversation,
    extract_tasks,
    create_calendar_event,
    list_calendar_events,
    update_calendar_event,
    delete_calendar_event,
    create_reminder,
    list_reminders,
    search_messages,
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
    "search_messages",
]
