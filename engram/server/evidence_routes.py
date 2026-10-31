"""Optional evidence-answer HTTP surface; core recall and benchmark API remain unchanged."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..management.evidence import EvidenceAnswers, EvidenceError


class AskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=16000)
    n_chunks: int = Field(default=6, ge=1, le=12)
    as_of: Optional[float] = None
    redact_sensitive: bool = True


def create_router(get_service, auth_dependency) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/ask")
    def ask(req: AskRequest, user: str = Depends(auth_dependency)):
        try:
            return EvidenceAnswers(get_service()).ask(user, **req.model_dump())
        except EvidenceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.get("/v1/evidence/{token}")
    def source(token: str, include_sensitive: bool = False,
               offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
               user: str = Depends(auth_dependency)):
        try:
            return EvidenceAnswers(get_service()).source(
                user, token, include_sensitive=include_sensitive, offset=offset, limit=limit)
        except EvidenceError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    return router
