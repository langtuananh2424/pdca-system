"""Nghiệp vụ tổ chức cho người đang gọi (tool `whoami`, LLD 4.2)."""

from typing import Any

from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.errors import ForbiddenOrNotFound
from pdca_core.repositories import org


def whoami(ctx: UserContext, pool: ConnectionPool) -> dict[str, Any]:
    """Mọi vai trò; chỉ trả thông tin của chính người gọi (FR-AUTH-02)."""
    with pool.connection() as conn:
        profile = org.get_profile(conn, ctx.user_id)
        memberships = org.list_memberships(conn, ctx.user_id)
    if profile is None:
        raise ForbiddenOrNotFound()
    department = (
        {"id": profile.department_id, "name": profile.department_name}
        if profile.department_id is not None
        else None
    )
    return {
        "user_id": profile.user_id,
        "name": profile.name,
        "role": profile.role,
        "department": department,
        "projects": [
            {"id": m.project_id, "name": m.project_name, "project_role": m.project_role}
            for m in memberships
        ],
    }
