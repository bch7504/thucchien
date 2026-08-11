from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    sender: str | None = None
    content: str
    timestamp: str | None = Field(default=None, description="ISO 8601 datetime, optional")


class MessageScope(BaseModel):
    """How much of a real conversation's history to hand the agent, resolved server-side against
    the DB (see chat_service.get_scoped_messages) - the client only says WHAT it wants, never
    supplies the actual messages for this path, unlike the older `ChatRequest.messages` field."""

    kind: Literal["latest_n", "unread", "today", "yesterday", "this_week", "rolling_hours", "custom_range"]
    count: int | None = Field(default=None, ge=1, le=500, description="latest_n: how many, newest first")
    hours: int | None = Field(default=None, ge=1, le=24, description="rolling_hours: size of the trailing window")
    since: str | None = Field(default=None, description="custom_range: ISO datetime, naive = calendar_timezone")
    until: str | None = Field(default=None, description="custom_range: ISO datetime, naive = calendar_timezone")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000, description="Tin nhắn từ user")
    thread_id: str | None = Field(default=None, description="Conversation thread id; generated if omitted")
    messages: list[ChatMessage] | None = Field(
        default=None,
        description="Raw message history to summarize (read by summarize_conversation via state)",
    )
    conversation_id: str | None = Field(
        default=None,
        description=(
            "If `messages` come from a real 1-1/group chat conversation, its id - the server "
            "verifies the caller is actually a participant before letting the agent see them, "
            "rather than trusting whatever `messages` the client attached."
        ),
    )
    scope: MessageScope | None = Field(
        default=None,
        description=(
            "When set together with conversation_id, the server re-derives the message history "
            "from the DB according to this scope instead of trusting the client-supplied "
            "`messages` array - takes priority over `messages` when both are present."
        ),
    )


class InterruptPayload(BaseModel):
    type: Literal["calendar_event", "calendar_event_update", "calendar_event_delete", "reminder"]
    draft: dict


class ChatResponse(BaseModel):
    response: str = Field(default="", description="Phản hồi từ agent")
    analysis: str = Field(default="", description="Phân tích nội bộ")
    thread_id: str
    status: Literal["completed", "interrupted", "error"] = "completed"
    interrupt: InterruptPayload | None = None


class ResumeRequest(BaseModel):
    thread_id: str
    approved: bool
    edits: dict | None = None
