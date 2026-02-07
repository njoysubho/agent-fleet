from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AgentMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    from_agent: str
    text: str
    summary: str
    timestamp: datetime = Field(default_factory=utc_now)
    read: bool = False


TaskStatus = Literal["pending", "in_progress", "completed", "failed"]


class Task(BaseModel):
    id: str = ""
    subject: str
    description: str
    activeForm: str = "Working"
    owner: str = ""
    status: TaskStatus = "pending"
    blocks: list[str] = Field(default_factory=list)
    blockedBy: list[str] = Field(default_factory=list)


class TaskAssignment(BaseModel):
    type: Literal["task_assignment"] = "task_assignment"
    job_id: str
    taskId: str
    subject: str
    description: str
    assignedBy: str
    timestamp: str
    agent_type: str = "general"
    persona: str = ""


class IdleNotification(BaseModel):
    type: Literal["idle_notification"] = "idle_notification"
    from_agent: str
    timestamp: str
    idleReason: Literal["available", "blocked", "waiting_for_blocker"]


class ShutdownRequest(BaseModel):
    type: Literal["shutdown_request"] = "shutdown_request"
    requestId: str
    reason: str


class InterAgentMessage(BaseModel):
    type: Literal["agent_message"] = "agent_message"
    job_id: str
    from_agent: str
    to_agent: str
    text: str
    timestamp: str


class PlanApprovalRequest(BaseModel):
    type: Literal["plan_approval_request"] = "plan_approval_request"
    requestId: str
    plan: str
    taskId: str


class WorkerRequest(BaseModel):
    type: Literal["worker_request"] = "worker_request"
    job_id: str
    leader_id: str
    requested_count: int
    timestamp: str


class WorkerRequestResponse(BaseModel):
    type: Literal["worker_request_response"] = "worker_request_response"
    job_id: str
    granted_count: int
    worker_ids: list[str]
    timestamp: str


class JobEvent(BaseModel):
    type: str
    job_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)
