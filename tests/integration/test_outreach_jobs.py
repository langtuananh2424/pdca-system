"""Job liên lạc chủ động, hàng đợi gửi tin, run_job trên DB thật (LLD 5, SDD 4.11.3/4.11.7).

Dùng đồng hồ cố định (thứ Hai 03/03/2031 giờ Bangkok) để ngày/giờ làm việc ổn
định; chỉ kiểm tra người dùng do test tạo (DB dùng chung với test khác).
T-04 (chạy hai lần → mỗi người một tin), T-05 (không trả lời → not_reported, rỗng).
"""

import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from psycopg_pool import ConnectionPool

from pdca_core.job_runner import JobOutcome, run_job
from pdca_core.outreach import jobs, outbox
from pdca_core.outreach.compose import TemplateComposer
from pdca_core.outreach.ports import OutboundMessage, SendResult
from tests.db_fixtures import Database
from tests.mcp_helpers import Person, person, task

pytestmark = pytest.mark.integration

BKK = ZoneInfo("Asia/Bangkok")
DAY = date(2031, 3, 3)
MORNING = datetime(2031, 3, 3, 8, 30, tzinfo=BKK)
AFTERNOON = datetime(2031, 3, 3, 16, 45, tzinfo=BKK)
REMIND = datetime(2031, 3, 3, 17, 15, tzinfo=BKK)
COMPOSER = TemplateComposer()


def _settings(**kw: Any) -> jobs.OutreachSettings:
    return jobs.OutreachSettings(channel=kw.pop("channel", "log"), **kw)


