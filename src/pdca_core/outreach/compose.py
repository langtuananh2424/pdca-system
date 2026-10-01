"""Soạn tin nhắc việc / hỏi tiến độ bằng mẫu cố định (FR-NTF-02, FR-NTF-03, FR-CHK-07).

Bản P1 không gọi LLM (OI-04 chưa chốt): nội dung lấy thẳng từ dữ liệu, không
có gì do AI suy đoán. `MessageComposer` là giao diện để thay bằng bộ soạn dùng
LLM sau này (LLD 5.4) — khi đó dữ liệu người dùng phải bọc trong `<du_lieu>`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from pdca_core.repositories.outreach import OpenTask, ProjectNeed

MAX_TASKS_IN_MESSAGE = 10
MAX_QUESTIONS_PER_PROJECT = 3
FALLBACK_SENDER = "cấp trên của bạn"


@dataclass(frozen=True, slots=True)
class Draft:
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class MessageContext:
    name: str
    manager_name: str | None
    local_date: date
    tasks: Sequence[OpenTask]
    projects: Sequence[ProjectNeed]


class MessageComposer(Protocol):
    def morning_nudge(self, ctx: MessageContext) -> Draft: ...
    def progress_ask(self, ctx: MessageContext) -> Draft: ...
    def progress_remind(self, ctx: MessageContext) -> Draft: ...


def _signature(ctx: MessageContext) -> str:
    """FR-NTF-03: luôn nêu rõ là trợ lý AI thay mặt cấp trên, không phải người đó nhắn."""
    on_behalf = ctx.manager_name or FALLBACK_SENDER
    return (
        f"— Trợ lý AI PDCA, thay mặt {on_behalf}. "
        f"Đây là tin tự động, không phải {on_behalf} trực tiếp nhắn."
    )


def _task_lines(ctx: MessageContext) -> list[str]:
    lines = []
    for t in ctx.tasks[:MAX_TASKS_IN_MESSAGE]:
        due = ""
        if t.due_date is not None:
            due = " — QUÁ HẠN" if t.due_date < ctx.local_date else f" — hạn {t.due_date:%d/%m}"
            if t.due_date == ctx.local_date:
                due = " — hạn HÔM NAY"
        lines.append(f"- [{t.project_name}] {t.title} ({t.status}){due}")
    if len(ctx.tasks) > MAX_TASKS_IN_MESSAGE:
        lines.append(f"- … và {len(ctx.tasks) - MAX_TASKS_IN_MESSAGE} task khác")
    return lines


def _questions(project: ProjectNeed) -> list[str]:
    """FR-CHK-07: câu hỏi Check theo `projects.config.check.questions`."""
    check = project.config.get("check") if isinstance(project.config, dict) else None
    raw = check.get("questions") if isinstance(check, dict) else None
    if not isinstance(raw, list):
        return []
    return [str(q).strip() for q in raw if str(q).strip()][:MAX_QUESTIONS_PER_PROJECT]


def _report_section(ctx: MessageContext) -> list[str]:
    pending = [p for p in ctx.projects if not p.submitted]
    lines = ["Mời bạn trả lời ngắn cho từng project chưa báo cáo:"]
    for p in pending:
        lines.append(f"* {p.project_name}")
        lines += [f"  - {q}" for q in _questions(p)]
    lines += [
        "",
        "Trả lời theo mẫu: Đã làm / Vướng gì / Xung đột lịch.",
        "Hoặc mở Claude Code và chạy /pdca:chot-ngay để chốt ngày.",
    ]
    return lines


class TemplateComposer:
    def morning_nudge(self, ctx: MessageContext) -> Draft:
        tasks = _task_lines(ctx)
        body = [f"Chào {ctx.name},", ""]
        if tasks:
            body += ["Việc đang mở của bạn hôm nay:", *tasks]
        else:
            body.append("Hiện bạn không có task đang mở. Nếu đã có việc mới, hãy báo cấp trên.")
        body += ["", _signature(ctx)]
        return Draft(subject=f"[PDCA] Việc hôm nay {ctx.local_date:%d/%m}", body="\n".join(body))

    def progress_ask(self, ctx: MessageContext) -> Draft:
        body = [f"Chào {ctx.name},", "", *_report_section(ctx), "", _signature(ctx)]
        return Draft(subject=f"[PDCA] Tiến độ ngày {ctx.local_date:%d/%m}", body="\n".join(body))

    def progress_remind(self, ctx: MessageContext) -> Draft:
        body = [
            f"Chào {ctx.name},",
            "",
            "Nhắc lại (một lần duy nhất): chưa thấy báo cáo hôm nay của bạn.",
            *_report_section(ctx),
            "",
            _signature(ctx),
        ]
        return Draft(
            subject=f"[PDCA] Nhắc lại: tiến độ ngày {ctx.local_date:%d/%m}", body="\n".join(body)
        )
