"""Hỏi cấp trên (UC-18, FR-ASK) trên DB thật: nghiệp vụ, tin báo, hạn mức, hết hạn, MCP.

Ca T-11 (người nhận không do tham số), T-12 (người thứ ba bị từ chối), T-13 (trả lời hai lần),
T-16 (hạn mức), T-17/T-17b/T-18 (chọn người nhận, vượt cấp).
"""

import json
from dataclasses import dataclass
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from pdca_core.errors import Conflict, ForbiddenOrNotFound, InvalidArgument, RateLimited
from pdca_core.questions import service
from pdca_core.questions.service import QuestionSettings
from tests.db_fixtures import Conn
from tests.mcp_helpers import audit_rows, call_tool
from tests.org_helpers import (
    context_for,
    department_id,
    make_user,
    project_id,
    seed_director_id,
)

pytestmark = pytest.mark.integration

SETTINGS = QuestionSettings(channel="log", ttl_days=3, max_open=5, max_per_day=10)
PRIVATE_TEXT = "BÍ-MẬT-NỘI-DUNG-CÂU-HỎI"


@dataclass(frozen=True)
class Org:
    dept: int
    project: int
    director: int
    head: int
    staff: int
    peer: int  # đồng nghiệp cùng phòng, không liên quan tới câu hỏi
    other_head: int  # trưởng phòng phòng khác


def _org(pool: ConnectionPool) -> Org:
    with pool.connection() as conn:
        dept = department_id(conn)
        proj = project_id(conn)
        director = seed_director_id(conn)
        head = make_user(conn, "dept_head", manager_id=director, dept_id=dept, project=proj)
        staff = make_user(conn, "staff", manager_id=head, dept_id=dept, project=proj)
        peer = make_user(conn, "staff", manager_id=head, dept_id=dept, project=proj)
        other_head = make_user(
            conn,
            "dept_head",
            manager_id=director,
            dept_id=department_id(conn, "Phòng Thử nghiệm B"),
        )
    return Org(dept, proj, director, head, staff, peer, other_head)


@pytest.fixture
def org(app_pool: ConnectionPool) -> Org:
    return _org(app_pool)


def _ask(pool: ConnectionPool, asker: int, **kw: Any) -> dict[str, Any]:
    return service.ask(
        context_for(pool, asker), pool, SETTINGS, body=kw.pop("body", PRIVATE_TEXT), **kw
    )


