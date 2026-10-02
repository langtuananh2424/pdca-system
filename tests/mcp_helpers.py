"""Hạ tầng gọi MCP Server thật qua HTTP cho test tích hợp và ma trận quyền."""

import json
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
import uvicorn
from mcp import Client

# create_mcp_http_client định nghĩa ở mô-đun riêng tư mcp.shared._httpx_utils.
from mcp.client.streamable_http import (  # type: ignore[attr-defined]
    create_mcp_http_client,
    streamable_http_client,
)
from psycopg_pool import ConnectionPool

from apps.mcp_server.__main__ import create_app
from apps.mcp_server.settings import Settings
from pdca_core.authz.tokens import issue_token
from pdca_core.repositories.tokens import PgTokenRepository
from tests.db_fixtures import Database


@dataclass(frozen=True)
class Person:
    user_id: int
    token: str
    project_id: int


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="session")
def mcp_url(db: Database, app_pool: ConnectionPool) -> Iterator[str]:
    port = _free_port()
    settings = Settings.from_env(
        {
            "DATABASE_URL": db.conninfo("pdca_app"),
            "MCP_PORT": str(port),
            "RATE_LIMIT_PER_MIN": "1000",
        }
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings, app_pool), host="127.0.0.1", port=port, log_config=None)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=10)


def person(pool: ConnectionPool, project_name: str, role: str = "staff") -> Person:
    with pool.connection() as conn:
        row = conn.execute(
            """
            with p as (select id, department_id from projects where name = %s),
                 u as (
                   insert into users (name, email, role, department_id)
                   select 'MCP test', %s, %s, department_id from p returning id
                 ),
                 m as (insert into project_members (project_id, user_id)
                       select p.id, u.id from p, u)
            select u.id, p.id from u, p
            """,
            (project_name, f"{uuid.uuid4().hex[:12]}@test.invalid", role),
        ).fetchone()
    assert row is not None
    token = issue_token(PgTokenRepository(pool), int(row[0])).token
    return Person(user_id=int(row[0]), token=token, project_id=int(row[1]))


def task(
    pool: ConnectionPool, who: Person, title: str, due: str | None, status: str = "todo"
) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            """
            insert into tasks (project_id, assignee_id, created_by, title, due_date, status)
            values (%s, %s, %s, %s, %s, %s) returning id
            """,
            (who.project_id, who.user_id, who.user_id, title, due, status),
        ).fetchone()
    assert row is not None
    return int(row[0])


async def call_tool(
    url: str, token: str | None, tool: str, args: dict[str, Any] | None = None
) -> tuple[bool, Any]:
    """Trả (is_error, dữ liệu có cấu trúc hoặc văn bản lỗi)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        create_mcp_http_client(headers=headers) as http,
        Client(streamable_http_client(url, http_client=http)) as client,
    ):
        result = await client.call_tool(tool, args or {})
    if result.is_error:
        text: str = result.content[0].text  # type: ignore[union-attr]
        # SDK thêm tiền tố "Error executing tool <tên>: " trước thông điệp của ToolError.
        return True, text.removeprefix(f"Error executing tool {tool}: ")
    data = result.structured_content
    if data is None:
        data = json.loads(result.content[0].text)  # type: ignore[union-attr]
    return False, data


def audit_rows(pool: ConnectionPool, user_id: int | None, tool: str) -> list[tuple[Any, ...]]:
    with pool.connection() as conn:
        return conn.execute(
            "select result, error_code from audit_log"
            " where user_id is not distinct from %s and tool = %s order by id",
            (user_id, tool),
        ).fetchall()
