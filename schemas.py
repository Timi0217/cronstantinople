from __future__ import annotations
import datetime as dt
from typing import Optional
from pydantic import BaseModel, Field


class ScheduleCreate(BaseModel):
    name: str = Field(..., description="Human-readable name, e.g. 'Monday standup reminder'")
    description: str = Field("", description="Optional longer description")
    cron_expr: Optional[str] = Field(None, description="Cron expression, e.g. '0 9 * * MON' for every Monday at 9am UTC")
    interval_seconds: Optional[int] = Field(None, description="Run every N seconds (alternative to cron_expr)")
    webhook_url: str = Field(..., description="URL to POST when the schedule fires")
    webhook_headers: dict = Field(default_factory=dict, description="Optional HTTP headers for the webhook")
    webhook_body: dict = Field(default_factory=dict, description="Optional JSON body to send with the webhook")
    timezone: str = Field("UTC", description="IANA timezone, e.g. 'America/New_York'")


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cron_expr: Optional[str] = None
    interval_seconds: Optional[int] = None
    webhook_url: Optional[str] = None
    webhook_headers: Optional[dict] = None
    webhook_body: Optional[dict] = None
    timezone: Optional[str] = None
    is_active: Optional[bool] = None


class ScheduleOut(BaseModel):
    id: str
    name: str
    description: str
    cron_expr: Optional[str]
    interval_seconds: Optional[int]
    webhook_url: str
    webhook_headers: dict
    webhook_body: dict
    timezone: str
    is_active: bool
    next_run_at: Optional[dt.datetime]
    last_run_at: Optional[dt.datetime]
    created_at: dt.datetime
    created_by: str

    class Config:
        from_attributes = True


class ExecutionOut(BaseModel):
    id: str
    schedule_id: str
    triggered_at: dt.datetime
    status: str
    response_code: Optional[int]
    response_body: str
    duration_ms: int

    class Config:
        from_attributes = True


class ScheduleDetail(ScheduleOut):
    recent_executions: list[ExecutionOut] = []
