"""Danh tính người gọi, chỉ tạo từ token (FR-AUTH-02)."""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    STAFF = "staff"
    DEPT_HEAD = "dept_head"
    DIRECTOR = "director"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class UserContext:
    user_id: int
    role: Role
    department_id: int | None
    project_ids: frozenset[int]
    request_id: str
