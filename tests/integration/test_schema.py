"""Kiểm thử V1 (LLD 2.2), quyền vai trò DB (LLD 2.3) và seed dev (LLD 2.5)."""

import pytest
from psycopg import errors

from tests.db_fixtures import ROOT, Conn, Database

pytestmark = pytest.mark.integration


def _denied(conn: Conn, sql: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute(sql)


def test_migrations_applied(db: Database) -> None:
    with db.connect("flyway") as conn:
        rows = conn.execute(
            "select version, success from flyway_schema_history"
            " where type = 'SQL' order by installed_rank"
        ).fetchall()
    assert rows[:2] == [("1", True), ("2", True)]
    assert all(success for _, success in rows)


def test_second_migrate_is_noop(db: Database) -> None:
    logs = db.run_flyway("migrate")
    assert "No migration necessary" in logs


def test_app_can_write_but_not_delete(app: Conn) -> None:
    with app.transaction():
        row = app.execute("insert into departments (name) values ('Phòng tạm') returning id")
        dept = row.fetchone()
        assert dept is not None
        app.execute("update departments set name = 'Phòng tạm 2' where id = %s", dept)
    # Xóa mềm bằng deleted_at; pdca_app không có quyền delete (LLD 2.3, DR-05).
    _denied(app, "delete from users where email = 'staff.a1@example.com'")


def test_audit_log_is_append_only(app: Conn) -> None:
    """FR-AUD-02: pdca_app chỉ insert/select trên audit_log."""
    with app.transaction():
        app.execute(
            "insert into audit_log (request_id, actor_kind, tool, result)"
            " values ('req-1', 'system', 'whoami', 'ok')"
        )
        app.execute("select count(*) from audit_log")
    _denied(app, "update audit_log set result = 'error'")
    _denied(app, "delete from audit_log")
    _denied(app, "truncate audit_log")


def test_app_cannot_touch_flyway_history(app: Conn) -> None:
    _denied(app, "select * from flyway_schema_history")


def test_readonly_cannot_write(db: Database) -> None:
    with db.connect("pdca_readonly") as conn:
        conn.execute("select count(*) from users")
        _denied(conn, "insert into departments (name) values ('x')")
        _denied(conn, "update users set name = 'x'")


def test_default_privileges_cover_future_tables(db: Database) -> None:
    """Bảng do migration sau tạo (chủ sở hữu flyway) tự có quyền cho pdca_app."""
    with db.connect("flyway") as owner:
        owner.execute(
            "create table zz_future (id bigint generated always as identity primary key, v text)"
        )
        owner.commit()
        try:
            with db.connect("pdca_app") as conn:
                conn.execute("insert into zz_future (v) values ('a')")
                conn.execute("update zz_future set v = 'b'")
                conn.commit()
                _denied(conn, "delete from zz_future")
            with db.connect("pdca_readonly") as conn:
                conn.execute("select * from zz_future")
        finally:
            owner.execute("drop table zz_future")
            owner.commit()


def test_report_unique_per_user_project_day(app: Conn) -> None:
    """FR-CHK-03: mỗi (người, project, ngày) một báo cáo."""
    sql = """
        insert into reports (user_id, project_id, report_date, source, status)
        select u.id, p.id, date '2026-10-01', 'manual', 'submitted'
        from users u join projects p on p.department_id = u.department_id
        where u.email = 'staff.a1@example.com'
    """
    with app.transaction():
        app.execute(sql)
    with pytest.raises(errors.UniqueViolation), app.transaction():
        app.execute(sql)


def test_seed_org_structure(app: Conn) -> None:
    # Chỉ đếm tài khoản seed; test khác tạo người dùng @test.invalid.
    rows = app.execute(
        "select role, count(*) from users where email like '%@example.com' group by role"
    ).fetchall()
    roles = {role: count for role, count in rows}
    assert roles == {"admin": 1, "director": 1, "dept_head": 2, "staff": 3}
    # Nhân viên báo cáo cho trưởng phòng của mình, trưởng phòng báo cáo cho giám đốc.
    bad = app.execute(
        """
        select u.email from users u
        join departments d on d.id = u.department_id
        left join users m on m.id = u.manager_id
        where u.email like '%@example.com'
          and ((u.role = 'staff' and u.manager_id is distinct from d.head_user_id)
            or (u.role = 'dept_head' and m.role is distinct from 'director'))
        """
    ).fetchall()
    assert bad == []


def test_seed_is_idempotent(db: Database) -> None:
    seed = (ROOT / "db" / "seed" / "R__dev_seed.sql").read_text(encoding="utf-8")
    count_sql = """
        select (select count(*) from departments), (select count(*) from users),
               (select count(*) from projects), (select count(*) from project_members)
    """
    with db.connect("flyway") as conn:
        before = conn.execute(count_sql).fetchone()
        conn.execute(seed.encode())
        after = conn.execute(count_sql).fetchone()
        conn.rollback()
    assert before == after
