from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from shared.protocol import Task


class JobCreateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    workspace_url: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


class JobDetailResponse(BaseModel):
    job: dict[str, Any]
    tasks: list[Task] = Field(default_factory=list)


class AgentInfo(BaseModel):
    name: str | None = None
    role: str | None = None
    status: str | None = None
    model: str | None = None
    heartbeat_ts: str | None = None
    current_task: str | None = None
    job_id: str | None = None
