"""Dựng rule đã ghép vào plugin Claude Code (LLD 7.1, FR-RULE-06).

Sinh `plugin/rules/_company.md` và `plugin/rules/<phòng>.md` từ `rules/`, đồng
bộ danh sách phòng vào `userConfig.department.options` của `plugin.json`.
Hook SessionStart của plugin in tệp tương ứng với phòng người dùng chọn.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adapters.rules.composer import (
    RuleFormatError,
    compose,
    department_slugs,
    load_layer,
    render,
)

# additionalContext/stdout của hook SessionStart bị cắt ở 10.000 ký tự.
MAX_CONTEXT_CHARS = 10_000
COMPANY_FILE = "_company.md"


@dataclass(frozen=True)
class BuildResult:
    outputs: dict[Path, str]  # tệp → nội dung mong muốn
    stale: list[Path]  # tệp khác với nội dung mong muốn (hoặc thừa/thiếu)
    skipped: list[str]  # mô tả mục phòng ban bị bỏ vì đụng mục bắt buộc


def plan(rules_root: Path, plugin_root: Path) -> BuildResult:
    company = load_layer(rules_root / "company", layer="company", root=rules_root)
    if not company:
        raise RuleFormatError("rules/company has no rule files")
    out_dir = plugin_root / "rules"
    outputs: dict[Path, str] = {}
    skipped: list[str] = []

    outputs[out_dir / COMPANY_FILE] = render(compose([company]), department=None)
    slugs = department_slugs(rules_root)
    for slug in slugs:
        layer = load_layer(
            rules_root / "departments" / slug, layer=f"department:{slug}", root=rules_root
        )
        composition = compose([company, layer])
        skipped += [
            f"{i.source} (key {i.key!r} is mandatory at company level)" for i in composition.skipped
        ]
        outputs[out_dir / f"{slug}.md"] = render(composition, department=slug)

    for path, text in outputs.items():
        if len(text) > MAX_CONTEXT_CHARS:
            raise RuleFormatError(
                f"{path.name}: {len(text)} characters exceeds the SessionStart limit of"
                f" {MAX_CONTEXT_CHARS}; shorten the rules"
            )

    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    outputs[manifest] = _manifest_with_departments(manifest, slugs)

    stale = [p for p, text in outputs.items() if _read(p) != text]
    stale += [p for p in sorted(out_dir.glob("*.md")) if p not in outputs and p.name != "README.md"]
    return BuildResult(outputs=outputs, stale=stale, skipped=skipped)


def write(result: BuildResult) -> None:
    for path in result.stale:
        if path in result.outputs:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(result.outputs[path], encoding="utf-8", newline="\n")
        else:
            path.unlink()


def _manifest_with_departments(manifest: Path, slugs: list[str]) -> str:
    data: dict[str, Any] = json.loads(manifest.read_text(encoding="utf-8"))
    department = data.get("userConfig", {}).get("department")
    if department is None:
        raise RuleFormatError(f"{manifest}: userConfig.department is missing")
    if slugs:
        department["options"] = slugs
    else:
        department.pop("options", None)
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