def _rows(pool: ConnectionPool, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _notices(pool: ConnectionPool, user_id: int, kind: str) -> list[tuple[Any, ...]]:
    return _rows(
        pool,
        "select dedupe_key, status, payload from outbound_messages"
        " where user_id = %s and kind = %s order by id",
        user_id,
        kind,
    )


# --- hỏi và nhận (FR-ASK-01, 02, 06) ---


def test_question_goes_to_direct_manager_and_notifies_without_content(
    app_pool: ConnectionPool, org: Org
) -> None:
    result = _ask(app_pool, org.staff)
    assert result["status"] == "open"

    [row] = _rows(
        app_pool,
        "select asker_id, recipient_id from questions where id = %s",
        result["question_id"],
    )
    assert row == (org.staff, org.head)

    [(key, status, payload)] = _notices(app_pool, org.head, "question_notice")
    assert key == f"question_notice:{result['question_id']}"
    assert status == "queued"
    assert PRIVATE_TEXT not in json.dumps(payload, ensure_ascii=False)  # FR-ASK-06, FR-ASK-08

    received = service.list_mine(context_for(app_pool, org.head), app_pool, side="recipient")
    [item] = [i for i in received["items"] if i["id"] == result["question_id"]]
    assert item["body"] == PRIVATE_TEXT and item["asker_name"] and item["status"] == "open"


def test_project_and_task_context_is_validated(app_pool: ConnectionPool, org: Org) -> None:
    with app_pool.connection() as conn:
        mine = conn.execute(
            "insert into tasks (project_id, assignee_id, created_by, title)"
            " values (%s, %s, %s, 'm') returning id",
            (org.project, org.staff, org.head),
        ).fetchone()
        peers = conn.execute(
            "insert into tasks (project_id, assignee_id, created_by, title)"
            " values (%s, %s, %s, 'm') returning id",
            (org.project, org.peer, org.head),
        ).fetchone()
    assert mine is not None and peers is not None

    ok = _ask(app_pool, org.staff, project_id=org.project, task_id=int(mine[0]))
    assert ok["question_id"]
    with pytest.raises(ForbiddenOrNotFound):  # task của người khác
        _ask(app_pool, org.staff, project_id=org.project, task_id=int(peers[0]))
    with pytest.raises(InvalidArgument):  # task_id cần project_id
        _ask(app_pool, org.staff, task_id=int(mine[0]))
    with pytest.raises(ForbiddenOrNotFound):  # không phải thành viên project
        with app_pool.connection() as conn:
            other = project_id(conn, "Dự án thử nghiệm B")
        _ask(app_pool, org.staff, project_id=other)
    with pytest.raises(InvalidArgument):  # câu hỏi rỗng
        _ask(app_pool, org.staff, body="   ")


# --- quyền đọc / trả lời (FR-ASK-03, T-12) ---


def test_third_parties_cannot_read_or_answer(app_pool: ConnectionPool, org: Org) -> None:
    qid = _ask(app_pool, org.staff)["question_id"]
    with app_pool.connection() as conn:
        admin = make_user(conn, "admin")

    for outsider in (org.peer, org.other_head, org.director, admin):
        ctx = context_for(app_pool, outsider)
        if outsider != admin:  # admin không có question.* nên không gọi được list
            for side in ("asker", "recipient"):
                items = service.list_mine(ctx, app_pool, side=side)["items"]
                assert qid not in [i["id"] for i in items]
        with pytest.raises(ForbiddenOrNotFound):
            service.answer(ctx, app_pool, SETTINGS, question_id=qid, action="send", body="x")
        with pytest.raises(ForbiddenOrNotFound):
            service.cancel(ctx, app_pool, question_id=qid)

    # Người hỏi không tự trả lời được câu hỏi của chính mình.
    with pytest.raises(ForbiddenOrNotFound):
        service.answer(
            context_for(app_pool, org.staff),
            app_pool,
            SETTINGS,
            question_id=qid,
            action="send",
            body="x",
        )
    # Câu hỏi không tồn tại cho cùng lỗi như câu hỏi không được xem.
    with pytest.raises(ForbiddenOrNotFound):
        service.answer(
            context_for(app_pool, org.head),
            app_pool,
            SETTINGS,
            question_id=2_000_000_000,
            action="send",
            body="x",
        )


# --- trả lời một lần (FR-ASK-04, T-13) ---


def test_answer_is_sent_once_and_notifies_asker(app_pool: ConnectionPool, org: Org) -> None:
    qid = _ask(app_pool, org.staff)["question_id"]
    head = context_for(app_pool, org.head)

    done = service.answer(
        head, app_pool, SETTINGS, question_id=qid, action="send", body="Hãy làm theo cách A."
    )
    assert done == {"question_id": qid, "status": "answered"}
    with pytest.raises(Conflict):
        service.answer(head, app_pool, SETTINGS, question_id=qid, action="send", body="lần hai")

    assert len(_rows(app_pool, "select 1 from question_answers where question_id = %s", qid)) == 1
    [(key, _, payload)] = _notices(app_pool, org.staff, "answer_notice")
    assert key == f"answer_notice:{qid}"
    assert "Hãy làm theo cách A." not in json.dumps(payload, ensure_ascii=False)

    asked = service.list_mine(context_for(app_pool, org.staff), app_pool, side="asker")
    [item] = [i for i in asked["items"] if i["id"] == qid]
    assert item["status"] == "answered"
    assert item["answer"]["body"] == "Hãy làm theo cách A."


def test_decline_needs_reason_and_is_visible_to_asker(app_pool: ConnectionPool, org: Org) -> None:
    qid = _ask(app_pool, org.staff)["question_id"]
    head = context_for(app_pool, org.head)
    with pytest.raises(InvalidArgument):
        service.answer(head, app_pool, SETTINGS, question_id=qid, action="decline")
    with pytest.raises(InvalidArgument):
        service.answer(head, app_pool, SETTINGS, question_id=qid, action="send")  # thiếu body
    with pytest.raises(InvalidArgument):
        service.answer(head, app_pool, SETTINGS, question_id=qid, action="maybe", body="x")

    service.answer(
        head, app_pool, SETTINGS, question_id=qid, action="decline", reason="Để họp xong"
    )
    asked = service.list_mine(
        context_for(app_pool, org.staff), app_pool, side="asker", status="declined"
    )
    [item] = [i for i in asked["items"] if i["id"] == qid]
    assert item["decline_reason"] == "Để họp xong" and "answer" not in item


def test_draft_answers_are_never_shown_to_asker(app_pool: ConnectionPool, org: Org) -> None:
    """FR-ASK-09: bản nháp `draft_by_agent` (P2) chỉ có thể là nội bộ; P1 không bao giờ trả ra."""
    qid = _ask(app_pool, org.staff)["question_id"]
    _rows(
        app_pool,
        "insert into question_answers (question_id, author_id, status, body)"
        " values (%s, %s, 'draft_by_agent', 'NHÁP') returning id",
        qid,
        org.head,
    )
    for side, user in (("asker", org.staff), ("recipient", org.head)):
        items = service.list_mine(context_for(app_pool, user), app_pool, side=side)["items"]
        [item] = [i for i in items if i["id"] == qid]
        assert "answer" not in item
    assert _notices(app_pool, org.staff, "answer_notice") == []


# --- rút, hết hạn, hạn mức ---


def test_cancel_then_nobody_can_answer(app_pool: ConnectionPool, org: Org) -> None:
    qid = _ask(app_pool, org.staff)["question_id"]
    staff = context_for(app_pool, org.staff)
    assert service.cancel(staff, app_pool, question_id=qid)["status"] == "cancelled"
    with pytest.raises(Conflict):
        service.cancel(staff, app_pool, question_id=qid)
    with pytest.raises(Conflict):
        service.answer(
            context_for(app_pool, org.head),
            app_pool,
            SETTINGS,
            question_id=qid,
            action="send",
            body="x",
        )


def test_overdue_questions_expire_once_and_notify_asker(app_pool: ConnectionPool, org: Org) -> None:
    qid = _ask(app_pool, org.staff)["question_id"]
    fresh = _ask(app_pool, org.peer)["question_id"]
    _rows(app_pool, "update questions set due_at = now() - interval '1 minute' where id = %s", qid)

    assert service.expire_overdue(app_pool, SETTINGS) == [qid]
    assert service.expire_overdue(app_pool, SETTINGS) == []  # chạy lại không làm gì thêm
    assert _rows(app_pool, "select status from questions where id = %s", qid) == [("expired",)]
    assert _rows(app_pool, "select status from questions where id = %s", fresh) == [("open",)]
    assert len(_notices(app_pool, org.staff, "answer_notice")) == 1
    with pytest.raises(Conflict):
        service.answer(
            context_for(app_pool, org.head),
            app_pool,
            SETTINGS,
            question_id=qid,
            action="send",
            body="muộn",
        )
    # Không có câu trả lời nào do hệ thống tự sinh (FR-ASK-05).
    assert _rows(app_pool, "select 1 from question_answers where question_id = %s", qid) == []


def test_open_and_daily_limits(app_pool: ConnectionPool, org: Org) -> None:
    """T-16."""
    ctx = context_for(app_pool, org.staff)
    tight = QuestionSettings(channel="log", max_open=2, max_per_day=10)
    for _ in range(2):
        service.ask(ctx, app_pool, tight, body="q")
    with pytest.raises(RateLimited):
        service.ask(ctx, app_pool, tight, body="q")

    # Hạn mức theo ngày tính cả câu đã đóng.
    daily = QuestionSettings(channel="log", max_open=50, max_per_day=3)
    with pytest.raises(RateLimited):  # đã có 2 câu hôm nay, câu thứ 3 được, câu thứ 4 không
        for _ in range(3):
            service.ask(ctx, app_pool, daily, body="q")


def test_notice_waits_for_working_hours(app_pool: ConnectionPool, org: Org) -> None:
    """FR-NTF-04: người nhận nghỉ phép thì tin chờ, câu hỏi vẫn `open`."""
    with app_pool.connection() as conn:
        conn.execute("update users set away_until = current_date + 3 where id = %s", (org.head,))
    qid = _ask(app_pool, org.staff)["question_id"]
    [row] = _rows(
        app_pool,
        "select next_attempt_at > now() from outbound_messages where dedupe_key = %s",
        f"question_notice:{qid}",
    )
    assert row == (True,)
    assert _rows(app_pool, "select status from questions where id = %s", qid) == [("open",)]


# --- chọn người nhận: T-17, T-17b, T-18 (một giao dịch, rollback sau test) ---


def _chain(app: Conn) -> tuple[int, int, int]:
    dept = department_id(app)
    director = seed_director_id(app)
    head = make_user(app, "dept_head", manager_id=director, dept_id=dept)
    staff = make_user(app, "staff", manager_id=head, dept_id=dept)
    return director, head, staff


def test_t17_vacant_dept_head_goes_to_next_level(app: Conn) -> None:
    director, head, staff = _chain(app)
    assert service.resolve_recipient(app, staff).user_id == head

    app.execute("update users set status = 'locked' where id = %s", (head,))
    assert service.resolve_recipient(app, staff).user_id == director  # vượt cấp

    app.execute("update users set status = 'active' where id = %s", (head,))
    assert service.resolve_recipient(app, staff).user_id == head  # trưởng phòng quay lại

    app.execute("update users set deleted_at = now() where id = %s", (head,))
    assert service.resolve_recipient(app, staff).user_id == director  # xóa mềm cũng là trống


def test_t17b_away_dept_head_is_not_vacant(app: Conn) -> None:
    _, head, staff = _chain(app)
    app.execute("update users set away_until = current_date + 30 where id = %s", (head,))
    assert service.resolve_recipient(app, staff).user_id == head


def test_t18_broken_chain_is_refused_even_with_one_director(app: Conn) -> None:
    """Không có bước "giám đốc duy nhất": người không có cấp trên đủ điều kiện thì bị từ chối."""
    director = seed_director_id(app)
    app.execute(
        "update users set status = 'locked' where role = 'director' and id <> %s", (director,)
    )
    orphan = make_user(app, "staff", dept_id=department_id(app))  # không có manager_id
    with pytest.raises(InvalidArgument):
        service.resolve_recipient(app, orphan)

    wrong_role = make_user(app, "staff")  # manager_id trỏ tới nhân viên không có cấp trên
    with pytest.raises(InvalidArgument):
        service.resolve_recipient(app, make_user(app, "staff", manager_id=wrong_role))


def test_t18_chain_longer_than_three_steps_is_refused(app: Conn) -> None:
    director = seed_director_id(app)
    top = make_user(app, "staff", manager_id=director)
    s3 = make_user(app, "staff", manager_id=top)
    s2 = make_user(app, "staff", manager_id=s3)
    s1 = make_user(app, "staff", manager_id=s2)
    # s1 → s2 → s3 → top → director: giám đốc ở bước thứ 4, ngoài giới hạn 3 bước.
    with pytest.raises(InvalidArgument):
        service.resolve_recipient(app, s1)


def test_t18_director_without_superior_cannot_ask(app: Conn) -> None:
    director = seed_director_id(app)
    app.execute(
        "update users set status = 'locked' where role = 'director' and id <> %s", (director,)
    )
    with pytest.raises(InvalidArgument):
        service.resolve_recipient(app, director)


def test_manager_cycle_does_not_loop(app: Conn) -> None:
    a = make_user(app, "staff")
    b = make_user(app, "staff", manager_id=a)
    app.execute("update users set manager_id = %s where id = %s", (b, a))
    app.execute("update users set status = 'locked' where role = 'director'")
    with pytest.raises(InvalidArgument):
        service.resolve_recipient(app, a)


# --- qua MCP HTTP: T-11, audit không chứa nội dung ---


async def test_mcp_flow_and_audit(mcp_url: str, app_pool: ConnectionPool, org: Org) -> None:
    from pdca_core.authz.tokens import issue_token
    from pdca_core.repositories.tokens import PgTokenRepository

    tokens = PgTokenRepository(app_pool)
    staff_token = issue_token(tokens, org.staff).token
    head_token = issue_token(tokens, org.head).token

    # T-11: tham số người nhận do model truyền không có tác dụng.
    is_error, data = await call_tool(
        mcp_url, staff_token, "ask_superior", {"body": PRIVATE_TEXT, "recipient_id": org.other_head}
    )
    if not is_error:
        assert data["recipient_name"]
        [row] = _rows(
            app_pool, "select recipient_id from questions where id = %s", data["question_id"]
        )
        assert row == (org.head,)
    qid = _ask(app_pool, org.staff)["question_id"]

    is_error, data = await call_tool(
        mcp_url, head_token, "get_my_questions", {"side": "recipient", "status": "open"}
    )
    assert not is_error and qid in [i["id"] for i in data["items"]]

    is_error, data = await call_tool(
        mcp_url, staff_token, "answer_question", {"question_id": qid, "action": "send", "body": "x"}
    )
    assert is_error and data.startswith("forbidden_or_not_found")

    is_error, data = await call_tool(
        mcp_url, head_token, "answer_question", {"question_id": qid, "action": "send", "body": "ok"}
    )
    assert not is_error and data["status"] == "answered"
    is_error, data = await call_tool(
        mcp_url, head_token, "answer_question", {"question_id": qid, "action": "send", "body": "ok"}
    )
    assert is_error and data.startswith("conflict")

    is_error, data = await call_tool(mcp_url, None, "get_my_questions", {"side": "asker"})
    assert is_error and data.startswith("unauthorized")

    # Audit ghi cả lời gọi bị từ chối, nhưng không ghi nội dung (FR-ASK-08).
    results = [r[0] for r in audit_rows(app_pool, org.staff, "answer_question")]
    assert results == ["denied"]
    [(params,)] = _rows(
        app_pool,
        "select params_redacted from audit_log"
        " where user_id = %s and tool = 'ask_superior' order by id limit 1",
        org.staff,
    )
    assert PRIVATE_TEXT not in json.dumps(params, ensure_ascii=False)
    assert params["body"] == {"len": len(PRIVATE_TEXT)}
