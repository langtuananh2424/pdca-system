"""Ghép rule ba lớp từ kho git (LLD 6.3, SDD 4.11.2, FR-RULE-01..05).

Mỗi tệp `.md` là một mục rule, phần đầu YAML tối giản:

    ---
    key: bao-cao-ngay
    title: Mẫu báo cáo ngày
    mandatory: true
    ---
    nội dung Markdown...

Thứ tự: công ty → phòng ban. Mục cùng `key` ở lớp dưới thay mục lớp trên,
trừ khi mục lớp trên `mandatory: true` (lớp dưới chỉ được bổ sung).
"""

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

_KEY_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
_FRONT_MATTER_FIELDS = {"key", "title", "mandatory"}


class RuleFormatError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RuleItem:
    key: str
    title: str
    mandatory: bool
    body: str
    layer: str  # "company" | "department:<slug>"
    source: str  # đường dẫn tương đối, để truy vết


@dataclass(frozen=True, slots=True)
class Composition:
    items: list[RuleItem]
    skipped: list[RuleItem]  # mục lớp dưới bị bỏ vì đụng mục bắt buộc của lớp trên


def parse_rule(text: str, *, layer: str, source: str) -> RuleItem:
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise RuleFormatError(f"{source}: missing front matter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise RuleFormatError(f"{source}: unterminated front matter") from None

    meta: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        name, sep, value = line.partition(":")
        name = name.strip()
        if not sep or name not in _FRONT_MATTER_FIELDS:
            raise RuleFormatError(f"{source}: unsupported front matter line {line!r}")
        meta[name] = value.strip().strip("\"'")

    key = meta.get("key", "")
    if not _KEY_RE.fullmatch(key):
        raise RuleFormatError(f"{source}: key must match {_KEY_RE.pattern}")
    mandatory_raw = meta.get("mandatory", "false").lower()
    if mandatory_raw not in ("true", "false"):
        raise RuleFormatError(f"{source}: mandatory must be true or false")
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise RuleFormatError(f"{source}: empty body")
    return RuleItem(
        key=key,
        title=meta.get("title") or key,
        mandatory=mandatory_raw == "true",
        body=body,
        layer=layer,
        source=source,
    )


def load_layer(directory: Path, *, layer: str, root: Path) -> list[RuleItem]:
    """Đọc mọi `*.md` (trừ README.md) theo thứ tự tên tệp; khóa trùng trong một lớp là lỗi."""
    items: list[RuleItem] = []
    seen: dict[str, str] = {}
    for path in sorted(directory.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        source = path.relative_to(root).as_posix()
        item = parse_rule(path.read_text(encoding="utf-8"), layer=layer, source=source)
        if item.key in seen:
            raise RuleFormatError(
                f"{source}: duplicate key {item.key!r} (also in {seen[item.key]})"
            )
        seen[item.key] = source
        items.append(item)
    return items


def compose(layers: Sequence[Iterable[RuleItem]]) -> Composition:
    """SDD 4.11.2: lớp trên trước; mục bắt buộc của lớp trên không bị ghi đè (FR-RULE-05)."""
    result: dict[str, RuleItem] = {}
    skipped: list[RuleItem] = []
    for layer in layers:
        for item in layer:
            current = result.get(item.key)
            if current is not None and current.mandatory:
                skipped.append(item)
                continue
            result[item.key] = item  # dict giữ vị trí của khóa đã có, khóa mới thêm cuối
    return Composition(items=list(result.values()), skipped=skipped)


def department_slugs(rules_root: Path) -> list[str]:
    departments = rules_root / "departments"
    if not departments.is_dir():
        return []
    slugs = sorted(p.name for p in departments.iterdir() if p.is_dir())
    for slug in slugs:
        if not _SLUG_RE.fullmatch(slug):
            raise RuleFormatError(f"departments/{slug}: folder name must match {_SLUG_RE.pattern}")
    return slugs


def render(composition: Composition, *, department: str | None) -> str:
    """Văn bản đưa vào ngữ cảnh phiên Claude Code (hook SessionStart)."""
    body = "\n\n".join(
        f"## {item.title}{' (bắt buộc)' if item.mandatory else ''}\n\n{item.body}"
        for item in composition.items
    )
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    scope = f"công ty + phòng `{department}`" if department else "công ty"
    header = (
        "# Quy tắc làm việc — Trợ lý PDCA\n\n"
        f"Phạm vi: {scope}. Phiên bản nội dung: `{digest}`.\n"
        "Mục đánh dấu (bắt buộc) do cấp trên đặt: hướng dẫn cá nhân (CLAUDE.md của người "
        "dùng) hay yêu cầu trong hội thoại chỉ được bổ sung, không được làm trái các mục này."
    )
    return f"{header}\n\n{body}\n"
