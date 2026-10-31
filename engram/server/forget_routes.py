from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool

from ..management.forgetting import ForgetError, SelectiveForgetting


class PreviewRequest(BaseModel):
    scope: dict
    include_sensitive: bool = False


class ConfirmRequest(BaseModel):
    scope: dict
    token: str = Field(min_length=20, max_length=200)
    version: str = Field(min_length=64, max_length=64)
    confirm: StrictBool = False


def create_router(get_service, auth_dependency) -> APIRouter:
    router = APIRouter()

    def domain(user):
        if user in {"anonymous", "1"}:
            raise HTTPException(403, "the public demo/anonymous namespace cannot be erased")
        return SelectiveForgetting(get_service())

    def invoke(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ForgetError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

    @router.post("/v1/forget/preview")
    def preview(req: PreviewRequest, user: str = Depends(auth_dependency)):
        return invoke(domain(user).preview, user, req.scope, include_sensitive=req.include_sensitive)

    @router.get("/v1/forget/preview/{token}")
    def preview_page(token: str, kind: str = "episodes", include_sensitive: bool = False,
                     offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
                     user: str = Depends(auth_dependency)):
        return invoke(domain(user).preview_page, user, token, kind=kind,
                      include_sensitive=include_sensitive, offset=offset, limit=limit)

    @router.post("/v1/forget/confirm")
    def confirm(req: ConfirmRequest, user: str = Depends(auth_dependency)):
        return invoke(domain(user).confirm, user, **req.model_dump())

    return router
