"""Chạy MCP Server: `python -m apps.mcp_server` (Streamable HTTP, endpoint `/mcp`)."""

import uvicorn
from mcp.server.transport_security import TransportSecuritySettings
from psycopg_pool import ConnectionPool
from starlette.applications import Starlette

from adapters.mcp.server import ToolDeps, build_server
from apps import json_logging
from apps.mcp_server.settings import Settings
from pdca_core.output_limits import OutputLimits
from pdca_core.ratelimit import RateLimiter
from pdca_core.repositories.audit import PgAuditRepository
from pdca_core.repositories.tokens import PgTokenRepository


def create_app(settings: Settings, pool: ConnectionPool) -> Starlette:
    deps = ToolDeps(
        pool=pool,
        tokens=PgTokenRepository(pool),
        audit=PgAuditRepository(pool),
        limits=OutputLimits(max_rows=settings.output_max_rows, max_bytes=settings.output_max_bytes),
        rate_limiter=RateLimiter(settings.rate_limit_per_min),
    )
    return build_server(deps).streamable_http_app(
        stateless_http=True,
        json_response=True,
        host=settings.host,
        transport_security=TransportSecuritySettings(allowed_hosts=settings.allowed_hosts),
    )


def main() -> None:
    json_logging.configure()
    settings = Settings.from_env()
    with ConnectionPool(
        settings.database_url, min_size=1, max_size=settings.db_pool_max, open=True
    ) as pool:
        uvicorn.run(
            create_app(settings, pool),
            host=settings.host,
            port=settings.port,
            log_config=None,
            proxy_headers=True,
        )


if __name__ == "__main__":
    main()
