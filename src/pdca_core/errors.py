"""Lỗi nghiệp vụ dùng chung, ánh xạ 1-1 sang mã lỗi chuẩn của tool (LLD 4.1)."""

from enum import StrEnum
from typing import ClassVar


class ErrorCode(StrEnum):
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN_OR_NOT_FOUND = "forbidden_or_not_found"
    INVALID_ARGUMENT = "invalid_argument"
    CONFLICT = "conflict"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal"


class ToolError(Exception):
    """Lỗi trả về cho người gọi tool. `message` phải an toàn để hiển thị."""

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.code.value
        super().__init__(self.message)


class Unauthorized(ToolError):
    """Token sai/hết hạn/bị thu hồi hoặc tài khoản khóa — không nêu lý do cụ thể (LLD 11)."""

    code = ErrorCode.UNAUTHORIZED

    def __init__(self) -> None:
        super().__init__()


class ForbiddenOrNotFound(ToolError):
    """Không phân biệt "không tồn tại" với "không được xem" (LLD 3.3)."""

    code = ErrorCode.FORBIDDEN_OR_NOT_FOUND

    def __init__(self) -> None:
        super().__init__()


class InvalidArgument(ToolError):
    code = ErrorCode.INVALID_ARGUMENT


class Conflict(ToolError):
    code = ErrorCode.CONFLICT


class RateLimited(ToolError):
    code = ErrorCode.RATE_LIMITED

    def __init__(self) -> None:
        super().__init__()


class Internal(ToolError):
    """Lỗi không lường trước; chi tiết chỉ ghi log, không trả cho người gọi."""

    code = ErrorCode.INTERNAL

    def __init__(self) -> None:
        super().__init__()
