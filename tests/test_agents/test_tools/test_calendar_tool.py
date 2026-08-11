from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from src.agents import graph as agent_graph
from src.services import calendar_service

_USER_ID = "test-user-calendar-tool"


def _config():
    return {"configurable": {"thread_id": str(uuid4())}}


def _script_tool_call(fake_llm_factory, tool_name: str, args: dict):
    """First planner turn calls the tool; second turn (after the ToolMessage) replies."""

    def _final_message(state):
        last = state["messages"][-1]
        assert isinstance(last, ToolMessage)
        return AIMessage(content=f"final:{last.content}")

    responses = [
        AIMessage(content="", tool_calls=[{"name": tool_name, "args": args, "id": "call_1"}]),
    ]
    llm = fake_llm_factory(responses)
    real_ainvoke = llm.ainvoke

    async def ainvoke(messages):
        if llm._responses:
            return await real_ainvoke(messages)
        return _final_message({"messages": messages})

    llm.ainvoke = ainvoke
    return llm


def _mock_service(monkeypatch, fake_service):
    async def _fake(user_id):
        return fake_service

    monkeypatch.setattr(calendar_service, "_service", _fake)


@pytest.mark.asyncio
async def test_create_calendar_event_interrupts_then_creates(monkeypatch, fake_llm_factory):
    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {
        "id": "evt-abc",
        "htmlLink": "https://calendar.google.com/event?eid=abc",
    }
    _mock_service(monkeypatch, fake_service)

    llm = _script_tool_call(
        fake_llm_factory,
        "create_calendar_event",
        {"summary": "Launch review", "start_iso": "2026-08-01T11:00:00", "end_iso": "2026-08-01T12:00:00"},
    )
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    config = _config()
    result = await agent_graph.agent.ainvoke(
        {"messages": [HumanMessage(content="schedule the review")], "user_id": _USER_ID}, config
    )

    interrupts = result.get("__interrupt__")
    assert interrupts is not None
    assert interrupts[0].value["type"] == "calendar_event"
    assert interrupts[0].value["draft"]["summary"] == "Launch review"
    fake_service.events.return_value.insert.assert_not_called()

    result2 = await agent_graph.agent.ainvoke(Command(resume={"approved": True}), config)
    final = result2["messages"][-1]
    assert "Event created" in final.content
    fake_service.events.return_value.insert.assert_called_once()


@pytest.mark.asyncio
async def test_create_calendar_event_declined(monkeypatch, fake_llm_factory):
    fake_service = MagicMock()
    _mock_service(monkeypatch, fake_service)

    llm = _script_tool_call(
        fake_llm_factory,
        "create_calendar_event",
        {"summary": "Launch review", "start_iso": "2026-08-01T11:00:00", "end_iso": "2026-08-01T12:00:00"},
    )
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    config = _config()
    await agent_graph.agent.ainvoke(
        {"messages": [HumanMessage(content="schedule the review")], "user_id": _USER_ID}, config
    )

    result2 = await agent_graph.agent.ainvoke(Command(resume={"approved": False}), config)
    final = result2["messages"][-1]
    assert "not created" in final.content
    fake_service.events.return_value.insert.assert_not_called()


@pytest.mark.asyncio
async def test_create_calendar_event_not_connected(monkeypatch, fake_llm_factory):
    """No GoogleCalendarCredential row for this user - real (unmocked) credential-resolution path
    should surface as a friendly tool message, not a crash."""
    llm = _script_tool_call(
        fake_llm_factory,
        "create_calendar_event",
        {"summary": "Launch review", "start_iso": "2026-08-01T11:00:00", "end_iso": "2026-08-01T12:00:00"},
    )
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    config = _config()
    await agent_graph.agent.ainvoke(
        {"messages": [HumanMessage(content="schedule the review")], "user_id": "user-with-no-calendar"}, config
    )

    result2 = await agent_graph.agent.ainvoke(Command(resume={"approved": True}), config)
    final = result2["messages"][-1]
    assert "hasn't connected Google Calendar" in final.content


@pytest.mark.asyncio
async def test_update_calendar_event_interrupts_then_updates(monkeypatch, fake_llm_factory):
    fake_service = MagicMock()
    fake_service.events.return_value.patch.return_value.execute.return_value = {
        "id": "evt-1", "summary": "Launch review (moved)", "htmlLink": "https://calendar.google.com/event?eid=evt1",
    }
    _mock_service(monkeypatch, fake_service)

    llm = _script_tool_call(
        fake_llm_factory, "update_calendar_event", {"event_id": "evt-1", "start_iso": "2026-08-01T15:00:00"}
    )
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    config = _config()
    result = await agent_graph.agent.ainvoke(
        {"messages": [HumanMessage(content="move the review")], "user_id": _USER_ID}, config
    )

    interrupts = result.get("__interrupt__")
    assert interrupts is not None
    assert interrupts[0].value["type"] == "calendar_event_update"
    fake_service.events.return_value.patch.assert_not_called()

    result2 = await agent_graph.agent.ainvoke(Command(resume={"approved": True}), config)
    final = result2["messages"][-1]
    assert "Event updated" in final.content
    fake_service.events.return_value.patch.assert_called_once()


@pytest.mark.asyncio
async def test_delete_calendar_event_interrupts_then_deletes(monkeypatch, fake_llm_factory):
    fake_service = MagicMock()
    fake_service.events.return_value.delete.return_value.execute.return_value = {}
    _mock_service(monkeypatch, fake_service)

    llm = _script_tool_call(fake_llm_factory, "delete_calendar_event", {"event_id": "evt-1"})
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    config = _config()
    result = await agent_graph.agent.ainvoke(
        {"messages": [HumanMessage(content="cancel the review")], "user_id": _USER_ID}, config
    )

    interrupts = result.get("__interrupt__")
    assert interrupts is not None
    assert interrupts[0].value["type"] == "calendar_event_delete"
    fake_service.events.return_value.delete.assert_not_called()

    result2 = await agent_graph.agent.ainvoke(Command(resume={"approved": True}), config)
    final = result2["messages"][-1]
    assert "deleted" in final.content.lower()
    fake_service.events.return_value.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_calendar_event_declined(monkeypatch, fake_llm_factory):
    fake_service = MagicMock()
    _mock_service(monkeypatch, fake_service)

    llm = _script_tool_call(fake_llm_factory, "delete_calendar_event", {"event_id": "evt-1"})
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    config = _config()
    await agent_graph.agent.ainvoke(
        {"messages": [HumanMessage(content="cancel the review")], "user_id": _USER_ID}, config
    )

    result2 = await agent_graph.agent.ainvoke(Command(resume={"approved": False}), config)
    final = result2["messages"][-1]
    assert "not deleted" in final.content
    fake_service.events.return_value.delete.assert_not_called()
