from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AIPermission, Conversation, ConversationParticipant, Message, User
from src.models.auth_schemas import UserPublic
from src.models.chat_schemas import ConversationSummary, MessageOut


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def serialize_message(message: Message, sender: User) -> MessageOut:
    return MessageOut(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        sender_name=sender.display_name,
        content=message.content,
        created_at=_iso(message.created_at),
    )


async def assert_participant(db: AsyncSession, conversation_id: str, user_id: str) -> None:
    participant = (
        await db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant of this conversation")


async def get_participant_ids(db: AsyncSession, conversation_id: str) -> list[str]:
    rows = await db.execute(
        select(ConversationParticipant.user_id).where(ConversationParticipant.conversation_id == conversation_id)
    )
    return [r[0] for r in rows.all()]


async def get_ai_permission(db: AsyncSession, conversation_id: str, user_id: str) -> AIPermission | None:
    return (
        await db.execute(
            select(AIPermission).where(
                AIPermission.conversation_id == conversation_id,
                AIPermission.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def set_ai_permission(db: AsyncSession, conversation_id: str, user_id: str, granted: bool) -> AIPermission:
    permission = await get_ai_permission(db, conversation_id, user_id)
    if permission is None:
        permission = AIPermission(conversation_id=conversation_id, user_id=user_id, granted=granted)
        db.add(permission)
    else:
        permission.granted = granted
    await db.commit()
    await db.refresh(permission)
    return permission


async def assert_ai_permission(db: AsyncSession, conversation_id: str, user_id: str) -> None:
    permission = await get_ai_permission(db, conversation_id, user_id)
    if permission is None or not permission.granted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="AI permission not granted for this conversation"
        )


async def create_message(db: AsyncSession, conversation_id: str, sender_id: str, content: str) -> Message:
    message = Message(conversation_id=conversation_id, sender_id=sender_id, content=content)
    db.add(message)
    conversation = await db.get(Conversation, conversation_id)
    conversation.updated_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(message)
    return message


async def mark_read(db: AsyncSession, conversation_id: str, user_id: str) -> None:
    participant = (
        await db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a participant of this conversation")
    participant.last_read_at = datetime.now(UTC)
    await db.commit()


async def get_or_create_direct_conversation(db: AsyncSession, user_a_id: str, user_b_id: str) -> Conversation:
    candidate_ids = (
        (
            await db.execute(
                select(ConversationParticipant.conversation_id)
                .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
                .where(Conversation.type == "direct", ConversationParticipant.user_id == user_a_id)
            )
        )
        .scalars()
        .all()
    )
    for cid in candidate_ids:
        participant_ids = await get_participant_ids(db, cid)
        if set(participant_ids) == {user_a_id, user_b_id}:
            return await db.get(Conversation, cid)

    conversation = Conversation(type="direct", name=None, created_by=user_a_id)
    db.add(conversation)
    await db.flush()
    db.add(ConversationParticipant(conversation_id=conversation.id, user_id=user_a_id))
    db.add(ConversationParticipant(conversation_id=conversation.id, user_id=user_b_id))
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def create_group_conversation(
    db: AsyncSession, creator_id: str, member_ids: list[str], name: str
) -> Conversation:
    conversation = Conversation(type="group", name=name, created_by=creator_id)
    db.add(conversation)
    await db.flush()
    all_member_ids = {creator_id, *member_ids}
    for member_id in all_member_ids:
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=member_id))
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def build_conversation_summary(
    db: AsyncSession, conversation: Conversation, current_user_id: str
) -> ConversationSummary:
    participant_rows = (
        await db.execute(
            select(User, ConversationParticipant)
            .join(ConversationParticipant, ConversationParticipant.user_id == User.id)
            .where(ConversationParticipant.conversation_id == conversation.id)
        )
    ).all()
    participants = [
        UserPublic(id=u.id, email=u.email, display_name=u.display_name, role=u.role) for u, _ in participant_rows
    ]
    my_participant = next((cp for _, cp in participant_rows if cp.user_id == current_user_id), None)

    if conversation.type == "group":
        name = conversation.name or "Group"
    else:
        other = next((u for u, _ in participant_rows if u.id != current_user_id), None)
        name = other.display_name if other else "Direct message"

    last_message_row = (
        await db.execute(
            select(Message, User)
            .join(User, User.id == Message.sender_id)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).first()
    last_message = serialize_message(last_message_row[0], last_message_row[1]) if last_message_row else None

    unread_count = 0
    if my_participant is not None:
        unread_count = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id == conversation.id,
                    Message.created_at > my_participant.last_read_at,
                    Message.sender_id != current_user_id,
                )
            )
        ).scalar_one()

    return ConversationSummary(
        id=conversation.id,
        type=conversation.type,
        name=name,
        participants=participants,
        last_message=last_message,
        unread_count=unread_count,
        updated_at=_iso(conversation.updated_at),
    )
