"""Tin báo hỏi–đáp soạn bằng mẫu (FR-ASK-06, FR-NTF-03).

Tin **không** chứa nội dung câu hỏi hay câu trả lời (FR-ASK-06, FR-ASK-08): người nhận
mở Claude Code để đọc. Chỉ nêu tên người liên quan, mã câu hỏi và trạng thái.
"""

from pdca_core.outreach.compose import Draft

_SIGNATURE = "— Trợ lý AI PDCA. Đây là tin tự động, không phải người đó trực tiếp nhắn."

# Trạng thái cuối → câu mô tả cho người hỏi.
_OUTCOME = {
    "answered": "đã trả lời câu hỏi của bạn",
    "declined": "đã từ chối trả lời câu hỏi của bạn",
    "expired": "chưa trả lời câu hỏi của bạn trong thời hạn",
}


def question_notice(*, recipient_name: str, asker_name: str, question_id: int) -> Draft:
    body = [
        f"Chào {recipient_name},",
        "",
        f"{asker_name} vừa gửi bạn một câu hỏi (mã #{question_id}).",
        "Mở Claude Code và chạy /pdca:cau-hoi-den-toi để đọc và trả lời.",
        "",
        _SIGNATURE,
    ]
    return Draft(subject=f"[PDCA] Câu hỏi mới #{question_id}", body="\n".join(body))


def answer_notice(*, asker_name: str, recipient_name: str, question_id: int, outcome: str) -> Draft:
    verb = _OUTCOME[outcome]
    body = [
        f"Chào {asker_name},",
        "",
        f"{recipient_name} {verb} (mã #{question_id}).",
        "Mở Claude Code và chạy /pdca:hoi-cap-tren để xem chi tiết.",
        "",
        _SIGNATURE,
    ]
    return Draft(subject=f"[PDCA] Câu hỏi #{question_id}: {outcome}", body="\n".join(body))
