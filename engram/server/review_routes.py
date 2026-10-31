"""Review APIs share the application's authentication without importing the app."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..management.review import ReviewError, ReviewQueue


class QuestionUpdate(BaseModel):
    status: Literal["open", "resolved"]
    resolution_id: str | None = Field(default=None, max_length=256)
    note: str = Field(default="", max_length=2000)
    expected_version: str = Field(min_length=64, max_length=64)
    include_sensitive: bool = False


class HealthUpdate(BaseModel):
    status: Literal["open", "ignored", "resolved"]
    note: str = Field(default="", max_length=2000)
    expected_version: str = Field(min_length=64, max_length=64)
    include_sensitive: bool = False


def create_router(get_service, auth_dependency) -> APIRouter:
    router = APIRouter(prefix="/v1/review", tags=["review"])

    def run(method, user, **kwargs):
        try:
            return getattr(ReviewQueue(get_service()), method)(user, **kwargs)
        except ReviewError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @router.get("/outcomes")
    def outcomes(kind: str = "all", status: str = "live", q: str = Query("", max_length=2000),
                 include_sensitive: bool = False, limit: int = Query(30, ge=1, le=100),
                 offset: int = Query(0, ge=0), user: str = Depends(auth_dependency)):
        return run("outcomes", user, kind=kind, status=status, q=q, include_sensitive=include_sensitive, limit=limit, offset=offset)

    @router.get("/questions")
    def questions(status: str = "open", q: str = Query("", max_length=2000),
                  include_sensitive: bool = False, limit: int = Query(20, ge=1, le=100),
                  offset: int = Query(0, ge=0), user: str = Depends(auth_dependency)):
        return run("questions", user, status=status, q=q, include_sensitive=include_sensitive, limit=limit, offset=offset)

    @router.patch("/questions/{fact_id}")
    def question_update(fact_id: str, body: QuestionUpdate, user: str = Depends(auth_dependency)):
        return run("update_question", user, fact_id=fact_id, **body.model_dump())

    @router.get("/health")
    def health(kind: str = "all", status: str = "open", q: str = Query("", max_length=2000),
               include_sensitive: bool = False, limit: int = Query(30, ge=1, le=100),
               offset: int = Query(0, ge=0), user: str = Depends(auth_dependency)):
        return run("health", user, kind=kind, status=status, q=q, include_sensitive=include_sensitive, limit=limit, offset=offset)

    @router.patch("/health/{finding_id}")
    def health_update(finding_id: str, body: HealthUpdate, user: str = Depends(auth_dependency)):
        return run("update_health", user, finding_id=finding_id, **body.model_dump())

    return router
