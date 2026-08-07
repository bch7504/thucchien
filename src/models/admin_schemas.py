from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from src.models.memory_schemas import MemoryOut
from src.models.reminder_schemas import ReminderOut
from src.models.task_schemas import TaskOut


class AdminUserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime


class AdminStats(BaseModel):
    total_users: int
    total_conversations: int
    total_messages: int
    new_users_last_7_days: int
    tokens_used_today: int
    requests_today: int
    daily_token_budget: int
    budget_used_pct: float


class UpdateRoleRequest(BaseModel):
    role: Literal["user", "admin"]


class UpdateStatusRequest(BaseModel):
    is_active: bool


class AdminConversationOut(BaseModel):
    id: str
    type: str
    name: str | None
    created_by: str
    created_at: datetime
    participant_count: int
    message_count: int


class AdminMessageOut(BaseModel):
    id: str
    sender_id: str
    sender_display_name: str
    content: str
    created_at: datetime


class AdminTaskOut(TaskOut):
    owner_id: str
    owner_email: str
    owner_display_name: str
    conversation_label: str | None


class AdminReminderOut(ReminderOut):
    owner_id: str | None
    owner_email: str | None
    owner_display_name: str | None


class AdminMemoryOut(MemoryOut):
    owner_id: str
    owner_email: str
    owner_display_name: str
