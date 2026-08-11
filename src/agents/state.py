from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """State schema cho LangGraph agent.

    Mỗi node đọc và ghi vào state này.
    total=False cho phép tất cả fields là optional.
    """

    query: str
    context: str
    analysis: str
    response: str
    error: str
    metadata: dict
    user_id: str | None  # id of the user driving this run, for tools that push WS updates to them
    # id of the real 1-1/group conversation this run is about, if any - for tools scoped to it
    # (e.g. search_messages). Only ever set in routes.py from the already-authorized
    # request.conversation_id, never LLM-supplied - same trust model as user_id above.
    conversation_id: str | None

    # Tool-calling planner loop (messages, ToolNode, tools_condition all require this).
    messages: Annotated[list[AnyMessage], add_messages]

    # Structured outputs of the summarize/calendar/reminder tools.
    summary: str
    calendar_event_draft: dict | None
    reminder_draft: dict | None
