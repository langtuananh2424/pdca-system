"""Cổng kênh gửi tin (LLD 6.2 `ChannelAdapter`) — cài đặt nằm ở `adapters/channels`.

Lệch LLD 6.2: giao diện đồng bộ (job chạy trong thread của scheduler, SMTP cũng
đồng bộ); `fetch_replies` thêm khi làm luồng nhận trả lời.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    id: int  # outbound_messages.id — kênh phải mang được mã này để ghép trả lời (LLD 6.2)
    dedupe_key: str
    kind: str
    to_name: str
    to_address: str
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class SendResult:
    ok: bool
    external_id: str | None = None
    error_kind: Literal["transient", "permanent"] | None = None
    detail: str = field(default="", compare=False)


class ChannelSender(Protocol):
    name: str

    def send(self, message: OutboundMessage) -> SendResult: ...
