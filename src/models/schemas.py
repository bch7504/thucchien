from typing import Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    sender: str | None = None
    content: str
    timestamp: str | None = Field(default=None, description="ISO 8601 datetime, optional")


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