def _sql(pool: ConnectionPool, sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _messages(pool: ConnectionPool, who: Person) -> list[tuple[Any, ...]]:
    return _sql(
        pool,
        "select kind, dedupe_key, status, payload from outbound_messages"
        " where user_id = %s order by id",
        who.user_id,
    )


def _project(pool: ConnectionPool) -> str:
    """Project riêng (phòng A) để danh sách project của người dùng test ổn định."""
    name = f"Outreach {uuid.uuid4().hex[:8]}"
    _sql(
        pool,
        "insert into projects (name, department_id, config)"
        " select %s, id, %s::jsonb from departments where name = 'Phòng Thử nghiệm A'",
        name,
        '{"check": {"questions": ["Có blocker kỹ thuật không?"]}}',
    )
    return name


def test_morning_nudge_selects_recipients(app_pool: ConnectionPool) -> None:
    project = _project(app_pool)
    ok = person(app_pool, project)
    task(app_pool, ok, "Viết API", "2031-03-03")
    away = person(app_pool, project)
    locked = person(app_pool, project)
    late_starter = person(app_pool, project)
    director = person(app_pool, project, role="director")
    _sql(app_pool, "update users set away_until = %s where id = %s", DAY, away.user_id)
    _sql(app_pool, "update users set status = 'locked' where id = %s", locked.user_id)
    _sql(app_pool, "update users set work_start = '10:00' where id = %s", late_starter.user_id)

    first = jobs.morning_nudge(app_pool, MORNING, _settings(), COMPOSER)
    second = jobs.morning_nudge(app_pool, MORNING, _settings(), COMPOSER)

    assert ok.user_id in first.enqueued
    for excluded in (away, locked, late_starter, director):
        assert excluded.user_id not in first.enqueued + first.skipped
    # T-04: chạy lại không tạo tin trùng.
    assert ok.user_id in second.skipped and ok.user_id not in second.enqueued
    [(kind, key, status, payload)] = _messages(app_pool, ok)
    assert (kind, key, status) == (
        "morning_nudge",
        f"morning_nudge:{ok.user_id}:2031-03-03",
        "queued",
    )
    assert "Viết API" in payload["body"] and "hạn HÔM NAY" in payload["body"]
    assert "Trợ lý AI PDCA" in payload["body"]
    assert payload["local_date"] == "2031-03-03"


def test_daily_cap_and_timezone(app_pool: ConnectionPool) -> None:
    project = _project(app_pool)
    capped = person(app_pool, project)
    far = person(app_pool, project)
    # Đã có 1 tin cho ngày 03/03 → hạn mức 1 chặn tin tiếp theo (FR-NTF-04).
    _sql(
        app_pool,
        "insert into outbound_messages (user_id, channel, kind, payload, dedupe_key)"
        " values (%s, 'log', 'summary', %s::jsonb, %s)",
        capped.user_id,
        '{"local_date": "2031-03-03"}',
        f"test:{capped.user_id}",
    )
    # 08:30 Bangkok = 20:30 hôm trước ở New York → ngoài giờ làm (08:30–17:30).
    _sql(app_pool, "update users set timezone = 'America/New_York' where id = %s", far.user_id)

    result = jobs.morning_nudge(app_pool, MORNING, _settings(max_messages_per_day=1), COMPOSER)
    assert capped.user_id not in result.enqueued
    assert far.user_id not in result.enqueued


def test_progress_ask_remind_and_not_reported(app_pool: ConnectionPool) -> None:
    project = _project(app_pool)
    reported = person(app_pool, project)
    silent = person(app_pool, project)
    replied = person(app_pool, project)
    drafted = person(app_pool, project)
    _sql(
        app_pool,
        "insert into reports (user_id, project_id, report_date, done, source, status)"
        " values (%s, %s, %s, 'xong', 'claude_code', 'submitted')",
        reported.user_id,
        reported.project_id,
        DAY,
    )

    ask = jobs.progress_ask(app_pool, AFTERNOON, _settings(), COMPOSER)
    assert reported.user_id in ask.skipped  # đã nộp đủ → không hỏi
    for who in (silent, replied, drafted):
        assert who.user_id in ask.enqueued
    [(_, _, _, payload)] = _messages(app_pool, silent)
    assert payload["project_ids"] == [silent.project_id]
    assert "Có blocker kỹ thuật không?" in payload["body"]

    # Giả lập bộ gửi: tin đã gửi; một người đã trả lời; một người có bản nháp từ trả lời.
    _sql(
        app_pool,
        "update outbound_messages set status = 'sent' where kind = 'progress_ask'"
        " and user_id = any(%s)",
        [silent.user_id, replied.user_id, drafted.user_id],
    )
    _sql(
        app_pool,
        "update outbound_messages set status = 'replied' where user_id = %s",
        replied.user_id,
    )
    _sql(
        app_pool,
        "insert into reports (user_id, project_id, report_date, done, source, status)"
        " values (%s, %s, %s, 'nháp từ trả lời', 'channel_reply', 'draft_by_agent')",
        drafted.user_id,
        drafted.project_id,
        DAY,
    )

    remind1 = jobs.progress_remind(app_pool, REMIND, _settings(), COMPOSER)
    remind2 = jobs.progress_remind(app_pool, REMIND, _settings(), COMPOSER)
    assert silent.user_id in remind1.enqueued and silent.user_id in remind2.skipped
    assert replied.user_id not in remind1.enqueued + remind1.skipped
    reminders = [m for m in _messages(app_pool, silent) if m[0] == "reminder"]
    assert [m[1] for m in reminders] == [f"progress_ask:{silent.user_id}:2031-03-03:r1"]

    # T-05: sau khi nhắc lại vẫn im lặng → not_reported rỗng; không đè bản nháp/đã nộp.
    marked = jobs.mark_not_reported(app_pool, DAY)
    assert silent.user_id in marked.enqueued
    assert replied.user_id in marked.enqueued  # trả lời nhưng chưa thành báo cáo nào
    assert drafted.user_id not in marked.enqueued
    assert reported.user_id not in marked.enqueued + marked.skipped  # không được hỏi
    rows = _sql(
        app_pool,
        "select user_id, status, source, done, blockers, raw_text_approved from reports"
        " where report_date = %s and user_id = any(%s) order by user_id",
        DAY,
        [silent.user_id, drafted.user_id],
    )
    assert rows == [
        (silent.user_id, "not_reported", "system", "", "", None),
        (drafted.user_id, "draft_by_agent", "channel_reply", "nháp từ trả lời", "", None),
    ]
    assert jobs.mark_not_reported(app_pool, DAY).enqueued.count(silent.user_id) == 0


class FakeChannel:
    def __init__(self, name: str, results: list[SendResult]) -> None:
        self.name = name
        self.results = results
        self.sent: list[OutboundMessage] = []

    def send(self, message: OutboundMessage) -> SendResult:
        self.sent.append(message)
        return self.results.pop(0) if self.results else SendResult(ok=True, external_id="x")


def _queue(pool: ConnectionPool, who: Person, channel: str, tag: str) -> int:
    rows = _sql(
        pool,
        "insert into outbound_messages (user_id, channel, kind, payload, dedupe_key)"
        " values (%s, %s, 'morning_nudge', %s::jsonb, %s) returning id",
        who.user_id,
        channel,
        '{"subject": "Chủ đề", "body": "Nội dung"}',
        f"{tag}:{uuid.uuid4().hex}",
    )
    return int(rows[0][0])


def _status(pool: ConnectionPool, message_id: int) -> tuple[Any, ...]:
    return _sql(
        pool,
        "select status, attempts, external_id from outbound_messages where id = %s",
        message_id,
    )[0]


def test_outbox_sends_retries_and_gives_up(app_pool: ConnectionPool) -> None:
    channel = f"test-{uuid.uuid4().hex[:6]}"
    who = person(app_pool, _project(app_pool))
    ok_id = _queue(app_pool, who, channel, "ok")
    flaky_id = _queue(app_pool, who, channel, "flaky")
    bad_id = _queue(app_pool, who, channel, "bad")
    other_channel_id = _queue(app_pool, who, "chua-cau-hinh", "other")

    fake = FakeChannel(
        channel,
        [
            SendResult(ok=True, external_id="ext-1"),
            SendResult(ok=False, error_kind="transient", detail="timeout"),
            SendResult(ok=False, error_kind="permanent", detail="bad address"),
        ],
    )
    stats = outbox.send_pending(app_pool, {channel: fake}, max_attempts=2)
    assert stats.detail() == {"sent": 1, "failed": 1, "dead": 1}
    assert _status(app_pool, ok_id) == ("sent", 1, "ext-1")
    assert _status(app_pool, flaky_id) == ("failed", 1, None)
    assert _status(app_pool, bad_id) == ("dead", 1, None)
    assert _status(app_pool, other_channel_id) == ("queued", 0, None)
    first = fake.sent[0]
    assert (first.id, first.subject, first.body, first.to_name) == (
        ok_id,
        "Chủ đề",
        "Nội dung",
        "MCP test",
    )

    fake.results = [SendResult(ok=False, error_kind="transient")]
    assert outbox.send_pending(app_pool, {channel: fake}, max_attempts=2).dead == 1
    assert _status(app_pool, flaky_id) == ("dead", 2, None)
    assert outbox.send_pending(app_pool, {channel: fake}, max_attempts=2).detail() == {
        "sent": 0,
        "failed": 0,
        "dead": 0,
    }


def test_outbox_gives_up_on_inactive_user_and_adapter_crash(app_pool: ConnectionPool) -> None:
    channel = f"test-{uuid.uuid4().hex[:6]}"
    who = person(app_pool, _project(app_pool))
    gone = person(app_pool, _project(app_pool))
    crash_id = _queue(app_pool, who, channel, "crash")
    gone_id = _queue(app_pool, gone, channel, "gone")
    _sql(app_pool, "update users set status = 'locked' where id = %s", gone.user_id)

    class Crashing:
        name = channel

        def send(self, message: OutboundMessage) -> SendResult:
            raise ConnectionError("smtp down")

    outbox.send_pending(app_pool, {channel: Crashing()}, max_attempts=3)
    assert _status(app_pool, crash_id) == ("failed", 1, None)
    assert _status(app_pool, gone_id) == ("dead", 1, None)


def test_run_job_is_idempotent(db: Database, app_pool: ConnectionPool) -> None:
    job = f"test_job_{uuid.uuid4().hex[:6]}"
    at = datetime(2031, 3, 3, 9, 45, tzinfo=BKK)
    calls: list[int] = []

    def work() -> dict[str, Any]:
        calls.append(1)
        return {"enqueued": 2}

    assert run_job(app_pool, job, at, work) == JobOutcome.SUCCEEDED
    assert run_job(app_pool, job, at, work) == JobOutcome.DUPLICATE  # T-04
    assert len(calls) == 1

    def boom() -> dict[str, Any]:
        raise RuntimeError("secret detail")

    later = at + timedelta(days=1)
    assert run_job(app_pool, job, later, boom) == JobOutcome.FAILED
    assert _sql(
        app_pool,
        "select scheduled_for, status, detail from job_runs where job = %s order by scheduled_for",
        job,
    ) == [
        (at, "succeeded", {"enqueued": 2}),
        (later, "failed", {"error": "RuntimeError"}),
    ]


def test_run_job_skips_while_another_instance_holds_the_lock(
    db: Database, app_pool: ConnectionPool
) -> None:
    job = f"test_job_{uuid.uuid4().hex[:6]}"
    with db.connect("pdca_app") as other:
        other.execute("select pg_advisory_lock(hashtextextended(%s, 0))", (f"pdca_job:{job}",))
        outcome = run_job(app_pool, job, MORNING, lambda: {"ran": 1})
        other.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (f"pdca_job:{job}",))
    assert outcome == JobOutcome.SKIPPED
    assert _sql(app_pool, "select status from job_runs where job = %s", job) == [("skipped",)]


def test_concurrent_progress_ask_sends_one_message_each(app_pool: ConnectionPool) -> None:
    """T-04: hai lần chạy đồng thời cùng giờ → mỗi người đúng một tin."""
    project = _project(app_pool)
    people = [person(app_pool, project) for _ in range(3)]
    barrier = threading.Barrier(2)

    def run() -> None:
        barrier.wait()
        jobs.progress_ask(app_pool, AFTERNOON, _settings(), COMPOSER)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for who in people:
        assert [m[0] for m in _messages(app_pool, who)] == ["progress_ask"]
