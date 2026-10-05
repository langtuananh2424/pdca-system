"""Ma trận quyền mức tool cho hỏi cấp trên (LLD 3.2 `question.*` + mục "Quyền" của LLD 4.2).

Quyền theo vai trò × quan hệ với câu hỏi: người hỏi, người nhận, người thứ ba. Một ca "được phép"
nghĩa là qua được phân quyền (không `ForbiddenOrNotFound`); `answer_question` được gọi với
câu hỏi mới cho mỗi ca vì thao tác ghi làm đổi trạng thái.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.errors import ForbiddenOrNotFound
from pdca_core.questions import service
from pdca_core.questions.service import QuestionSettings
from tests.org_helpers import (
    context_for,
    department_id,
    make_user,
    project_id,
    seed_director_id,
)

pytestmark = pytest.mark.integration

ROLES = ("staff", "dept_head", "director", "admin")
SETTINGS = QuestionSettings(channel="log", max_open=1000, max_per_day=1000)


@dataclass(frozen=True)
class World:
    callers: dict[str, UserContext]
    peer: int  # người thứ ba cùng phòng
    other: int  # người hỏi/nhận khác, không phải người gọi


@pytest.fixture(scope="module")
def world(app_pool: ConnectionPool) -> World:
    with app_pool.connection() as conn:
        dept, proj = department_id(conn), project_id(conn)
        top = seed_director_id(conn)
        head = make_user(conn, "dept_head", manager_id=top, dept_id=dept, project=proj)
        ids = {
            "staff": make_user(conn, "staff", manager_id=head, dept_id=dept, project=proj),
            "dept_head": make_user(conn, "dept_head", manager_id=top, dept_id=dept, project=proj),
            # Giám đốc phải có cấp trên khớp `resolve_recipient` mới hỏi được (giám đốc khác).
            "director": make_user(conn, "director", manager_id=top, project=proj),
            "admin": make_user(conn, "admin"),
        }
        peer = make_user(conn, "staff", manager_id=head, dept_id=dept, project=proj)
        other = make_user(conn, "dept_head", manager_id=top, dept_id=dept)
    return World({r: context_for(app_pool, uid) for r, uid in ids.items()}, peer, other)


def _question(pool: ConnectionPool, asker: int, recipient: int) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            "insert into questions (asker_id, recipient_id, body, due_at)"
            " values (%s, %s, 'matrix', now() + interval '1 day') returning id",
            (asker, recipient),
        ).fetchone()
    assert row is not None
    return int(row[0])


Call = Callable[[UserContext, ConnectionPool, World], Any]

CASES: list[tuple[str, str, Call, set[str]]] = [
    (
        "ask_superior",
        "own",
        lambda c, p, w: service.ask(c, p, SETTINGS, body="matrix"),
        {"staff", "dept_head", "director"},
    ),
    (
        "get_my_questions",
        "asker_side",
        lambda c, p, w: service.list_mine(c, p, side="asker"),
        {"staff", "dept_head", "director"},
    ),
    (
        "get_my_questions",
        "recipient_side",
        lambda c, p, w: service.list_mine(c, p, side="recipient"),
        {"staff", "dept_head", "director"},
    ),
    (
        "answer_question",
        "question_to_me",
        lambda c, p, w: service.answer(
            c,
            p,
            SETTINGS,
            question_id=_question(p, w.peer, c.user_id),
            action="send",
            body="m",
        ),
        {"dept_head", "director"},  # staff không có question.answer dù là người nhận
    ),
    (
        "answer_question",
        "question_to_someone_else",
        lambda c, p, w: service.answer(
            c,
            p,
            SETTINGS,
            question_id=_question(p, w.peer, w.other),
            action="send",
            body="m",
        ),
        set(),  # kể cả giám đốc (cấp trên của người nhận) và quản trị
    ),
    (
        "answer_question",
        "question_i_asked",
        lambda c, p, w: service.answer(
            c,
            p,
            SETTINGS,
            question_id=_question(p, c.user_id, w.other),
            action="decline",
            reason="m",
        ),
        set(),  # người hỏi không tự trả lời
    ),
    (
        "cancel_question",
        "my_question",
        lambda c, p, w: service.cancel(c, p, question_id=_question(p, c.user_id, w.other)),
        {"staff", "dept_head", "director"},
    ),
    (
        "cancel_question",
        "question_of_peer",
        lambda c, p, w: service.cancel(c, p, question_id=_question(p, w.peer, c.user_id)),
        set(),  # người nhận cũng không rút hộ người hỏi
    ),
]


@pytest.mark.parametrize(
    ("call", "role", "allowed"),
    [
        pytest.param(call, role, role in allowed, id=f"{tool}-{situation}-{role}")
        for tool, situation, call, allowed in CASES
        for role in ROLES
    ],
)
def test_question_tool_permission(
    world: World, app_pool: ConnectionPool, call: Call, role: str, allowed: bool
) -> None:
    ctx = world.callers[role]
    if allowed:
        call(ctx, app_pool, world)
    else:
        with pytest.raises(ForbiddenOrNotFound):
            call(ctx, app_pool, world)
