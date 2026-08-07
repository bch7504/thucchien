from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.db.models import Conversation, ConversationParticipant, Message, User
from src.db.session import get_db
from src.models.auth_schemas import UserPublic
from src.models.chat_schemas import (
    AIPermissionOut,
    AIPermissionUpdateRequest,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationSummary,
    MessageListResponse,
    MessageOut,
    SendMessageRequest,
)
from src.services import chat_service, proactive_service
from src.websocket.manager import manager

router = APIRouter()


@router.get("/users", response_model=list[UserPublic])
async def list_users(
    search: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[UserPublic]:
    stmt = select(User).where(User.id != current_user.id)
    if search:
        pattern = f"%{search}%"
        stmt = stmt.where(or_(User.display_name.ilike(pattern), User.email.ilike(pattern)))
    users = (await db.execute(stmt)).scalars().all()
    return [UserPublic(id=u.id, email=u.email, display_name=u.display_name, role=u.role) for u in users]


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationListResponse:
    conversation_ids = (
        (
            await db.execute(
                select(ConversationParticipant.conversation_id).where(
                    ConversationParticipant.user_id == current_user.id
                )
            )
        )
        .scalars()
        .all()
    )
    conversations = (
        (
            await db.execute(
                select(Conversation)
                .where(Conversation.id.in_(conversation_ids))
                .order_by(Conversation.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    summaries = [await chat_service.build_conversation_summary(db, c, current_user.id) for c in conversations]
    return ConversationListResponse(conversations=summaries)


@router.post("/conversations", response_model=ConversationSummary)
async def create_conversation(
    request: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConversationSummary:
    if request.type == "direct":
        if len(request.participant_ids) != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Direct conversations need exactly one other participant",
            )
        conversation = await chat_service.get_or_create_direct_conversation(
            db, current_user.id, request.participant_ids[0]
        )
    else:
        if not request.name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Group conversations require a name")
        conversation = await chat_service.create_group_conversation(
            db, current_user.id, request.participant_ids, request.name
        )
    return await chat_service.build_conversation_summary(db, conversation, current_user.id)


@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def get_messages(
    conversation_id: str,
    before: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    await chat_service.assert_participant(db, conversation_id, current_user.id)

    stmt = (
        select(Message, User).join(User, User.id == Message.sender_id).where(Message.conversation_id == conversation_id)
    )
    if before:
        before_message = await db.get(Message, before)
        if before_message is not None:
            stmt = stmt.where(Message.created_at < before_message.created_at)
    stmt = stmt.order_by(Message.created_at.desc()).limit(limit + 1)

    rows = (await db.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    messages = [chat_service.serialize_message(m, u) for m, u in reversed(rows)]
    return MessageListResponse(messages=messages, has_more=has_more)


@router.post("/conversations/{conversation_id}/messages", response_model=MessageOut)
async def send_message(
    conversation_id: str,
    request: SendMessageRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageOut:
    await chat_service.assert_participant(db, conversation_id, current_user.id)
    message = await chat_service.create_message(db, conversation_id, current_user.id, request.content)
    message_out = chat_service.serialize_message(message, current_user)

    participant_ids = await chat_service.get_participant_ids(db, conversation_id)
    await manager.broadcast_to_users(participant_ids, {"type": "new_message", "message": message_out.model_dump()})
    background_tasks.add_task(
        proactive_service.maybe_suggest_task,
        conversation_id=conversation_id,
        sender_id=current_user.id,
        content=request.content,
    )
    return message_out


@router.get("/conversations/{conversation_id}/ai-permission", response_model=AIPermissionOut)
async def get_conversation_ai_permission(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AIPermissionOut:
    await chat_service.assert_participant(db, conversation_id, current_user.id)
    permission = await chat_service.get_ai_permission(db, conversation_id, current_user.id)
    if permission is None:
        return AIPermissionOut(conversation_id=conversation_id, granted=False, updated_at=None)
    return AIPermissionOut(
        conversation_id=conversation_id, granted=permission.granted, updated_at=permission.updated_at.isoformat()
    )


@router.put("/conversations/{conversation_id}/ai-permission", response_model=AIPermissionOut)
async def update_conversation_ai_permission(
    conversation_id: str,
    request: AIPermissionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AIPermissionOut:
    await chat_service.assert_participant(db, conversation_id, current_user.id)
    permission = await chat_service.set_ai_permission(db, conversation_id, current_user.id, request.granted)
    return AIPermissionOut(
        conversation_id=conversation_id, granted=permission.granted, updated_at=permission.updated_at.isoformat()
    )


@router.post("/conversations/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await chat_service.mark_read(db, conversation_id, current_user.id)
    return {"status": "ok"}
