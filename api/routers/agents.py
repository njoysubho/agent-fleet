from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from api.auth import verify_api_key
from api.models.schemas import AgentInfo


router = APIRouter(
    prefix="/agents", tags=["agents"], dependencies=[Depends(verify_api_key)]
)


@router.get("", response_model=list[AgentInfo])
async def list_agents(request: Request):
    coordinator = request.app.state.state.coordinator
    agents = await coordinator.list_agents()
    # Best-effort: only return a subset of fields.
    return [AgentInfo(**a) for a in agents]
