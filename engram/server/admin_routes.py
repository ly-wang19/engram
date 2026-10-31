"""Explicit operator authorization for global model settings."""
from __future__ import annotations

import hmac
import os
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, SecretStr
from typing import Literal


class ModelConfigRequest(BaseModel):
    mode: Literal["inherit", "custom", "offline"] = "inherit"
    model: str = ""
    api_base: str = ""
    api_key: SecretStr | None = None
    clear_key: bool = False
    timeout: float = 30


def create_router(get_service, auth_dependency):
    router = APIRouter(prefix="/v1/admin", tags=["administration"])

    def check_admin(key: str) -> bool:
        keys = [item.strip() for item in os.environ.get("ENGRAM_ADMIN_KEYS", "").split(",") if item.strip()]
        return any(hmac.compare_digest(key, candidate) for candidate in keys)

    def admin(user: str = Depends(auth_dependency), x_engram_admin_key: str = Header(default="")):
        if not check_admin(x_engram_admin_key):
            raise HTTPException(403, "administrator key required")
        return user

    @router.get("/access")
    def access(user: str = Depends(auth_dependency), x_engram_admin_key: str = Header(default="")):
        return {"configured": bool(os.environ.get("ENGRAM_ADMIN_KEYS", "").strip()),
                "is_admin": check_admin(x_engram_admin_key)}

    @router.get("/models")
    def models(user: str = Depends(admin)):
        return get_service().models.view()

    @router.put("/models/{role}")
    def configure(role: Literal["extractor", "answerer", "vision"], req: ModelConfigRequest,
                  user: str = Depends(admin)):
        payload = req.model_dump(exclude={"api_key"})
        payload["api_key"] = req.api_key.get_secret_value() if req.api_key is not None else None
        try:
            return get_service().models.configure(role, payload)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception as exc:
            raise HTTPException(503, {"error": type(exc).__name__, "message": "model backend unavailable"}) from None

    @router.post("/models/{role}/test")
    def test(role: Literal["extractor", "answerer", "vision"], user: str = Depends(admin)):
        return get_service().models.test(role)

    return router
