"""Dựng cơ cấu tổ chức nhỏ cho test hỏi cấp trên (UC-18): người dùng có `manager_id`."""

import uuid
from datetime import date
from typing import Any

from psycopg import Connection
from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.authz.tokens import authenticate, issue_token
from pdca_core.repositories.tokens import PgTokenRepository


def department_id(conn: Connection[Any], name: str = "Phòng Thử nghiệm A") -> int:
    row = conn.execute("select id from departments where name = %s", (name,)).fetchone()
    assert row is not None
    return int(row[0])


def project_id(conn: Connection[Any], name: str = "Dự án thử nghiệm A") -> int:
    row = conn.execute("select id from projects where name = %s", (name,)).fetchone()
    assert row is not None
    return int(row[0])


def seed_director_id(conn: Connection[Any]) -> int:
    row = conn.execute("select id from users where email = 'director@example.com'").fetchone()
    assert row is not None
    return int(row[0])


def make_user(
    conn: Connection[Any],
    role: str,
    *,
    manager_id: int | None = None,
    dept_id: int | None = None,
    project: int | None = None,
    status: str = "active",
    away_until: date | None = None,
    name: str | None = None,
) -> int:
    """Tạo người dùng (email ngẫu nhiên); `project` thì thêm làm thành viên."""
    row = conn.execute(
        """
        insert into users (name, email, role, department_id, manager_id, status, away_until)
        values (%s, %s, %s, %s, %s, %s, %s) returning id
        """,
        (
            name or f"{role} {uuid.uuid4().hex[:6]}",
            f"{uuid.uuid4().hex[:12]}@test.invalid",
            role,
            dept_id,
            manager_id,
            status,
            away_until,
        ),
    ).fetchone()
    assert row is not None
    user_id = int(row[0])
    if project is not None:
        conn.execute(
            "insert into project_members (project_id, user_id) values (%s, %s)", (project, user_id)
        )
    return user_id


def context_for(pool: ConnectionPool, user_id: int) -> UserContext:
    """`UserContext` dựng từ token thật, như lúc MCP xác thực."""
    tokens = PgTokenRepository(pool)
    return authenticate(issue_token(tokens, user_id).token, tokens, "test")
