"""Truy cập `api_tokens` (LLD 2.2, 3.1)."""

from collections.abc import Iterable
from datetime import datetime, timedelta

from psycopg_pool import ConnectionPool

from pdca_core.authz.tokens import TokenOwner

# Không ghi `last_used_at` quá một lần mỗi phút cho mỗi token (LLD 3.1: không chặn đường nóng).
_TOUCH_INTERVAL = timedelta(minutes=1)


class PgTokenRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def find_active(self, token_hash: bytes) -> TokenOwner | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                select t.id, u.id, u.role, u.department_id
                from api_tokens t
                join users u on u.id = t.user_id
                where t.token_hash = %s
                  and t.revoked_at is null
                  and t.expires_at > now()
                  and u.status = 'active'
                  and u.deleted_at is null
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        return TokenOwner(token_id=row[0], user_id=row[1], role=row[2], department_id=row[3])

    def project_ids(self, user_id: int) -> Iterable[int]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """
                select pm.project_id
                from project_members pm
                join projects p on p.id = pm.project_id
                where pm.user_id = %s and p.deleted_at is null
                """,
                (user_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def touch(self, token_id: int) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                update api_tokens set last_used_at = now()
                where id = %s and (last_used_at is null or last_used_at < now() - %s)
                """,
                (token_id, _TOUCH_INTERVAL),
            )

    def insert(
        self, user_id: int, token_hash: bytes, label: str | None, ttl: timedelta
    ) -> tuple[int, datetime] | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """
                insert into api_tokens (user_id, token_hash, label, expires_at)
                select u.id, %s, %s, now() + %s
                from users u
                where u.id = %s and u.status = 'active' and u.deleted_at is null
                returning id, expires_at
                """,
                (token_hash, label, ttl, user_id),
            ).fetchone()
        return None if row is None else (row[0], row[1])

    def revoke(self, token_id: int) -> bool:
        with self._pool.connection() as conn:
            cur = conn.execute(
                "update api_tokens set revoked_at = now() where id = %s and revoked_at is null",
                (token_id,),
            )
            return cur.rowcount == 1
