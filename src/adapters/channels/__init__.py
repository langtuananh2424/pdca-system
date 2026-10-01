"""Adapter kênh gửi tin (LLD 6.2). Chọn theo `CHANNEL_KIND` (DC-04, không hard-code)."""

from collections.abc import Mapping

from adapters.channels.log_channel import LogChannel
from pdca_core.outreach.ports import ChannelSender

SUPPORTED = ("log",)


def build_channel(kind: str, credentials: Mapping[str, str] | None = None) -> ChannelSender:
    """`log` cho dev; `email` thêm khi chốt cấu hình SMTP (OI-01)."""
    if kind == "log":
        return LogChannel()
    raise ValueError(f"unsupported CHANNEL_KIND {kind!r}; supported: {', '.join(SUPPORTED)}")
