"""Gửi email thật qua SMTP tới Mailpit (máy chủ thư giả, không gửi ra ngoài).

Kiểm tra cả chuỗi: job xếp hàng → outbox → EmailChannel → SMTP, và header để
ghép trả lời sau này (LLD 6.2).
"""

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from psycopg_pool import ConnectionPool
from testcontainers.core.container import DockerContainer

from adapters.channels import build_channel
from pdca_core.outreach import jobs, outbox
from pdca_core.outreach.compose import TemplateComposer
from pdca_core.outreach.ports import OutboundMessage
from tests.mcp_helpers import person, task

pytestmark = pytest.mark.integration

MAILPIT_IMAGE = "axllent/mailpit:v1.31"  # giữ đồng bộ deploy/docker-compose.yml


@pytest.fixture(scope="module")
def mailpit() -> Iterator[dict[str, str]]:
    container = (
        DockerContainer(MAILPIT_IMAGE)
        .with_exposed_ports(1025, 8025)
        .with_env("MP_SMTP_AUTH_ACCEPT_ANY", "1")
        .with_env("MP_SMTP_AUTH_ALLOW_INSECURE", "1")
    )
    with container:
        host = container.get_container_host_ip()
        api = f"http://{host}:{container.get_exposed_port(8025)}/api/v1"
        deadline = time.monotonic() + 30
        while True:
            try:
                httpx.get(f"{api}/messages", timeout=2).raise_for_status()
                break
            except httpx.HTTPError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
        yield {
            "api": api,
            "SMTP_HOST": host,
            "SMTP_PORT": str(container.get_exposed_port(1025)),
        }


def _env(mailpit: dict[str, str]) -> dict[str, str]:
    return {
        "SMTP_HOST": mailpit["SMTP_HOST"],
        "SMTP_PORT": mailpit["SMTP_PORT"],
        "SMTP_SECURITY": "none",
        "SMTP_USER": "pdca",
        "SMTP_PASSWORD": "dev",
        "SMTP_FROM": "Trợ lý PDCA <pdca@example.com>",
        "SMTP_REPLY_TO": "pdca-reply@example.com",
    }


def _mail_to(mailpit: dict[str, str], address: str) -> list[dict[str, Any]]:
    data = httpx.get(f"{mailpit['api']}/search", params={"query": f"to:{address}"}).json()
    return list(data["messages"])


def test_progress_ask_is_delivered_by_email(
    app_pool: ConnectionPool, mailpit: dict[str, str]
) -> None:
    who = person(app_pool, "Dự án thử nghiệm A")
    task(app_pool, who, "Hoàn thiện báo cáo quý", "2031-03-05")
    with app_pool.connection() as conn:
        row = conn.execute("select email from users where id = %s", (who.user_id,)).fetchone()
    assert row is not None
    address = str(row[0])

    now = datetime(2031, 3, 3, 16, 45, tzinfo=ZoneInfo("Asia/Bangkok"))
    result = jobs.progress_ask(
        app_pool, now, jobs.OutreachSettings(channel="email"), TemplateComposer()
    )
    assert who.user_id in result.enqueued

    channel = build_channel("email", _env(mailpit))
    stats = outbox.send_pending(app_pool, {"email": channel})
    assert stats.sent >= 1

    with app_pool.connection() as conn:
        status, external_id, message_id = conn.execute(
            "select status, external_id, id from outbound_messages"
            " where user_id = %s and kind = 'progress_ask'",
            (who.user_id,),
        ).fetchone()  # type: ignore[misc]
    assert status == "sent"

    [mail] = _mail_to(mailpit, address)
    assert mail["Subject"] == "[PDCA] Tiến độ ngày 03/03"
    assert mail["From"]["Address"] == "pdca@example.com"
    headers = httpx.get(f"{mailpit['api']}/message/{mail['ID']}/headers").json()
    assert headers["X-Pdca-Outbound-Id"] == [str(message_id)]
    assert headers["Auto-Submitted"] == ["auto-generated"]
    assert headers["Reply-To"] == ["pdca-reply@example.com"]
    assert f"<{mail['MessageID']}>" == external_id  # ghép trả lời qua In-Reply-To
    text = httpx.get(f"{mailpit['api']}/message/{mail['ID']}").json()["Text"]
    assert "Trợ lý AI PDCA, thay mặt" in text
    assert "/pdca:chot-ngay" in text


def test_unreachable_smtp_is_transient(app_pool: ConnectionPool) -> None:
    channel = build_channel(
        "email",
        {
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": "1",
            "SMTP_SECURITY": "none",
            "SMTP_FROM": "pdca@example.com",
            "SMTP_TIMEOUT_SECONDS": "2",
        },
    )
    result = channel.send(OutboundMessage(1, "k", "morning_nudge", "A", "a@example.com", "s", "b"))
    assert (result.ok, result.error_kind) == (False, "transient")
