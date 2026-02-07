from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.auth import verify_api_key
from shared.protocol import Task


router = APIRouter(
    prefix="/tasks", tags=["tasks"], dependencies=[Depends(verify_api_key)]
)


@router.get("/{job_id}", response_model=list[Task])
async def list_tasks(request: Request, job_id: str):
    coordinator = request.app.state.state.coordinator
    return await coordinator.list_tasks(job_id)
