from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from pdca_core.authz.context import Role
from pdca_core.authz.tokens import (
    TokenOwner,
    authenticate,
    generate_token,
    hash_token,
    issue_token,
    revoke_token,
)
from pdca_core.errors import ForbiddenOrNotFound, InvalidArgument, Unauthorized


class FakeRepo:
    def __init__(self) -> None:
        self.tokens: dict[bytes, TokenOwner] = {}
        self.lookups = 0
        self.touched: list[int] = []
        self.revoked: set[int] = set()

    def find_active(self, token_hash: bytes) -> TokenOwner | None:
        self.lookups += 1
        owner = self.tokens.get(token_hash)
        return None if owner is None or owner.token_id in self.revoked else owner

    def project_ids(self, user_id: int) -> Iterable[int]:
        return [7, 8] if user_id == 42 else []

    def touch(self, token_id: int) -> None:
        self.touched.append(token_id)

    def insert(
        self, user_id: int, token_hash: bytes, label: str | None, ttl: timedelta
    ) -> tuple[int, datetime] | None:
        if user_id != 42:
            return None
        token_id = len(self.tokens) + 1
        self.tokens[token_hash] = TokenOwner(token_id, user_id, "staff", 5)
        return token_id, datetime(2026, 1, 1, tzinfo=UTC) + ttl

    def revoke(self, token_id: int) -> bool:
        if token_id in self.revoked or token_id > len(self.tokens):
            return False
        self.revoked.add(token_id)
        return True


def test_generated_token_format() -> None:
    token = generate_token()
    assert token.startswith("pdca_")
    assert len(token) == 5 + 43
    assert generate_token() != token


def test_hash_is_sha256_digest() -> None:
    assert len(hash_token("pdca_x")) == 32
    assert hash_token("a") != hash_token("b")


def test_issue_then_authenticate() -> None:
    repo = FakeRepo()
    issued = issue_token(repo, 42, "laptop")
    assert issued.expires_at == datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=90)

    ctx = authenticate(issued.token, repo, "req-1")

    assert ctx.user_id == 42
    assert ctx.role is Role.STAFF
    assert ctx.department_id == 5
    assert ctx.project_ids == frozenset({7, 8})
    assert ctx.request_id == "req-1"
    assert repo.touched == [issued.token_id]


@pytest.mark.parametrize(
    "token", [None, "", "Bearer x", "pdca_short", "xxxx_" + "a" * 43, "pdca_" + "!" * 43]
)
def test_malformed_tokens_are_rejected_without_db_lookup(token: str | None) -> None:
    repo = FakeRepo()
    with pytest.raises(Unauthorized):
        authenticate(token, repo, "r")
    assert repo.lookups == 0


def test_unknown_or_revoked_token_is_unauthorized() -> None:
    repo = FakeRepo()
    with pytest.raises(Unauthorized):
        authenticate(generate_token(), repo, "r")
    issued = issue_token(repo, 42)
    revoke_token(repo, issued.token_id)
    with pytest.raises(Unauthorized):
        authenticate(issued.token, repo, "r")


def test_unknown_role_in_db_is_unauthorized() -> None:
    repo = FakeRepo()
    token = generate_token()
    repo.tokens[hash_token(token)] = TokenOwner(1, 42, "superuser", None)
    with pytest.raises(Unauthorized):
        authenticate(token, repo, "r")


def test_issue_rejects_inactive_user_and_bad_ttl() -> None:
    with pytest.raises(InvalidArgument):
        issue_token(FakeRepo(), 99)
    with pytest.raises(InvalidArgument):
        issue_token(FakeRepo(), 42, ttl=timedelta(0))


def test_revoke_unknown_token() -> None:
    with pytest.raises(ForbiddenOrNotFound):
        revoke_token(FakeRepo(), 123)
