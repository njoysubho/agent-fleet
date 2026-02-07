from __future__ import annotations

import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api.routers import agents, jobs, tasks
from api.services.state import build_state, check_db


async def lifespan(app: FastAPI):
    state = await build_state()
    app.state.state = state
    try:
        yield
    finally:
        try:
            await state.coordinator.close()
        except Exception:
            pass
        try:
            if state.db_engine is not None:
                await state.db_engine.dispose()
        except Exception:
            pass


app = FastAPI(title="Agent Team API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobs.router, prefix="/api/v1")
app.include_router(agents.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health():
    state = app.state.state
    redis_ok = False
    try:
        redis_ok = bool(await state.coordinator.redis.ping())
    except Exception:
        redis_ok = False

    db_ok = await check_db(state.db_engine)
    return {"ok": redis_ok and db_ok, "redis": redis_ok, "db": db_ok}


@app.websocket("/api/v1/ws/jobs/{job_id}")
async def job_ws(ws: WebSocket, job_id: str):
    # WebSockets are authenticated with X-Api-Key as a header.
    api_key = ws.headers.get("x-api-key")
    expected = os.environ.get("API_SECRET_KEY")
    if not expected or api_key != expected:
        await ws.close(code=1008)
        return

    await ws.accept()
    app_obj = ws.scope.get("app")
    coordinator = app_obj.state.state.coordinator
    pubsub = coordinator.redis.pubsub()
    await pubsub.subscribe(coordinator.keys.job_events_channel(job_id))

    try:
        async for msg in pubsub.listen():
            if msg.get("type") != "message":
                continue
            data = msg.get("data")
            if isinstance(data, (bytes, bytearray)):
                data = data.decode("utf-8")
            await ws.send_text(data)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await pubsub.unsubscribe(coordinator.keys.job_events_channel(job_id))
            await pubsub.aclose()
        except Exception:
            pass
