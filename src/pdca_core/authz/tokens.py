"""Token API: cấp, băm, xác thực, thu hồi (LLD 3.1, FR-AUTH-01..04)."""

import hashlib
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from pdca_core.authz.context import Role, UserContext
from pdca_core.errors import ForbiddenOrNotFound, InvalidArgument, Unauthorized

TOKEN_PREFIX = "pdca_"  # noqa: S105 — tiền tố công khai (LLD 3.1)
TOKEN_BYTES = 32
DEFAULT_TTL = timedelta(days=90)
# 32 byte base64url không đệm = 43 ký tự.
_TOKEN_RE = re.compile(r"pdca_[A-Za-z0-9_-]{43}")


@dataclass(frozen=True, slots=True)
class TokenOwner:
    token_id: int
    user_id: int
    role: str
    department_id: int | None


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token_id: int
    token: str  # chỉ hiển thị đúng một lần lúc cấp
    expires_at: datetime


class TokenRepository(Protocol):
    def find_active(self, token_hash: bytes) -> TokenOwner | None:
        """Token chưa hết hạn, chưa thu hồi, người dùng active và chưa xóa."""
        ...

    def project_ids(self, user_id: int) -> Iterable[int]: ...

    def touch(self, token_id: int) -> None:
        """Cập nhật `last_used_at`; được phép gom/bỏ qua để không chặn đường nóng."""
        ...

    def insert(
        self, user_id: int, token_hash: bytes, label: str | None, ttl: timedelta
    ) -> tuple[int, datetime] | None:
        """Trả (id, expires_at), hoặc None nếu người dùng không tồn tại/không active."""
        ...

    def revoke(self, token_id: int) -> bool:
        """Trả False nếu không có token đang hiệu lực với id này."""
        ...


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def authenticate(token: str | None, repo: TokenRepository, request_id: str) -> UserContext:
    """Dựng `UserContext` từ token; mọi trường hợp hỏng đều là `Unauthorized`."""
    if token is None or not _TOKEN_RE.fullmatch(token):
        raise Unauthorized()
    owner = repo.find_active(hash_token(token))
    if owner is None:
        raise Unauthorized()
    try:
        role = Role(owner.role)
    except ValueError:
        raise Unauthorized() from None
    repo.touch(owner.token_id)
    return UserContext(
        user_id=owner.user_id,
        role=role,
        department_id=owner.department_id,
        project_ids=frozenset(repo.project_ids(owner.user_id)),
        request_id=request_id,
    )


def issue_token(
    repo: TokenRepository,
    user_id: int,
    label: str | None = None,
    ttl: timedelta = DEFAULT_TTL,
) -> IssuedToken:
    """Cấp token mới. Xoay vòng = cấp mới rồi thu hồi cũ (LLD 3.1)."""
    if ttl <= timedelta(0):
        raise InvalidArgument("ttl must be positive")
    token = generate_token()
    inserted = repo.insert(user_id, hash_token(token), label, ttl)
    if inserted is None:
        raise InvalidArgument("user not found or not active")
    token_id, expires_at = inserted
    return IssuedToken(token_id=token_id, token=token, expires_at=expires_at)


def revoke_token(repo: TokenRepository, token_id: int) -> None:
    if not repo.revoke(token_id):
        raise ForbiddenOrNotFound()
