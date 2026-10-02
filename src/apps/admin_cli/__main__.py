"""CLI quản trị `pdca-admin` (SDD 4.7.3).

- `token issue|revoke`: cấp/thu hồi token (LLD 3.1). Chạy trên máy chủ với
  `DATABASE_URL`; mỗi thao tác ghi audit `actor_kind=admin_cli`.
- `rules build [--check]`: ghép rule vào plugin Claude Code (LLD 7.1); không cần DB.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from psycopg_pool import ConnectionPool

from adapters.rules import plugin_build
from adapters.rules.composer import RuleFormatError
from pdca_core.audit.log import ActorKind, AuditEntry, AuditResult, redact_params
from pdca_core.authz.tokens import DEFAULT_TTL, issue_token, revoke_token
from pdca_core.errors import InvalidArgument, ToolError
from pdca_core.repositories.audit import PgAuditRepository
from pdca_core.repositories.tokens import PgTokenRepository
from pdca_core.repositories.users import PgUserRepository
from pdca_core.tool_runner import new_request_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdca-admin")
    sub = parser.add_subparsers(dest="group", required=True)

    token = sub.add_parser("token", help="quản lý API token").add_subparsers(
        dest="command", required=True
    )
    issue = token.add_parser("issue", help="cấp token mới (hiển thị một lần)")
    issue.add_argument("--email", required=True)
    issue.add_argument("--label")
    issue.add_argument("--days", type=int, default=DEFAULT_TTL.days)
    revoke = token.add_parser("revoke", help="thu hồi token")
    revoke.add_argument("--id", type=int, required=True, dest="token_id")

    rules = sub.add_parser("rules", help="rule ba lớp").add_subparsers(
        dest="command", required=True
    )
    build = rules.add_parser("build", help="ghép rule vào plugin Claude Code")
    build.add_argument("--rules-dir", type=Path, default=Path("rules"))
    build.add_argument("--plugin-dir", type=Path, default=Path("plugin"))
    build.add_argument(
        "--check", action="store_true", help="chỉ kiểm tra plugin đã cập nhật (dùng trong CI)"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.group == "rules":
        return _rules_build(args.rules_dir, args.plugin_dir, check=args.check)
    return _token(args)


def _rules_build(rules_dir: Path, plugin_dir: Path, *, check: bool) -> int:
    try:
        result = plugin_build.plan(rules_dir, plugin_dir)
    except RuleFormatError as exc:
        print(f"invalid rules: {exc}", file=sys.stderr)
        return 1
    for note in result.skipped:
        print(f"skipped: {note}", file=sys.stderr)
    if check:
        for path in result.stale:
            print(f"stale: {path.as_posix()}", file=sys.stderr)
        if result.stale:
            print("run: uv run pdca-admin rules build", file=sys.stderr)
        return 1 if result.stale else 0
    plugin_build.write(result)
    for path in result.stale:
        print(f"updated: {path.as_posix()}")
    return 0


def _token(args: argparse.Namespace) -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    with ConnectionPool(database_url, min_size=1, max_size=1) as pool:
        audit = PgAuditRepository(pool)
        tokens = PgTokenRepository(pool)
        tool = f"admin.{args.group}.{args.command}"
        params = {k: v for k, v in vars(args).items() if k not in ("group", "command")}
        error: ToolError | None = None
        try:
            if args.command == "issue":
                user_id = PgUserRepository(pool).id_by_email(args.email)
                if user_id is None:
                    raise InvalidArgument("user not found")
                issued = issue_token(tokens, user_id, args.label, timedelta(days=args.days))
                print(f"token_id:   {issued.token_id}")
                print(f"expires_at: {issued.expires_at.isoformat()}")
                print(f"token:      {issued.token}")
                print(
                    "Token chỉ hiển thị một lần; nhập vào ô API token khi bật plugin pdca"
                    " trong Claude Code."
                )
            else:
                revoke_token(tokens, args.token_id)
                print(f"revoked token {args.token_id}")
        except ToolError as exc:
            error = exc
            print(f"{exc.code.value}: {exc.message}", file=sys.stderr)
        finally:
            audit.write(
                AuditEntry(
                    request_id=new_request_id(),
                    user_id=None,
                    actor_kind=ActorKind.ADMIN_CLI,
                    tool=tool,
                    params_redacted=redact_params(params),
                    result=AuditResult.OK if error is None else AuditResult.ERROR,
                    error_code=error.code.value if error else None,
                )
            )
    return 0 if error is None else 1


if __name__ == "__main__":
    sys.exit(main())
