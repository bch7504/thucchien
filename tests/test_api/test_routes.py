from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch, fake_llm_factory):
    """Every route test gets a fake LLM by default so nothing hits a live OpenAI key."""
    reply = AIMessage(content="Mocked agent reply.")
    llm = fake_llm_factory([reply])
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)
    return llm


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_chat_requires_auth(client):
    response = await client.post("/api/v1/chat", json={"message": "hello"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_empty_message(client, auth_headers):
    response = await client.post("/api/v1/chat", json={"message": ""}, headers=auth_headers)
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_chat_rejects_conversation_id_caller_is_not_a_participant_of(
    client, auth_headers, other_auth_headers
):
    # A third user's conversation with other_auth_headers' user - auth_headers' user is in neither.
    third = await client.post(
        "/api/v1/auth/register",
        json={"email": "carol@example.com", "password": "password123", "display_name": "Carol"},
    )
    third_headers = {"Authorization": f"Bearer {third.json()['access_token']}"}
    other_me = await client.get("/api/v1/auth/me", headers=other_auth_headers)
    other_id = other_me.json()["id"]
    conv = await client.post(
        "/api/v1/conversations", json={"type": "direct", "participant_ids": [other_id]}, headers=third_headers
    )
    conversation_id = conv.json()["id"]

    # auth_headers' user was never added to this conversation - it belongs to third_headers and
    # other_auth_headers' user only.
    response = await client.post(
        "/api/v1/chat",
        json={"message": "Summarize this.", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_chat_rejects_conversation_id_when_ai_permission_not_granted(client, auth_headers, other_auth_headers):
    other_me = await client.get("/api/v1/auth/me", headers=other_auth_headers)
    other_id = other_me.json()["id"]
    conv = await client.post(
        "/api/v1/conversations", json={"type": "direct", "participant_ids": [other_id]}, headers=auth_headers
    )
    conversation_id = conv.json()["id"]

    # A participant, but the AI permission for this conversation was never granted - default deny.
    response = await client.post(
        "/api/v1/chat",
        json={"message": "Summarize this.", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_chat_allows_conversation_id_caller_is_a_participant_of(client, auth_headers, other_auth_headers):
    other_me = await client.get("/api/v1/auth/me", headers=other_auth_headers)
    other_id = other_me.json()["id"]
    conv = await client.post(
        "/api/v1/conversations", json={"type": "direct", "participant_ids": [other_id]}, headers=auth_headers
    )
    conversation_id = conv.json()["id"]
    await client.put(
        f"/api/v1/conversations/{conversation_id}/ai-permission", json={"granted": True}, headers=auth_headers
    )

    response = await client.post(
        "/api/v1/chat",
        json={"message": "Summarize this.", "conversation_id": conversation_id},
        headers=auth_headers,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_completed_response(client, auth_headers):
    response = await client.post("/api/v1/chat", json={"message": "hello"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["response"] == "Mocked agent reply."
    assert data["thread_id"]


@pytest.mark.asyncio
async def test_chat_surfaces_llm_error_instead_of_empty_response(client, auth_headers, monkeypatch):
    def broken_get_llm():
        raise RuntimeError("Rate limit reached")

    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", broken_get_llm)

    response = await client.post("/api/v1/chat", json={"message": "hello"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["response"] == "Rate limit reached"


@pytest.mark.asyncio
async def test_chat_uses_provided_messages_as_context(client, auth_headers, monkeypatch, fake_llm_factory):
    captured = {}
    reply = AIMessage(content="Summary done.")
    llm = fake_llm_factory([reply])

    async def ainvoke(messages):
        captured["messages"] = messages
        return reply

    llm.ainvoke = ainvoke
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    response = await client.post(
        "/api/v1/chat",
        json={
            "message": "Summarize this",
            "messages": [
                {"role": "user", "sender": "Alice", "content": "hi"},
                {"role": "user", "sender": "Bob", "content": "let's meet tomorrow"},
            ],
        },
        headers=auth_headers,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_chat_interrupts_and_resume_completes(client, auth_headers, monkeypatch, fake_llm_factory):
    from src.services import calendar_service

    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {"id": "evt-1"}
    monkeypatch.setattr(calendar_service, "get_calendar_service", lambda: fake_service)

    def _final_message(state):
        last = state["messages"][-1]
        assert isinstance(last, ToolMessage)
        return AIMessage(content=f"final:{last.content}")

    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_calendar_event",
                    "args": {
                        "summary": "Sync",
                        "start_iso": "2026-08-01T10:00:00",
                        "end_iso": "2026-08-01T10:30:00",
                    },
                    "id": "call_1",
                }
            ],
        )
    ]
    llm = fake_llm_factory(responses)
    real_ainvoke = llm.ainvoke

    async def ainvoke(messages):
        if llm._responses:
            return await real_ainvoke(messages)
        return _final_message({"messages": messages})

    llm.ainvoke = ainvoke
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    response = await client.post("/api/v1/chat", json={"message": "book a sync"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "interrupted"
    assert data["interrupt"]["type"] == "calendar_event"
    thread_id = data["thread_id"]

    resume_response = await client.post(
        "/api/v1/chat/resume", json={"thread_id": thread_id, "approved": True}, headers=auth_headers
    )
    assert resume_response.status_code == 200
    resume_data = resume_response.json()
    assert resume_data["status"] == "completed"
    assert "Event created" in resume_data["response"]


@pytest.mark.asyncio
async def test_chat_blocked_when_over_daily_token_budget(client, auth_headers, monkeypatch):
    from src.agents import graph as agent_graph
    from src.services import usage_service

    async def _over_budget():
        return True

    monkeypatch.setattr(usage_service, "is_over_budget", _over_budget)

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("agent graph must not run when over the daily token budget")

    monkeypatch.setattr(agent_graph.agent, "ainvoke", _must_not_run)

    response = await client.post("/api/v1/chat", json={"message": "hello"}, headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert "hạn mức" in data["response"]


@pytest.mark.asyncio
async def test_chat_resume_not_blocked_by_budget(client, auth_headers, monkeypatch, fake_llm_factory):
    """resume_chat() completes an action a human already approved (interrupt() confirm) - it must
    stay exempt from the budget block, or an approved reminder/calendar action could get stranded
    with no way to finish once the daily budget is hit mid-flow."""
    from src.services import calendar_service

    fake_service = MagicMock()
    fake_service.events.return_value.insert.return_value.execute.return_value = {"id": "evt-1"}
    monkeypatch.setattr(calendar_service, "get_calendar_service", lambda: fake_service)

    def _final_message(state):
        last = state["messages"][-1]
        return AIMessage(content=f"final:{last.content}")

    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "create_calendar_event",
                    "args": {
                        "summary": "Sync",
                        "start_iso": "2026-08-01T10:00:00",
                        "end_iso": "2026-08-01T10:30:00",
                    },
                    "id": "call_1",
                }
            ],
        )
    ]
    llm = fake_llm_factory(responses)
    real_ainvoke = llm.ainvoke

    async def ainvoke(messages):
        if llm._responses:
            return await real_ainvoke(messages)
        return _final_message({"messages": messages})

    llm.ainvoke = ainvoke
    monkeypatch.setattr("src.agents.nodes.planner_node.get_llm", lambda: llm)

    response = await client.post("/api/v1/chat", json={"message": "book a sync"}, headers=auth_headers)
    assert response.json()["status"] == "interrupted"
    thread_id = response.json()["thread_id"]

    from src.services import usage_service

    async def _over_budget():
        return True

    monkeypatch.setattr(usage_service, "is_over_budget", _over_budget)

    resume_response = await client.post(
        "/api/v1/chat/resume", json={"thread_id": thread_id, "approved": True}, headers=auth_headers
    )
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200
