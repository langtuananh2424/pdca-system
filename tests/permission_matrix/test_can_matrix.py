"""Ma trận quyền `can` — tham số hóa từ LLD bảng 3.2.

Bảng kỳ vọng dưới đây chép độc lập từ tài liệu (không đọc `policy.MATRIX`)
để phát hiện lệch giữa mã và thiết kế.
"""

import pytest

from pdca_core.authz.context import Role, UserContext
from pdca_core.authz.policy import MATRIX, Action, Resource, can, require
from pdca_core.errors import ForbiddenOrNotFound

# "-" = không có quyền; own = "của mình"; project = "theo project";
# dept = "trong phòng"; all = ✓.
#                        staff      dept_head  director  admin
LLD_3_2: dict[str, tuple[str, str, str, str]] = {
    "task.read.own":      ("own",     "own",     "own",    "-"),
    "task.update.own":    ("own",     "own",     "own",    "-"),
    "task.create":        ("-",       "dept",    "all",    "-"),
    "task.assign":        ("-",       "dept",    "all",    "-"),
    "report.submit.own":  ("own",     "own",     "own",    "-"),
    "report.read.own":    ("own",     "own",     "own",    "-"),
    "report.read.team":   ("-",       "dept",    "all",    "-"),
    "blockers.read.team": ("-",       "dept",    "all",    "-"),
    "plan.read":          ("project", "dept",    "all",    "-"),
    "plan.write":         ("own",     "dept",    "all",    "-"),
    "action.decide":      ("-",       "dept",    "all",    "-"),
    "summary.read":       ("-",       "dept",    "all",    "-"),
    "user.manage":        ("-",       "-",       "-",      "all"),
    "token.manage":       ("-",       "-",       "-",      "all"),
    "audit.read":         ("-",       "-",       "-",      "all"),
}  # fmt: skip
ROLES = (Role.STAFF, Role.DEPT_HEAD, Role.DIRECTOR, Role.ADMIN)

ME, PEER, STRANGER = 1, 2, 3
MY_DEPT, OTHER_DEPT = 10, 20
MY_PROJECT, DEPT_PROJECT, OTHER_PROJECT = 100, 200, 300

# Tài nguyên theo quan hệ với người gọi (thành viên MY_PROJECT, thuộc MY_DEPT).
RESOURCES = {
    "own": Resource(owner_id=ME, project_id=MY_PROJECT, department_id=MY_DEPT),
    "own_in_other_dept": Resource(owner_id=ME, project_id=OTHER_PROJECT, department_id=OTHER_DEPT),
    "peer_same_project": Resource(owner_id=PEER, project_id=MY_PROJECT, department_id=MY_DEPT),
    "peer_same_dept": Resource(owner_id=PEER, project_id=DEPT_PROJECT, department_id=MY_DEPT),
    "other_dept": Resource(owner_id=STRANGER, project_id=OTHER_PROJECT, department_id=OTHER_DEPT),
    "unscoped": Resource(),
}
ALLOWED_BY_SCOPE: dict[str, set[str]] = {
    "-": set(),
    "own": {"own", "own_in_other_dept"},
    "project": {"own", "peer_same_project"},
    "dept": {"own", "peer_same_project", "peer_same_dept"},
    "all": set(RESOURCES),
}


def _ctx(role: Role, department_id: int | None = MY_DEPT) -> UserContext:
    return UserContext(
        user_id=ME,
        role=role,
        department_id=department_id,
        project_ids=frozenset({MY_PROJECT}),
        request_id="t",
    )


def test_matrix_covers_exactly_the_documented_actions() -> None:
    assert {a.value for a in Action} == set(LLD_3_2)
    assert set(MATRIX) == set(Action)


CASES = [
    pytest.param(action, role, name, id=f"{action}-{role.value}-{name}")
    for action in LLD_3_2
    for role in ROLES
    for name in RESOURCES
]


@pytest.mark.parametrize(("action", "role", "resource_name"), CASES)
def test_can_matches_lld(action: str, role: Role, resource_name: str) -> None:
    scope = LLD_3_2[action][ROLES.index(role)]
    expected = resource_name in ALLOWED_BY_SCOPE[scope]
    ctx = _ctx(role)
    resource = RESOURCES[resource_name]
    assert can(ctx, Action(action), resource) is expected
    if expected:
        require(ctx, Action(action), resource)
    else:
        with pytest.raises(ForbiddenOrNotFound):
            require(ctx, Action(action), resource)


@pytest.mark.parametrize("action", [a for a in Action if LLD_3_2[a.value][1] == "dept"])
def test_dept_head_without_department_gets_nothing_team_wide(action: Action) -> None:
    ctx = _ctx(Role.DEPT_HEAD, department_id=None)
    for resource in RESOURCES.values():
        assert not can(ctx, action, resource)


def test_unknown_action_is_denied() -> None:
    assert not can(_ctx(Role.DIRECTOR), "made.up.action", Resource())  # type: ignore[arg-type]
