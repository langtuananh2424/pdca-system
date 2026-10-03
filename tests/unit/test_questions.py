"""Logic thuần của hỏi cấp trên: chọn người nhận (FR-ASK-02, OI-11), giờ gửi tin, tin báo."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from pdca_core.audit.log import redact_params
from pdca_core.questions import notices
from pdca_core.questions.delivery import next_delivery
from pdca_core.questions.service import pick_recipient
from pdca_core.repositories.questions import OrgNode

ASKER = 1


def node(user_id: int, role: str, *, active: bool = True) -> OrgNode:
    return OrgNode(user_id=user_id, role=role, name=f"u{user_id}", manager_id=None, active=active)


# --- pick_recipient ---


def test_direct_dept_head_is_chosen() -> None:
    chain = [node(2, "dept_head"), node(3, "director")]
    assert pick_recipient(chain, ASKER) == chain[0]


def test_vacant_dept_head_goes_to_next_level() -> None:
    """OI-11: trưởng phòng bị khóa/xóa → vượt cấp lên giám đốc."""
    chain = [node(2, "dept_head", active=False), node(3, "director")]
    assert pick_recipient(chain, ASKER) == chain[1]


def test_manager_with_wrong_role_is_skipped() -> None:
    chain = [node(2, "staff"), node(3, "dept_head")]
    assert pick_recipient(chain, ASKER) == chain[1]


def test_broken_chain_is_refused() -> None:
    """Chuỗi đứt: không tìm người nhận ngoài chuỗi quản lý (FR-ASK-02)."""
    assert pick_recipient([], ASKER) is None
    assert pick_recipient([node(2, "staff"), node(3, "dept_head", active=False)], ASKER) is None


def test_asker_is_never_the_recipient() -> None:
    me = node(ASKER, "director")
    assert pick_recipient([me], ASKER) is None


# --- next_delivery ---

BKK = ZoneInfo("Asia/Bangkok")
START, END = time(8, 30), time(17, 30)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2031, 3, day, hour, minute, tzinfo=BKK)


def test_in_working_hours_sends_now() -> None:
    assert next_delivery(at(3, 10), "Asia/Bangkok", START, END, None) is None


def test_before_work_waits_until_start_same_day() -> None:
    assert next_delivery(at(3, 6), "Asia/Bangkok", START, END, None) == at(3, 8, 30).astimezone(UTC)


def test_after_work_waits_until_next_day() -> None:
    assert next_delivery(at(3, 19), "Asia/Bangkok", START, END, None) == at(4, 8, 30).astimezone(
        UTC
    )


def test_away_defers_until_day_after_return() -> None:
    result = next_delivery(at(3, 10), "Asia/Bangkok", START, END, date(2031, 3, 5))
    assert result == at(6, 8, 30).astimezone(UTC)


def test_away_in_the_past_is_ignored() -> None:
    assert next_delivery(at(3, 10), "Asia/Bangkok", START, END, date(2031, 3, 2)) is None


def test_uses_recipient_timezone() -> None:
    # 10:00 Bangkok = 03:00 UTC = 20:00 ngày hôm trước ở Los Angeles → ngoài giờ làm.
    now = at(3, 10)
    result = next_delivery(now, "America/Los_Angeles", START, END, None)
    assert result is not None
    assert result.astimezone(ZoneInfo("America/Los_Angeles")).time() == START


# --- tin báo và audit không chứa nội dung (FR-ASK-06, FR-ASK-08) ---


def test_notices_carry_ids_and_names_only() -> None:
    q = notices.question_notice(
        recipient_name="Trưởng phòng", asker_name="Nhân viên", question_id=7
    )
    assert "#7" in q.body and "Nhân viên" in q.body and "/pdca:cau-hoi-den-toi" in q.body
    assert "Trợ lý AI PDCA" in q.body  # FR-NTF-03
    for outcome in ("answered", "declined", "expired"):
        a = notices.answer_notice(
            asker_name="Nhân viên", recipient_name="Trưởng phòng", question_id=7, outcome=outcome
        )
        assert "#7" in a.body and "Trợ lý AI PDCA" in a.body


def test_audit_redaction_keeps_action_but_not_text() -> None:
    redacted = redact_params(
        {
            "question_id": 5,
            "action": "send",
            "body": "nội dung riêng",
            "reason": None,
            "side": "asker",
        }
    )
    assert redacted == {
        "question_id": 5,
        "action": "send",
        "body": {"len": len("nội dung riêng")},
        "reason": None,
        "side": "asker",
    }


def test_question_expire_job_is_scheduled_hourly() -> None:
    from apps.scheduler.settings import DEFAULT_SCHEDULES, Settings

    assert DEFAULT_SCHEDULES["question_expire"] == "0 * * * *"
    env = {"DATABASE_URL": "postgresql://x", "JOB_QUESTION_EXPIRE_CRON": "*/30 * * * *"}
    assert Settings.from_env(env).schedules["question_expire"] == "*/30 * * * *"
