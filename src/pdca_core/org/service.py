"""Nghiệp vụ tổ chức cho người đang gọi (tool `whoami`, LLD 4.2)."""

from typing import Any

from psycopg_pool import ConnectionPool

from pdca_core.authz.context import UserContext
from pdca_core.authz.policy import Action, Resource, can
from pdca_core.errors import ForbiddenOrNotFound
from pdca_core.repositories import org
from pdca_core.repositories import plans as plan_repo
from pdca_core.validation import MAX_LIMIT


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


def project_members(ctx: UserContext, pool: ConnectionPool, *, project_id: int) -> dict[str, Any]:
    """Thành viên một project (FR-ORG-03) để tra `assignee_id` từ tên.

    Quyền: thành viên project, trưởng phòng của phòng chứa project, hoặc giám đốc
    (cùng phạm vi với `get_project_status`). Không phân biệt project không tồn tại
    với project không được xem.
    """
    with pool.connection() as conn:
        project = plan_repo.project_scope(conn, project_id)
        if project is None:
            raise ForbiddenOrNotFound()
        scope = Resource(project_id=project_id, department_id=project.department_id)
        if project_id not in ctx.project_ids and not can(ctx, Action.TASK_READ_TEAM, scope):
            raise ForbiddenOrNotFound()
        members = org.list_project_members(conn, project_id, MAX_LIMIT)
    return {
        "project": {"id": project.id, "status": project.status},
        "members": [
            {"user_id": m.user_id, "name": m.name, "role": m.role, "project_role": m.project_role}
            for m in members
        ],
    }
