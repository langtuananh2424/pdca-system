"""Chạy job idempotent (SDD 4.11.7, bất biến 9, T-04).

1. Chèn `job_runs(job, scheduled_for)` — trùng (đã chạy/đang chạy lần này) → bỏ qua.
2. Lấy khóa advisory của Postgres theo tên job — không lấy được (instance khác đang
   chạy job này) → `skipped`.
3. Chạy, ghi `succeeded`/`failed` kèm `detail`, nhả khóa.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

logger = logging.getLogger("pdca.jobs")


class JobOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"  # instance khác đang giữ khóa
    DUPLICATE = "duplicate"  # đã có job_runs cho (job, scheduled_for)


def run_job(
    pool: ConnectionPool,
    job: str,
    scheduled_for: datetime,
    fn: Callable[[], Mapping[str, Any] | None],
) -> JobOutcome:
    with pool.connection() as conn:
        row = conn.execute(
            """
            insert into job_runs (job, scheduled_for, status) values (%s, %s, 'running')
            on conflict (job, scheduled_for) do nothing
            returning id
            """,
            (job, scheduled_for),
        ).fetchone()
        conn.commit()
        if row is None:
            logger.info("job duplicate", extra={"job": job, "scheduled_for": scheduled_for})
            return JobOutcome.DUPLICATE
        run_id = row[0]

        lock_key = f"pdca_job:{job}"
        locked = conn.execute(
            "select pg_try_advisory_lock(hashtextextended(%s, 0))", (lock_key,)
        ).fetchone()
        if not locked or not locked[0]:
            _finish(conn, run_id, JobOutcome.SKIPPED, {"reason": "lock held"})
            return JobOutcome.SKIPPED
        try:
            try:
                detail = fn() or {}
            except Exception as exc:
                logger.exception("job failed", extra={"job": job})
                conn.rollback()
                _finish(conn, run_id, JobOutcome.FAILED, {"error": type(exc).__name__})
                return JobOutcome.FAILED
            _finish(conn, run_id, JobOutcome.SUCCEEDED, dict(detail))
            logger.info("job succeeded", extra={"job": job, **dict(detail)})
            return JobOutcome.SUCCEEDED
        finally:
            conn.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (lock_key,))
            conn.commit()


def _finish(
    conn: Connection[Any], run_id: int, outcome: JobOutcome, detail: dict[str, Any]
) -> None:
    conn.execute(
        "update job_runs set status = %s, finished_at = now(), detail = %s where id = %s",
        (outcome.value, Jsonb(detail), run_id),
    )
    conn.commit()
