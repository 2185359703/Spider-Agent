from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from auto_spider.config import get_settings
from auto_spider.db.session import get_session


@dataclass(frozen=True)
class Actor:
    user_id: str
    role: str


def get_actor(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_role: Annotated[str | None, Header()] = None,
) -> Actor:
    settings = get_settings()
    if settings.auth_mode == "dev":
        return Actor(user_id=x_user_id or "dev-user", role=x_user_role or "admin")
    if not x_user_id or not x_user_role:
        raise HTTPException(status_code=401, detail="未认证")
    return Actor(user_id=x_user_id, role=x_user_role)


def require_roles(*roles: str):
    def dependency(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
        if actor.role not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return actor

    return dependency


DbSession = Annotated[Session, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(get_actor)]
