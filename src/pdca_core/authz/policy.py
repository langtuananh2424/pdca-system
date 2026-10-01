"""Ma trận quyền và hàm `can` (LLD 3.2, SDD 4.11.1).

Hai lớp (NFR-SEC-02): (1) vai trò có hành động trong ma trận không,
(2) tài nguyên có nằm trong phạm vi của vai trò đó không. Mặc định từ chối.
"""

from dataclasses import dataclass
from enum import StrEnum

from pdca_core.authz.context import Role, UserContext
from pdca_core.errors import ForbiddenOrNotFound


class Action(StrEnum):
    TASK_READ_OWN = "task.read.own"
    TASK_UPDATE_OWN = "task.update.own"
    TASK_CREATE = "task.create"
    TASK_ASSIGN = "task.assign"
    REPORT_SUBMIT_OWN = "report.submit.own"
    REPORT_READ_OWN = "report.read.own"
    REPORT_READ_TEAM = "report.read.team"
    BLOCKERS_READ_TEAM = "blockers.read.team"
    PLAN_READ = "plan.read"
    PLAN_WRITE = "plan.write"
    ACTION_DECIDE = "action.decide"
    SUMMARY_READ = "summary.read"
    USER_MANAGE = "user.manage"
    TOKEN_MANAGE = "token.manage"  # noqa: S105 — tên hành động, không phải bí mật
    AUDIT_READ = "audit.read"


class Scope(StrEnum):
    """Phạm vi tài nguyên mà một vai trò được thao tác cho một hành động."""

    OWN = "own"  # tài nguyên của chính người gọi ("của mình")
    PROJECT = "project"  # project người gọi là thành viên ("theo project")
    DEPARTMENT = "department"  # thuộc phòng của người gọi ("trong phòng")
    ALL = "all"


_OWN_STAFF_UP = {Role.STAFF: Scope.OWN, Role.DEPT_HEAD: Scope.OWN, Role.DIRECTOR: Scope.OWN}
_TEAM = {Role.DEPT_HEAD: Scope.DEPARTMENT, Role.DIRECTOR: Scope.ALL}
_ADMIN = {Role.ADMIN: Scope.ALL}

# LLD bảng 3.2 — nguồn dữ liệu của bộ kiểm thử ma trận quyền.
MATRIX: dict[Action, dict[Role, Scope]] = {
    Action.TASK_READ_OWN: _OWN_STAFF_UP,
    Action.TASK_UPDATE_OWN: _OWN_STAFF_UP,
    Action.TASK_CREATE: _TEAM,
    Action.TASK_ASSIGN: _TEAM,
    Action.REPORT_SUBMIT_OWN: _OWN_STAFF_UP,
    Action.REPORT_READ_OWN: _OWN_STAFF_UP,
    Action.REPORT_READ_TEAM: _TEAM,
    Action.BLOCKERS_READ_TEAM: _TEAM,
    Action.PLAN_READ: {Role.STAFF: Scope.PROJECT, **_TEAM},
    Action.PLAN_WRITE: {Role.STAFF: Scope.OWN, **_TEAM},
    Action.ACTION_DECIDE: _TEAM,
    Action.SUMMARY_READ: _TEAM,
    Action.USER_MANAGE: _ADMIN,
    Action.TOKEN_MANAGE: _ADMIN,
    Action.AUDIT_READ: _ADMIN,
}


@dataclass(frozen=True, slots=True)
class Resource:
    """Thuộc tính phân quyền của tài nguyên, do tầng nghiệp vụ đọc từ DB.

    `department_id` là phòng sở hữu tài nguyên (với project: `projects.department_id`).
    Trường để trống nghĩa là không thuộc phạm vi đó → phạm vi tương ứng từ chối.
    """

    owner_id: int | None = None
    project_id: int | None = None
    department_id: int | None = None


def can(ctx: UserContext, action: Action, resource: Resource) -> bool:
    scope = MATRIX.get(action, {}).get(ctx.role)
    match scope:
        case Scope.ALL:
            return True
        case Scope.OWN:
            return resource.owner_id is not None and resource.owner_id == ctx.user_id
        case Scope.PROJECT:
            return resource.project_id is not None and resource.project_id in ctx.project_ids
        case Scope.DEPARTMENT:
            return ctx.department_id is not None and resource.department_id == ctx.department_id
        case _:
            return False


def require(ctx: UserContext, action: Action, resource: Resource) -> None:
    """Như `can` nhưng ném `ForbiddenOrNotFound` khi bị từ chối (LLD 3.3)."""
    if not can(ctx, action, resource):
        raise ForbiddenOrNotFound()
