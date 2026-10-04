"""Kênh email qua SMTP (LLD 6.2, FR-NTF-07). Chỉ gửi; nhận trả lời làm sau.

Mỗi thư mang `Message-ID` sinh từ `outbound_messages.id` (lưu vào `external_id`)
và header `X-PDCA-Outbound-Id` để luồng nhận trả lời ghép `In-Reply-To` về đúng
tin (LLD 6.2). `Auto-Submitted: auto-generated` (RFC 3834) để máy chủ thư không
gửi thư trả lời tự động (vắng mặt…) quay lại hệ thống.
"""

import smtplib
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from typing import Literal

from pdca_core.outreach.ports import OutboundMessage, SendResult

Security = Literal["starttls", "ssl", "none"]


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: Security
    sender: str  # "Tên <địa chỉ>" hoặc địa chỉ
    username: str | None = None
    password: str | None = None
    reply_to: str | None = None
    timeout: float = 30.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "SmtpSettings":
        host = env.get("SMTP_HOST", "")
        sender = env.get("SMTP_FROM", "")
        if not host or "@" not in parseaddr(sender)[1]:
            raise ValueError("CHANNEL_KIND=email requires SMTP_HOST and SMTP_FROM")
        security = env.get("SMTP_SECURITY", "starttls")
        if security not in ("starttls", "ssl", "none"):
            raise ValueError("SMTP_SECURITY must be starttls, ssl or none")
        default_port = {"starttls": 587, "ssl": 465, "none": 25}[security]
        return cls(
            host=host,
            port=int(env.get("SMTP_PORT") or default_port),
            security=security,  # type: ignore[arg-type]
            sender=sender,
            username=env.get("SMTP_USER") or None,
            password=env.get("SMTP_PASSWORD") or None,
            reply_to=env.get("SMTP_REPLY_TO") or None,
            timeout=float(env.get("SMTP_TIMEOUT_SECONDS") or 30),
        )


class EmailChannel:
    name = "email"

    def __init__(self, settings: SmtpSettings) -> None:
        self._settings = settings
        self._domain = parseaddr(settings.sender)[1].rpartition("@")[2] or None

    def build(self, message: OutboundMessage) -> EmailMessage:
        email = EmailMessage()
        email["From"] = self._settings.sender
        email["To"] = formataddr((message.to_name, message.to_address))
        if self._settings.reply_to:
            email["Reply-To"] = self._settings.reply_to
        email["Subject"] = message.subject
        email["Message-ID"] = make_msgid(idstring=f"pdca-{message.id}", domain=self._domain)
        email["X-PDCA-Outbound-Id"] = str(message.id)
        email["X-PDCA-Kind"] = message.kind
        email["Auto-Submitted"] = "auto-generated"
        email.set_content(message.body, charset="utf-8")
        return email

    def send(self, message: OutboundMessage) -> SendResult:
        try:
            Address(addr_spec=message.to_address)
        except (ValueError, IndexError):
            return SendResult(ok=False, error_kind="permanent", detail="invalid recipient address")
        email = self.build(message)
        try:
            with self._connect() as smtp:
                if self._settings.username:
                    smtp.login(self._settings.username, self._settings.password or "")
                smtp.send_message(email)
        except smtplib.SMTPRecipientsRefused as exc:
            codes = [code for code, _ in exc.recipients.values()]
            permanent = all(500 <= code < 600 for code in codes)
            return _fail(permanent, f"recipient refused {codes}")
        except smtplib.SMTPAuthenticationError as exc:
            # Lỗi cấu hình: để tạm thời, sửa cấu hình thì tin còn lại vẫn gửi được.
            return _fail(False, f"smtp auth failed {exc.smtp_code}")
        except smtplib.SMTPResponseException as exc:
            return _fail(500 <= exc.smtp_code < 600, f"smtp {exc.smtp_code}")
        except (smtplib.SMTPException, OSError) as exc:
            return _fail(False, type(exc).__name__)
        return SendResult(ok=True, external_id=str(email["Message-ID"]))

    def _connect(self) -> smtplib.SMTP:
        s = self._settings
        if s.security == "ssl":
            return smtplib.SMTP_SSL(
                s.host, s.port, timeout=s.timeout, context=ssl.create_default_context()
            )
        smtp = smtplib.SMTP(s.host, s.port, timeout=s.timeout)
        if s.security == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        return smtp


def _fail(permanent: bool, detail: str) -> SendResult:
    return SendResult(ok=False, error_kind="permanent" if permanent else "transient", detail=detail)
