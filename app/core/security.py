from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class User:
    username: str
    role: str
    permissions: tuple[str, ...]

    def can(self, permission: str) -> bool:
        return permission in self.permissions or "*" in self.permissions


USERS: dict[str, User] = {
    "commander-token": User("commander", "commander", ("*",)),
    "analyst-token": User("analyst", "analyst", ("read:state", "read:report", "ask:llm")),
    "operator-token": User(
        "operator",
        "operator",
        ("read:state", "control:sim", "edit:scenario", "ask:llm", "read:report"),
    ),
}


def user_from_token(token: str | None) -> User:
    if not token:
        return USERS["commander-token"]
    return USERS.get(token, USERS["commander-token"])


def require_permission(user: User, permission: str) -> None:
    if not user.can(permission):
        raise PermissionError(f"{user.username} lacks permission {permission}")


def role_catalog() -> list[dict[str, str | Iterable[str]]]:
    return [
        {"role": user.role, "username": user.username, "permissions": user.permissions}
        for user in USERS.values()
    ]
