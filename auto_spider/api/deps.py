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


VALID_ROLES = frozenset({"viewer", "operator", "reviewer", "admin"})


def get_actor(
    x_user_id: Annotated[str | None, Header()] = None,
    x_user_role: Annotated[str | None, Header()] = None,
) -> Actor:
    settings = get_settings()
    if settings.auth_mode == "dev":
        role = x_user_role or "admin"
        if role not in VALID_ROLES:
            raise HTTPException(status_code=403, detail="角色无效")
        return Actor(user_id=x_user_id or "dev-user", role=role)
    if not x_user_id or not x_user_role:
        raise HTTPException(status_code=401, detail="未认证")
    if x_user_role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail="角色无效")
    return Actor(user_id=x_user_id, role=x_user_role)


def require_roles(*roles: str):
    def dependency(actor: Annotated[Actor, Depends(get_actor)]) -> Actor:
        if actor.role != "admin" and actor.role not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return actor

    return dependency


DbSession = Annotated[Session, Depends(get_session)]
CurrentActor = Annotated[Actor, Depends(get_actor)]
OperatorActor = Annotated[Actor, Depends(require_roles("operator"))]
ReviewerActor = Annotated[Actor, Depends(require_roles("reviewer"))]
AdminActor = Annotated[Actor, Depends(require_roles("admin"))]
