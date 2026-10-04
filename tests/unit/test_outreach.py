from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger

from adapters.channels import build_channel
from apps.scheduler.__main__ import last_fire_time
from apps.scheduler.settings import DEFAULT_SCHEDULES, Settings
from pdca_core.outreach.compose import MessageContext, TemplateComposer
from pdca_core.outreach.jobs import dedupe_key
from pdca_core.outreach.ports import OutboundMessage
from pdca_core.repositories.outreach import OpenTask, ProjectNeed

TODAY = date(2031, 3, 3)
BKK = ZoneInfo("Asia/Bangkok")


def _ctx(manager: str | None = "Trưởng phòng A", tasks: int = 2) -> MessageContext:
    all_tasks = [
        OpenTask(1, "Sửa lỗi đăng nhập", "Dự án A", date(2031, 3, 1), "in_progress"),
        OpenTask(2, "Viết tài liệu", "Dự án A", TODAY, "todo"),
        OpenTask(3, "Chuẩn bị demo", "Dự án B", date(2031, 3, 10), "blocked"),
    ] + [OpenTask(10 + i, f"Việc {i}", "Dự án B", None, "todo") for i in range(12)]
    return MessageContext(
        name="Lan",
        manager_name=manager,
        local_date=TODAY,
        tasks=all_tasks[:tasks],
        projects=[
            ProjectNeed(
                1, "Dự án A", {"check": {"questions": ["Có blocker kỹ thuật không?"]}}, False
            ),
            ProjectNeed(2, "Dự án B", {"check": {"questions": ["Không hỏi vì đã nộp"]}}, True),
            ProjectNeed(3, "Dự án C", {"kind": "other"}, False),
        ],
    )


def test_dedupe_key_follows_lld_5_3() -> None:
    assert dedupe_key("progress_ask", 7, TODAY) == "progress_ask:7:2031-03-03"
    assert dedupe_key("progress_ask", 7, TODAY, reminder=True) == "progress_ask:7:2031-03-03:r1"


def test_every_message_says_it_is_an_ai_assistant() -> None:
    """FR-NTF-03."""
    composer = TemplateComposer()
    for ctx in (_ctx(), _ctx(manager=None)):
        for draft in (
            composer.morning_nudge(ctx),
            composer.progress_ask(ctx),
            composer.progress_remind(ctx),
        ):
            assert "Trợ lý AI PDCA, thay mặt" in draft.body
            assert "không phải" in draft.body
    assert "thay mặt Trưởng phòng A" in composer.morning_nudge(_ctx()).body
    assert "thay mặt cấp trên của bạn" in composer.morning_nudge(_ctx(manager=None)).body


def test_morning_nudge_lists_tasks_with_due_markers() -> None:
    body = TemplateComposer().morning_nudge(_ctx(tasks=3)).body
    assert "[Dự án A] Sửa lỗi đăng nhập (in_progress) — QUÁ HẠN" in body
    assert "Viết tài liệu (todo) — hạn HÔM NAY" in body
    assert "Chuẩn bị demo (blocked) — hạn 10/03" in body
    many = TemplateComposer().morning_nudge(_ctx(tasks=15)).body
    assert "… và 5 task khác" in many
    empty = TemplateComposer().morning_nudge(_ctx(tasks=0)).body
    assert "không có task đang mở" in empty


def test_progress_ask_covers_only_unreported_projects_with_their_questions() -> None:
    """FR-CHK-07: câu hỏi theo cấu hình project."""
    draft = TemplateComposer().progress_ask(_ctx())
    assert "* Dự án A\n  - Có blocker kỹ thuật không?" in draft.body
    assert "* Dự án C" in draft.body
    assert "Dự án B" not in draft.body
    assert "/pdca:chot-ngay" in draft.body
    assert draft.subject == "[PDCA] Tiến độ ngày 03/03"
    assert "một lần duy nhất" in TemplateComposer().progress_remind(_ctx()).body


@pytest.mark.parametrize(
    ("now", "job", "expected"),
    [
        (datetime(2031, 3, 3, 16, 47, tzinfo=BKK), "progress_ask", datetime(2031, 3, 3, 16, 45)),
        (datetime(2031, 3, 3, 16, 45, tzinfo=BKK), "progress_ask", datetime(2031, 3, 3, 16, 45)),
        # Thứ Hai 08:00: lần gần nhất là thứ Sáu tuần trước.
        (datetime(2031, 3, 3, 8, 0, tzinfo=BKK), "progress_remind", datetime(2031, 2, 28, 17, 15)),
    ],
)
def test_last_fire_time(now: datetime, job: str, expected: datetime) -> None:
    trigger = CronTrigger.from_crontab(DEFAULT_SCHEDULES[job], timezone=BKK)
    assert last_fire_time(trigger, now) == expected.replace(tzinfo=BKK)


def test_scheduler_settings() -> None:
    s = Settings.from_env(
        {"DATABASE_URL": "postgresql://x", "JOB_PROGRESS_ASK_CRON": "0 17 * * mon-fri"}
    )
    assert s.schedules["progress_ask"] == "0 17 * * mon-fri"
    assert s.schedules["morning_nudge"] == DEFAULT_SCHEDULES["morning_nudge"]
    assert (s.timezone, s.channel_kind, s.max_messages_per_day) == ("Asia/Bangkok", "log", 3)
    for expr in s.schedules.values():
        CronTrigger.from_crontab(expr, timezone=BKK)
    with pytest.raises(SystemExit):
        Settings.from_env({})


def test_channel_factory() -> None:
    channel = build_channel("log")
    result = channel.send(OutboundMessage(5, "k", "morning_nudge", "Lan", "lan@x", "s", "b"))
    assert (result.ok, result.external_id) == (True, "log-5")
    with pytest.raises(ValueError, match="unsupported CHANNEL_KIND"):
        build_channel("zalo")
