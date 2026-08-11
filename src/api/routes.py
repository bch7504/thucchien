from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents import graph as agent_graph
from src.auth.dependencies import get_current_user
from src.db.models import User
from src.db.session import get_db
from src.models.schemas import ChatMessage, ChatRequest, ChatResponse, InterruptPayload, ResumeRequest
from src.services import chat_service, usage_service

router = APIRouter()

# thread_id -> owner user id. In-memory only: enough to stop one user resuming another
# user's interrupted (unconfirmed calendar/reminder) run; doesn't need to survive a restart
# since thread_ids are random UUIDs nobody else can guess anyway.
_thread_owners: dict[str, str] = {}


def _check_thread_owner(thread_id: str, current_user: User) -> None:
    owner = _thread_owners.get(thread_id)
    if owner is not None and owner != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your conversation")


def _format_messages(messages: list[ChatMessage]) -> str:
    lines = []
    for m in messages:
        ts = chat_service.format_local_timestamp(m.timestamp) if m.timestamp else None
        who = f"{m.sender or m.role}" + (f" [{ts}]" if ts else "")
        lines.append(f"{who}: {m.content}")
    return "\n".join(lines)


def _build_chat_response(result: dict, thread_id: str) -> ChatResponse:
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        return ChatResponse(
            response="Please confirm the proposed action.",
            thread_id=thread_id,
            status="interrupted",
            interrupt=InterruptPayload(**payload),
        )

    error = result.get("error")
    if error:
        return ChatResponse(response=error, thread_id=thread_id, status="error")

    final_text = ""
    for m in reversed(result.get("messages", [])):
        # A trailing ToolMessage means graph.py routed straight to END after a "terminal" tool
        # (summarize_conversation/extract_tasks) - its content is already the final answer.
        if isinstance(m, (AIMessage, ToolMessage)) and m.content:
            final_text = m.content
            break
    return ChatResponse(response=final_text, thread_id=thread_id, status="completed")


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """Chat với AI agent."""
    if request.conversation_id:
        # Don't trust the client's word that `messages` came from a conversation it's allowed to
        # see - verify current_user is actually a participant before the agent processes them.
        await chat_service.assert_participant(db, request.conversation_id, current_user.id)
        # Being a participant isn't consent for the AI to read this conversation - that's a
        # separate, explicit per-user grant (AIPanel's Grant/Revoke permission toggle).
        await chat_service.assert_ai_permission(db, request.conversation_id, current_user.id)

    thread_id = request.thread_id or str(uuid4())
    _check_thread_owner(thread_id, current_user)

    # Ràng buộc đề bài: "tối ưu chi phí" - chặn hẳn cuộc gọi LLM mới (không chỉ cảnh báo) một khi
    # đã chạm daily_token_budget. Chỉ áp dụng cho lượt chat MỚI - resume_chat() bên dưới cố tình
    # không chặn, vì nó hoàn tất một hành động con người đã bấm xác nhận rồi (human-in-the-loop),
    # chặn ở đó sẽ để interrupt() treo lơ lửng không cách nào hoàn tất hay huỷ.
    if await usage_service.is_over_budget():
        return ChatResponse(
            response=(
                "Đã vượt hạn mức token/chi phí AI hôm nay. Vui lòng thử lại vào ngày mai hoặc "
                "liên hệ admin để tăng hạn mức."
            ),
            thread_id=thread_id,
            status="error",
        )

    _thread_owners.setdefault(thread_id, current_user.id)
    config = {"configurable": {"thread_id": thread_id}}
    if request.conversation_id and request.scope:
        # Server-resolved scope takes priority over any client-supplied `messages` - the whole
        # point of `scope` is to not trust the client's already-loaded (at most 50) messages.
        scoped = await chat_service.get_scoped_messages(db, request.conversation_id, current_user.id, request.scope)
        context_text = _format_messages(scoped) if scoped else ""
    else:
        context_text = _format_messages(request.messages) if request.messages else ""
    inputs = {
        "messages": [HumanMessage(content=request.message)],
        "context": context_text,
        "user_id": current_user.id,
        "conversation_id": request.conversation_id,
    }
    try:
        result = await agent_graph.agent.ainvoke(inputs, config)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _build_chat_response(result, thread_id)


@router.post("/chat/resume", response_model=ChatResponse)
async def resume_chat(request: ResumeRequest, current_user: User = Depends(get_current_user)) -> ChatResponse:
    """Resume an interrupted agent run with the user's confirm/reject decision."""
    _check_thread_owner(request.thread_id, current_user)
    config = {"configurable": {"thread_id": request.thread_id}}
    try:
        result = await agent_graph.agent.ainvoke(
            Command(resume={"approved": request.approved, "edits": request.edits}), config
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return _build_chat_response(result, request.thread_id)


@router.get("/status")
async def agent_status():
    """Kiểm tra trạng thái agent."""
    return {"status": "ready", "agent": "LangGraph Agent v1.0"}
