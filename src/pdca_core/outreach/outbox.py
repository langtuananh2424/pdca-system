"""Gửi hàng đợi `outbound_messages` qua kênh (LLD 5.1 `outbox/sender`, SDD 4.10 tin nhắn).

queued → sent; lỗi tạm thời → failed (lần chạy sau thử lại); hết lượt hoặc lỗi
vĩnh viễn → dead. Dòng được khóa `for update skip locked` trong lúc gửi để hai
bộ gửi chạy song song không gửi trùng.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from psycopg_pool import ConnectionPool

from pdca_core.outreach.ports import ChannelSender, OutboundMessage, SendResult
from pdca_core.repositories import outreach as repo

logger = logging.getLogger("pdca.outbox")


@dataclass(frozen=True)
class SendStats:
    sent: int = 0
    failed: int = 0
    dead: int = 0

    def detail(self) -> dict[str, int]:
        return {"sent": self.sent, "failed": self.failed, "dead": self.dead}


def send_pending(
    pool: ConnectionPool,
    senders: Mapping[str, ChannelSender],
    *,
    max_attempts: int = 5,
    batch: int = 50,
) -> SendStats:
    sent = failed = dead = 0
    with pool.connection() as conn, conn.transaction():
        for msg in repo.claim_pending(conn, list(senders), max_attempts, batch):
            address = repo.recipient_address(conn, msg.user_id)
            if address is None:  # người dùng bị khóa/xóa sau khi xếp hàng
                result = SendResult(ok=False, error_kind="permanent", detail="inactive user")
            else:
                result = _send(
                    senders[msg.channel],
                    OutboundMessage(
                        id=msg.id,
                        dedupe_key=msg.dedupe_key,
                        kind=msg.kind,
                        to_name=address[0],
                        to_address=address[1],
                        subject=str(msg.payload.get("subject", "")),
                        body=str(msg.payload.get("body", "")),
                    ),
                )

            if result.ok:
                repo.mark_sent(conn, msg.id, result.external_id)
                sent += 1
                continue
            is_dead = result.error_kind == "permanent" or msg.attempts + 1 >= max_attempts
            repo.mark_failed(conn, msg.id, dead=is_dead)
            logger.warning(
                "send failed",
                extra={
                    "message_id": msg.id,
                    "channel": msg.channel,
                    "error_kind": result.error_kind,
                    "dead": is_dead,
                    "detail": result.detail,
                },
            )
            if is_dead:
                dead += 1
            else:
                failed += 1
    return SendStats(sent=sent, failed=failed, dead=dead)


def _send(sender: ChannelSender, message: OutboundMessage) -> SendResult:
    try:
        return sender.send(message)
    except Exception as exc:  # lỗi bất ngờ của adapter coi là tạm thời
        logger.exception("channel raised", extra={"message_id": message.id})
        return SendResult(ok=False, error_kind="transient", detail=type(exc).__name__)
