"""Scheduler: `python -m apps.scheduler` (LLD 5.2, FR-NTF-01).

Chạy các job liên lạc chủ động theo lịch cron (múi giờ `TZ_DEFAULT`) qua
`run_job` (idempotent), và gửi hàng đợi tin theo chu kỳ. Gọi thẳng hàm nghiệp
vụ trong `pdca_core` (ADR-011). Lệch HLD: P1 gộp bộ gửi tin vào tiến trình này;
Agent Service (nhận/phân tích trả lời) thêm khi có kênh thật.
"""

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from psycopg_pool import ConnectionPool

from adapters.channels import build_channel
from apps import json_logging
from apps.scheduler.settings import Settings
from pdca_core.job_runner import run_job
from pdca_core.outreach import jobs, outbox
from pdca_core.outreach.compose import TemplateComposer

logger = logging.getLogger("pdca.scheduler")

JobFn = Callable[[datetime], Mapping[str, Any]]


def job_functions(pool: ConnectionPool, settings: Settings) -> dict[str, JobFn]:
    outreach = jobs.OutreachSettings(
        channel=settings.channel_kind, max_messages_per_day=settings.max_messages_per_day
    )
    composer = TemplateComposer()
    tz = ZoneInfo(settings.timezone)
    return {
        "morning_nudge": lambda now: jobs.morning_nudge(pool, now, outreach, composer).detail(),
        "progress_ask": lambda now: jobs.progress_ask(pool, now, outreach, composer).detail(),
        "progress_remind": lambda now: jobs.progress_remind(pool, now, outreach, composer).detail(),
        "mark_not_reported": lambda now: jobs.mark_not_reported(
            pool, now.astimezone(tz).date()
        ).detail(),
    }


def last_fire_time(trigger: CronTrigger, now: datetime) -> datetime:
    """Thời điểm theo lịch gần nhất ≤ now: khóa `job_runs` ổn định kể cả khi job chạy trễ."""
    fire: datetime | None = trigger.get_next_fire_time(None, now - timedelta(days=8))
    previous = fire
    while fire is not None and fire <= now:
        previous = fire
        fire = trigger.get_next_fire_time(fire, fire + timedelta(seconds=1))
    if previous is None or previous > now:
        return now.replace(second=0, microsecond=0)
    return previous


def make_runner(
    pool: ConnectionPool, name: str, trigger: CronTrigger, fn: JobFn
) -> Callable[[], None]:
    def run() -> None:
        now = datetime.now(UTC)
        scheduled_for = last_fire_time(trigger, now).astimezone(UTC)
        run_job(pool, name, scheduled_for, lambda: fn(now))

    return run


def main() -> None:
    json_logging.configure()
    settings = Settings.from_env()
    sender = build_channel(settings.channel_kind, os.environ)
    tz = ZoneInfo(settings.timezone)

    with ConnectionPool(settings.database_url, min_size=2, max_size=4, open=True) as pool:
        scheduler = BlockingScheduler(timezone=tz)
        for name, fn in job_functions(pool, settings).items():
            trigger = CronTrigger.from_crontab(settings.schedules[name], timezone=tz)
            scheduler.add_job(
                make_runner(pool, name, trigger, fn),
                trigger,
                id=name,
                coalesce=True,
                misfire_grace_time=600,
                max_instances=1,
            )
        scheduler.add_job(
            lambda: logger.info(
                "outbox",
                extra=outbox.send_pending(
                    pool, {sender.name: sender}, max_attempts=settings.outbox_max_attempts
                ).detail(),
            ),
            "interval",
            seconds=settings.outbox_interval_seconds,
            id="outbox_send",
            coalesce=True,
            max_instances=1,
        )
        logger.info(
            "scheduler started",
            extra={"timezone": settings.timezone, "channel": sender.name, **settings.schedules},
        )
        scheduler.start()


if __name__ == "__main__":
    main()
