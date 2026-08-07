import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agents.graph import route_after_planner
from src.agents.nodes.planner_node import planner_node


@pytest.mark.asyncio
async def test_planner_node_appends_ai_message(monkeypatch, fake_llm_factory):
    reply = AIMessage(content="Hi there!")
    llm = fake_llm_factory([reply])
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    result = await planner_node({"messages": [HumanMessage(content="hello")]})

    assert result == {"messages": [reply]}


@pytest.mark.asyncio
async def test_planner_node_captures_llm_error(monkeypatch):
    def broken_get_llm():
        raise RuntimeError("boom")

    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", broken_get_llm)

    result = await planner_node({"messages": [HumanMessage(content="hello")]})

    assert result == {"error": "boom"}


def test_route_after_planner_ends_on_error():
    assert route_after_planner({"error": "boom", "messages": []}) == "__end__"


def test_route_after_planner_routes_to_tools_on_tool_call():
    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"name": "summarize_conversation", "args": {}, "id": "1"}]),
        ]
    }
    assert route_after_planner(state) == "tools"


def test_route_after_planner_ends_on_plain_reply():
    state = {"messages": [HumanMessage(content="hi"), AIMessage(content="done")]}
    assert route_after_planner(state) == "__end__"
