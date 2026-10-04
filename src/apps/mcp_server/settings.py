"""Cấu hình MCP Server từ biến môi trường (LLD 8.1, DC-04)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    rate_limit_per_min: int
    output_max_rows: int
    output_max_bytes: int
    host: str
    port: int
    allowed_hosts: list[str]
    db_pool_max: int
    channel_kind: str
    question_ttl_days: int
    question_max_open: int
    question_max_per_day: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Settings":
        database_url = env.get("DATABASE_URL")
        if not database_url:
            raise SystemExit("DATABASE_URL is not set")
        return cls(
            database_url=database_url,
            rate_limit_per_min=int(env.get("RATE_LIMIT_PER_MIN", "60")),
            output_max_rows=int(env.get("OUTPUT_MAX_ROWS", "100")),
            output_max_bytes=int(env.get("OUTPUT_MAX_BYTES", str(50 * 1024))),
            host=env.get("MCP_HOST", "127.0.0.1"),
            port=int(env.get("MCP_PORT", "8000")),
            # Chống DNS rebinding của SDK: chỉ nhận Host trong danh sách.
            allowed_hosts=[
                h.strip()
                for h in env.get("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*").split(",")
                if h.strip()
            ],
            db_pool_max=int(env.get("DB_POOL_MAX", "10")),
            # Kênh của tin báo hỏi–đáp: phải trùng CHANNEL_KIND của scheduler (bộ gửi chỉ
            # lấy tin của kênh nó đang chạy).
            channel_kind=env.get("CHANNEL_KIND", "log"),
            question_ttl_days=int(env.get("QUESTION_TTL_DAYS", "3")),
            question_max_open=int(env.get("QUESTION_MAX_OPEN", "5")),
            question_max_per_day=int(env.get("QUESTION_MAX_PER_DAY", "10")),
        )
