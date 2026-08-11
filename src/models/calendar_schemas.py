from datetime import datetime

from pydantic import BaseModel, Field


class CalendarEventOut(BaseModel):
    id: str
    title: str
    start: str
    end: str | None
    url: str | None


class CalendarEventCreateRequest(BaseModel):
    summary: str = Field(..., min_length=1, max_length=200)
    start_iso: str
    end_iso: str
    description: str = ""
    attendees: list[str] | None = None


class CalendarEventUpdateRequest(BaseModel):
    summary: str | None = Field(default=None, min_length=1, max_length=200)
    start_iso: str | None = None
    end_iso: str | None = None
    description: str | None = None


class CalendarConnectionStatusOut(BaseModel):
    connected: bool
    google_email: str | None = None
    connected_at: datetime | None = None
