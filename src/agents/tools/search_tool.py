from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from src.agents.state import AgentState
from src.db import session as db_session
from src.services import chat_service


@tool
async def search_messages(
    query: str,
    max_results: int = 20,
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Search this conversation's own message history for a keyword or phrase (case-insensitive
    substring match). Use this to find something mentioned earlier that isn't already in the
    conversation content provided above - e.g. a date, a name, a decision, a link - or to try to
    resolve who/what a vague reference in the user's request points to. Read-only, no
    confirmation needed. Only works inside a real 1-1/group conversation.

    Args:
        query: Word or phrase to search for.
        max_results: Maximum number of matching messages to return (most recent first).
    """
    # conversation_id comes from injected state (set server-side in routes.py from an already
    # participant/permission-checked request), never from an LLM-supplied argument - same
    # spoofing guard as user_id in calendar_tool.py/reminder_tool.py.
    conversation_id = (state or {}).get("conversation_id")
    if not conversation_id:
        return "Not inside a real conversation, so there is no message history to search."

    async with db_session.async_session_maker() as db:
        results = await chat_service.search_messages(db, conversation_id, query, max_results)

    if not results:
        return f"No messages found matching '{query}'."
    return "\n".join(
        f"- {m.sender} [{chat_service.format_local_timestamp(m.timestamp)}]: {m.content}" for m in results
    )
