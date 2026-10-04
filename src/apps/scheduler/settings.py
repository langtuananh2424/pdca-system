"""Cấu hình Scheduler từ biến môi trường (LLD 5.2, 8.1, FR-NTF-01)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

# LLD 5.2, cú pháp crontab 5 trường theo TZ_DEFAULT. Lệch LLD: progress_remind 17:15
# (thay 17:45) để nằm trong giờ làm mặc định 08:30–17:30 — ngoài giờ thì không gửi.
DEFAULT_SCHEDULES = {
    "morning_nudge": "30 8 * * mon-fri",
    "progress_ask": "45 16 * * mon-fri",
    "progress_remind": "15 17 * * mon-fri",
    "mark_not_reported": "30 18 * * mon-fri",
    "question_expire": "0 * * * *",  # mỗi giờ (LLD 5.10)
}


@dataclass(frozen=True)
class Settings:
    database_url: str
    timezone: str
    channel_kind: str
    max_messages_per_day: int
    outbox_max_attempts: int
    outbox_interval_seconds: int
    question_ttl_days: int = 3
    schedules: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        database_url = env.get("DATABASE_URL")
        if not database_url:
            raise SystemExit("DATABASE_URL is not set")
        schedules = {
            job: env.get(f"JOB_{job.upper()}_CRON") or default
            for job, default in DEFAULT_SCHEDULES.items()
        }
        return cls(
            database_url=database_url,
            timezone=env.get("TZ_DEFAULT", "Asia/Bangkok"),
            channel_kind=env.get("CHANNEL_KIND", "log"),
            max_messages_per_day=int(env.get("NTF_MAX_PER_DAY", "3")),
            outbox_max_attempts=int(env.get("OUTBOX_MAX_ATTEMPTS", "5")),
            outbox_interval_seconds=int(env.get("OUTBOX_INTERVAL_SECONDS", "60")),
            question_ttl_days=int(env.get("QUESTION_TTL_DAYS", "3")),
            schedules=schedules,
        )
