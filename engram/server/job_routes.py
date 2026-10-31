"""Authenticated HTTP surface for the distillation job domain."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..management.jobs import JobConflict


class JobRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=512)
    summarize: bool = True
    clear_working: bool = True
    outcomes: Optional[bool] = True


def create_router(get_service, auth_dependency) -> APIRouter:
    router = APIRouter(tags=["distillation"])

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(status_code=404, detail="Distillation job not found") from None
        except JobConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @router.get("/v1/jobs")
    def list_jobs(
        status: Optional[Literal["pending", "running", "succeeded", "failed"]] = None,
        session_id: Optional[str] = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        user: str = Depends(auth_dependency),
    ):
        return call(get_service().jobs.list, user, status=status, session_id=session_id,
                    limit=limit, offset=offset)

    @router.post("/v1/jobs", status_code=202)
    def enqueue(req: JobRequest, user: str = Depends(auth_dependency)):
        return call(get_service().jobs.enqueue, user, req.session_id, summarize=req.summarize,
                    clear_working=req.clear_working, outcomes=req.outcomes)

    @router.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, user: str = Depends(auth_dependency)):
        get_service().jobs.recover(user)
        return call(get_service().jobs.get, user, job_id)

    @router.post("/v1/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str, user: str = Depends(auth_dependency)):
        return call(get_service().jobs.retry, user, job_id)

    return router
