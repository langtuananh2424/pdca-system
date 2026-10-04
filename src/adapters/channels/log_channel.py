"""Kênh `log` cho dev/thử nghiệm: không gửi đi đâu, chỉ ghi log (FR-NTF-07).

Dùng khi chưa cấu hình kênh thật (email làm sau, OI-01). Không bật ở prod.
"""

import logging

from pdca_core.outreach.ports import OutboundMessage, SendResult

logger = logging.getLogger("pdca.channel.log")


class LogChannel:
    name = "log"

    def send(self, message: OutboundMessage) -> SendResult:
        logger.info(
            "outbound message",
            extra={
                "message_id": message.id,
                "kind": message.kind,
                "to": message.to_address,
                "subject": message.subject,
                "body": message.body,
            },
        )
        return SendResult(ok=True, external_id=f"log-{message.id}")
