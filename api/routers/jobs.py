from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from api.auth import verify_api_key
from api.models.schemas import JobCreateRequest, JobCreateResponse, JobDetailResponse
from shared.protocol import Task


router = APIRouter(
    prefix="/jobs", tags=["jobs"], dependencies=[Depends(verify_api_key)]
)


@router.post("", response_model=JobCreateResponse)
async def create_job(request: Request, body: JobCreateRequest):
    coordinator = request.app.state.state.coordinator
    job_id = str(uuid4())
    await coordinator.upsert_job(
        job_id,
        {
            "job_id": job_id,
            "prompt": body.prompt,
            "status": "queued",
            "workspace_url": body.workspace_url or "",
            "config": body.config,
        },
    )
    await coordinator.submit_job(
        {
            "job_id": job_id,
            "prompt": body.prompt,
            "workspace_url": body.workspace_url,
            "config": body.config,
        }
    )
    await coordinator.publish_job_event(
        job_id, {"type": "job_submitted", "job_id": job_id}
    )
    return JobCreateResponse(job_id=job_id, status="queued")


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(request: Request, job_id: str):
    coordinator = request.app.state.state.coordinator
    job = await coordinator.get_job(job_id)
    tasks = await coordinator.list_tasks(job_id)
    return JobDetailResponse(
        job=job or {"job_id": job_id, "status": "unknown"}, tasks=tasks
    )


@router.get("/{job_id}/tasks", response_model=list[Task])
async def get_job_tasks(request: Request, job_id: str):
    coordinator = request.app.state.state.coordinator
    return await coordinator.list_tasks(job_id)


@router.delete("/{job_id}")
async def cancel_job(request: Request, job_id: str):
    coordinator = request.app.state.state.coordinator
    await coordinator.set_job_status(job_id, "cancelled")
    return {"job_id": job_id, "status": "cancelled"}
