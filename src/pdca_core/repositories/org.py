"""Truy vấn tổ chức: hồ sơ người dùng, phòng ban, thành viên project."""

from dataclasses import dataclass
from typing import Any

from psycopg import Connection
from psycopg.rows import class_row


@dataclass(frozen=True, slots=True)
class Profile:
    user_id: int
    name: str
    role: str
    department_id: int | None
    department_name: str | None


@dataclass(frozen=True, slots=True)
class Membership:
    project_id: int
    project_name: str
    project_role: str


def get_profile(conn: Connection[Any], user_id: int) -> Profile | None:
    with conn.cursor(row_factory=class_row(Profile)) as cur:
        return cur.execute(
            """
            select u.id as user_id, u.name, u.role,
                   d.id as department_id, d.name as department_name
            from users u
            left join departments d on d.id = u.department_id
            where u.id = %s and u.deleted_at is null
            """,
            (user_id,),
        ).fetchone()


def list_memberships(conn: Connection[Any], user_id: int) -> list[Membership]:
    with conn.cursor(row_factory=class_row(Membership)) as cur:
        return cur.execute(
            """
            select p.id as project_id, p.name as project_name, pm.project_role
            from project_members pm
            join projects p on p.id = pm.project_id
            where pm.user_id = %s and p.deleted_at is null
            order by p.name, p.id
            """,
            (user_id,),
        ).fetchall()
