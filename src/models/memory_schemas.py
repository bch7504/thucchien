from datetime import datetime

from pydantic import BaseModel, Field


class MemoryOut(BaseModel):
    id: str
    category: str
    title: str
    detail: str
    created_at: datetime


class MemoryCreateRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=40)
    title: str = Field(..., min_length=1, max_length=200)
    detail: str = ""


class MemoryUpdateRequest(BaseModel):
    category: str | None = Field(default=None, min_length=1, max_length=40)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    detail: str | None = None
