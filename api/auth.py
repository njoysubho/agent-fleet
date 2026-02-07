from __future__ import annotations

import os

from fastapi import Header, HTTPException


async def verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("API_SECRET_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="API_SECRET_KEY not configured")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid API key")
