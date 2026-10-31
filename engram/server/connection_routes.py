from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from typing import Literal


class ConnectionReport(BaseModel):
    source: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
    status: Literal["succeeded", "failed", "stored", "deferred"]
    operation: str = Field(default="sync", max_length=80)
    session_id: str | None = Field(default=None, max_length=512)
    error: str | None = Field(default=None, max_length=80)


def create_router(get_service, auth_dependency):
    router = APIRouter(prefix="/v1/connections", tags=["connections"])

    @router.get("")
    def index(user: str = Depends(auth_dependency)):
        return get_service().connections.index(user)

    @router.get("/events")
    def events(source: str | None = None, offset: int = Query(0, ge=0),
               limit: int = Query(30, ge=1, le=100), user: str = Depends(auth_dependency)):
        return get_service().connections.events(user, source, offset, limit)

    @router.post("/report")
    def report(req: ConnectionReport, user: str = Depends(auth_dependency)):
        return get_service().connections.record(user, **req.model_dump())

    return router
