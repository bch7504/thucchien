from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from src.agents.state import AgentState
from src.config import get_settings
from src.services.llm import get_llm


@tool
async def extract_tasks(
    state: Annotated[AgentState, InjectedState] = None,  # type: ignore[assignment]
) -> str:
    """Extract action items, tasks, and appointments mentioned in the conversation the user is
    currently asking about, as a JSON array."""
    text = (state or {}).get("context", "")
    if not text.strip():
        return "[]"

    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.calendar_timezone))
    llm = get_llm()
    prompt = (
        "Extract action items, tasks, and appointments mentioned in the following conversation. "
        "Output ONLY a JSON array, no prose, no markdown code fence. Each item must be an object "
        'with exactly these keys: "title" (string, written in Vietnamese - tiếng Việt), "due_at" '
        '(ISO 8601 datetime string, or null if no date/time was mentioned), "priority" (one of '
        '"High", "Medium", "Low" - keep these three values exactly as-is, in English). Resolve '
        "relative dates/times (\"tomorrow\", \"next Monday\", \"in an hour\") against the current "
        f"date and time, which is {now.strftime('%A, %Y-%m-%d %H:%M')} ({settings.calendar_timezone}). "
        "If nothing is found, output [].\n\n"
        f"{text}"
    )
    result = await llm.ainvoke(prompt)
    return result.content
