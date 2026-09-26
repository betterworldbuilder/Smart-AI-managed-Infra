"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from ..auth import User, current_user
from ..config import Settings, get_settings
from ..container import Container, get_container
from ..models import Actor

ContainerDep = Annotated[Container, Depends(get_container)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
UserDep = Annotated[User, Depends(current_user)]


def human_actor(user: User) -> Actor:
    """Every approval is attributed to the authenticated human."""
    return Actor.human(user.username)
