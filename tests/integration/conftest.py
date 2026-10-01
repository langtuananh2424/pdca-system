"""Hạ tầng test tích hợp: Postgres + Flyway thật bằng Testcontainers.

Dựng giống deploy/docker-compose.yml: cùng image, cùng script tạo vai trò
(deploy/initdb), Flyway chạy với db/migration + db/seed.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network

ROOT = Path(__file__).resolve().parents[2]
POSTGRES_IMAGE = "postgres:16-alpine"  # giữ đồng bộ deploy/docker-compose.yml
FLYWAY_IMAGE = "flyway/flyway:12-alpine"

PASSWORDS = {
    "postgres": "pg_test",
    "flyway": "flyway_test",
    "pdca_app": "app_test",
    "pdca_readonly": "ro_test",
}

Conn = psycopg.Connection[tuple[object, ...]]


@dataclass(frozen=True)
class Database:
    host: str
    port: int
    network: Network

    def conninfo(self, role: str) -> str:
        return (
            f"host={self.host} port={self.port} dbname=pdca user={role} password={PASSWORDS[role]}"
        )

    def connect(self, role: str) -> Conn:
        return psycopg.connect(self.conninfo(role))

    def run_flyway(self, *args: str, seed: bool = True) -> str:
        """Chạy Flyway trong container cùng mạng; lỗi nếu exit code khác 0."""
        locations = "filesystem:/flyway/sql" + (",filesystem:/flyway/seed" if seed else "")
        flyway = (
            DockerContainer(FLYWAY_IMAGE)
            .with_network(self.network)
            .with_volume_mapping(str(ROOT / "db" / "migration"), "/flyway/sql", "ro")
            .with_volume_mapping(str(ROOT / "db" / "seed"), "/flyway/seed", "ro")
            .with_env("FLYWAY_URL", "jdbc:postgresql://db:5432/pdca")
            .with_env("FLYWAY_USER", "flyway")
            .with_env("FLYWAY_PASSWORD", PASSWORDS["flyway"])
            .with_env("FLYWAY_LOCATIONS", locations)
            .with_command(list(args) or ["migrate"])
        )
        flyway.start()
        try:
            wrapped = flyway.get_wrapped_container()
            status = wrapped.wait(timeout=180)["StatusCode"]
            logs: str = wrapped.logs().decode("utf-8", errors="replace")
        finally:
            flyway.stop()
        if status != 0:
            raise AssertionError(f"flyway exited {status}:\n{logs}")
        return logs


@pytest.fixture(scope="session")
def db() -> Iterator[Database]:
    with Network() as network:
        postgres = (
            PostgresContainer(
                POSTGRES_IMAGE,
                username="postgres",
                password=PASSWORDS["postgres"],
                dbname="pdca",
            )
            .with_network(network)
            .with_network_aliases("db")
            .with_volume_mapping(
                str(ROOT / "deploy" / "initdb"), "/docker-entrypoint-initdb.d", "ro"
            )
            .with_env("FLYWAY_PASSWORD", PASSWORDS["flyway"])
            .with_env("PDCA_APP_PASSWORD", PASSWORDS["pdca_app"])
            .with_env("PDCA_READONLY_PASSWORD", PASSWORDS["pdca_readonly"])
        )
        with postgres:
            database = Database(
                host=postgres.get_container_host_ip(),
                port=int(postgres.get_exposed_port(5432)),
                network=network,
            )
            database.run_flyway("migrate")
            yield database


@pytest.fixture(scope="session")
def app_pool(db: Database) -> Iterator[ConnectionPool]:
    """Pool với vai trò pdca_app — đúng tài khoản ứng dụng dùng (LLD 2.3)."""
    with ConnectionPool(db.conninfo("pdca_app"), min_size=1, max_size=4) as pool:
        yield pool


@pytest.fixture
def app(db: Database) -> Iterator[Conn]:
    """Kết nối pdca_app; mọi thay đổi bị rollback sau test."""
    with db.connect("pdca_app") as conn:
        yield conn
        conn.rollback()
