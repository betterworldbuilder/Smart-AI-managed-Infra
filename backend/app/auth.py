"""POC authentication.

Deliberately minimal: a single local account (`admin` / `admin` by default), a
signed bearer token, and a loud warning in every response that exposes it.

    AUTH_ENABLED=false

disables it entirely for kiosk-style demos. This is *not* a production auth
system and the UI says so.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from pydantic import BaseModel

from .config import Settings, get_settings

WARNING = "POC ONLY - DO NOT USE THESE CREDENTIALS IN PRODUCTION"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    expires_at: int
    warning: str = WARNING


class User(BaseModel):
    username: str
    roles: list[str] = ["operator", "approver"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_token(settings: Settings, username: str) -> TokenResponse:
    expires_at = int(time.time()) + settings.auth_token_ttl_seconds
    payload = {"sub": username, "exp": expires_at}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = _b64(
        hmac.new(settings.auth_secret.encode(), body.encode(), hashlib.sha256).digest()
    )
    return TokenResponse(
        access_token=f"{body}.{signature}", username=username, expires_at=expires_at
    )


def verify_token(settings: Settings, token: str) -> dict[str, Any] | None:
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(
        hmac.new(settings.auth_secret.encode(), body.encode(), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:  # noqa: BLE001
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    return payload


def authenticate(settings: Settings, username: str, password: str) -> bool:
    return hmac.compare_digest(username, settings.auth_username) and hmac.compare_digest(
        password, settings.auth_password
    )


async def current_user(
    request: Request, settings: Settings = Depends(get_settings)
) -> User:
    """FastAPI dependency. Returns the demo user when auth is disabled."""
    if not settings.auth_enabled:
        return User(username="demo")
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_token(settings, header.split(" ", 1)[1].strip())
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return User(username=str(payload.get("sub", "admin")))
