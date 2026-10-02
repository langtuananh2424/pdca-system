"""Tra cứu người dùng phục vụ quản trị."""

from psycopg_pool import ConnectionPool


class PgUserRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def id_by_email(self, email: str) -> int | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "select id from users where lower(email) = lower(%s) and deleted_at is null",
                (email,),
            ).fetchone()
        return None if row is None else int(row[0])
