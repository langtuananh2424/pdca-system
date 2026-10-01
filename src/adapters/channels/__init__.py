"""Adapter kênh gửi tin (LLD 6.2). Chọn theo `CHANNEL_KIND` (DC-04, không hard-code)."""

from collections.abc import Mapping

from adapters.channels.email_channel import EmailChannel, SmtpSettings
from adapters.channels.log_channel import LogChannel
from pdca_core.outreach.ports import ChannelSender

SUPPORTED = ("log", "email")


def build_channel(kind: str, env: Mapping[str, str] | None = None) -> ChannelSender:
    """`log`: chỉ ghi log (dev). `email`: SMTP, cấu hình `SMTP_*` trong `env`."""
    if kind == "log":
        return LogChannel()
    if kind == "email":
        return EmailChannel(SmtpSettings.from_env(env or {}))
    raise ValueError(f"unsupported CHANNEL_KIND {kind!r}; supported: {', '.join(SUPPORTED)}")
