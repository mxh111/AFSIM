from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class User:
    username: str
    role: str
    permissions: tuple[str, ...]

    def can(self, permission: str) -> bool:
        return permission in self.permissions or "*" in self.permissions


class AuthenticationError(ValueError):
    pass


DEFAULT_TOKENS = {
    "commander": "commander-token",
    "operator": "operator-token",
    "analyst": "analyst-token",
}


TOKEN_ENV = {
    "commander": "AFSIM_COMMANDER_TOKEN",
    "operator": "AFSIM_OPERATOR_TOKEN",
    "analyst": "AFSIM_ANALYST_TOKEN",
}


ROLE_USERS: dict[str, User] = {
    "commander": User("commander", "commander", ("*",)),
    "analyst": User("analyst", "analyst", ("read:state", "read:report", "ask:llm")),
    "operator": User(
        "operator",
        "operator",
        ("read:state", "control:sim", "edit:scenario", "ask:llm", "read:report"),
    ),
}


def _configured_tokens() -> dict[str, User]:
    users: dict[str, User] = {}
    for role, user in ROLE_USERS.items():
        token = os.getenv(TOKEN_ENV[role], DEFAULT_TOKENS[role]).strip()
        if token:
            users[token] = user
    return users


def user_from_token(token: str | None) -> User:
    if not token:
        raise AuthenticationError("missing AFSIM token")
    user = _configured_tokens().get(token.strip())
    if not user:
        raise AuthenticationError("invalid AFSIM token")
    return user


def require_permission(user: User, permission: str) -> None:
    if not user.can(permission):
        raise PermissionError(f"{user.username} lacks permission {permission}")


def role_catalog() -> list[dict[str, str | Iterable[str]]]:
    return [
        {"role": user.role, "username": user.username, "permissions": user.permissions}
        for user in ROLE_USERS.values()
    ]
