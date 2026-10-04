"""Ghi `audit_log` — chỉ thêm (FR-AUD-02); vai trò pdca_app không có quyền sửa/xóa."""

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from pdca_core.audit.log import AuditEntry


class PgAuditRepository:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def write(self, entry: AuditEntry) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                insert into audit_log
                  (request_id, user_id, actor_kind, tool, params_redacted, result, error_code)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    entry.request_id,
                    entry.user_id,
                    entry.actor_kind.value,
                    entry.tool,
                    Jsonb(entry.params_redacted),
                    entry.result.value,
                    entry.error_code,
                ),
            )
