import smtplib
from datetime import timedelta
from email import message_from_bytes
from email.header import decode_header, make_header
from typing import Any, ClassVar

import pytest

from adapters.channels import build_channel
from adapters.channels.email_channel import EmailChannel, SmtpSettings
from pdca_core.outreach.outbox import retry_delay
from pdca_core.outreach.ports import OutboundMessage

MSG = OutboundMessage(
    id=42,
    dedupe_key="progress_ask:7:2031-03-03",
    kind="progress_ask",
    to_name="Nguyễn Thị Lan",
    to_address="lan@congty.vn",
    subject="[PDCA] Tiến độ ngày 03/03",
    body="Chào Lan,\nĐã làm / Vướng gì / Xung đột lịch.\n— Trợ lý AI PDCA",
)
ENV = {
    "SMTP_HOST": "smtp.congty.vn",
    "SMTP_FROM": "Trợ lý PDCA <pdca@congty.vn>",
    "SMTP_USER": "pdca@congty.vn",
    "SMTP_PASSWORD": "secret",
    "SMTP_REPLY_TO": "pdca-reply@congty.vn",
}


class FakeSMTP:
    """Thay `smtplib.SMTP`; `error` ném ra ở bước gửi."""

    instances: ClassVar[list["FakeSMTP"]] = []
    error: ClassVar[Exception | None] = None

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent: list[Any] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        self.calls.append("quit")

    def starttls(self, context: Any) -> None:
        self.calls.append("starttls")

    def login(self, user: str, password: str) -> None:
        self.calls.append(f"login:{user}")

    def send_message(self, message: Any) -> None:
        if FakeSMTP.error is not None:
            raise FakeSMTP.error
        self.sent.append(message)


@pytest.fixture
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.error = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)  # email_channel dùng smtplib.SMTP
    return FakeSMTP


def test_settings_from_env() -> None:
    s = SmtpSettings.from_env(ENV)
    assert (s.host, s.port, s.security, s.username) == (
        "smtp.congty.vn",
        587,
        "starttls",
        "pdca@congty.vn",
    )
    assert SmtpSettings.from_env({**ENV, "SMTP_SECURITY": "ssl"}).port == 465
    assert SmtpSettings.from_env({**ENV, "SMTP_PORT": "2525"}).port == 2525
    for bad in ({"SMTP_FROM": "x"}, {"SMTP_HOST": ""}, {"SMTP_SECURITY": "tls13"}):
        with pytest.raises(ValueError):
            SmtpSettings.from_env({**ENV, **bad})
    with pytest.raises(ValueError, match="requires SMTP_HOST"):
        build_channel("email", {})


def test_message_headers_allow_reply_matching() -> None:
    email = EmailChannel(SmtpSettings.from_env(ENV)).build(MSG)
    parsed = message_from_bytes(email.as_bytes())
    assert parsed["X-PDCA-Outbound-Id"] == "42"
    assert parsed["X-PDCA-Kind"] == "progress_ask"
    assert parsed["Auto-Submitted"] == "auto-generated"
    assert parsed["Reply-To"] == "pdca-reply@congty.vn"
    assert parsed["Message-ID"].startswith("<") and "pdca-42" in parsed["Message-ID"]
    assert parsed["Message-ID"].endswith("@congty.vn>")
    assert str(make_header(decode_header(parsed["Subject"]))) == MSG.subject
    assert "Nguyễn Thị Lan" in str(make_header(decode_header(parsed["To"])))
    body = parsed.get_payload(decode=True)
    assert isinstance(body, bytes) and "Trợ lý AI PDCA" in body.decode("utf-8")


def test_send_success(fake_smtp: type[FakeSMTP]) -> None:
    result = EmailChannel(SmtpSettings.from_env(ENV)).send(MSG)
    assert result.ok and result.external_id and "pdca-42" in result.external_id
    [smtp] = fake_smtp.instances
    assert (smtp.host, smtp.port) == ("smtp.congty.vn", 587)
    assert smtp.calls == ["starttls", "login:pdca@congty.vn", "quit"]
    assert smtp.sent[0]["Message-ID"] == result.external_id


def test_send_without_auth_or_tls(fake_smtp: type[FakeSMTP]) -> None:
    env = {"SMTP_HOST": "mail", "SMTP_FROM": "pdca@x.vn", "SMTP_SECURITY": "none"}
    assert EmailChannel(SmtpSettings.from_env(env)).send(MSG).ok
    assert fake_smtp.instances[0].calls == ["quit"]


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (smtplib.SMTPRecipientsRefused({"lan@congty.vn": (550, b"no such user")}), "permanent"),
        (smtplib.SMTPRecipientsRefused({"lan@congty.vn": (450, b"mailbox busy")}), "transient"),
        (smtplib.SMTPDataError(554, b"rejected"), "permanent"),
        (smtplib.SMTPDataError(451, b"try later"), "transient"),
        (smtplib.SMTPAuthenticationError(535, b"bad credentials"), "transient"),
        (smtplib.SMTPServerDisconnected("gone"), "transient"),
        (ConnectionRefusedError(), "transient"),
        (TimeoutError(), "transient"),
    ],
)
def test_send_error_mapping(fake_smtp: type[FakeSMTP], error: Exception, kind: str) -> None:
    fake_smtp.error = error
    result = EmailChannel(SmtpSettings.from_env(ENV)).send(MSG)
    assert (result.ok, result.error_kind) == (False, kind)
    assert "secret" not in result.detail


def test_invalid_recipient_is_permanent_without_connecting(fake_smtp: type[FakeSMTP]) -> None:
    bad = OutboundMessage(1, "k", "morning_nudge", "X", "not an address", "s", "b")
    result = EmailChannel(SmtpSettings.from_env(ENV)).send(bad)
    assert (result.ok, result.error_kind) == (False, "permanent")
    assert fake_smtp.instances == []


def test_retry_delays_grow() -> None:
    assert [retry_delay(n) for n in (1, 2, 3, 4, 9)] == [
        timedelta(minutes=1),
        timedelta(minutes=5),
        timedelta(minutes=15),
        timedelta(hours=1),
        timedelta(hours=1),
    ]
