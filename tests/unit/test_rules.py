import json
import re
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from adapters.mcp.server import ToolDeps, build_server
from adapters.rules import plugin_build
from adapters.rules.composer import (
    RuleFormatError,
    RuleItem,
    compose,
    load_layer,
    parse_rule,
    render,
)
from apps.admin_cli.__main__ import main as admin_main

ROOT = Path(__file__).resolve().parents[2]


def _rule(key: str, *, mandatory: bool = False, body: str = "nội dung", title: str = "") -> str:
    flag = str(mandatory).lower()
    return f"---\nkey: {key}\ntitle: {title or key}\nmandatory: {flag}\n---\n{body}\n"


def _item(key: str, layer: str, *, mandatory: bool = False) -> RuleItem:
    return RuleItem(key, key, mandatory, f"{layer}:{key}", layer, f"{layer}/{key}.md")


def test_parse_rule() -> None:
    item = parse_rule(
        _rule("bao-cao", mandatory=True, title="Báo cáo"), layer="company", source="c/a.md"
    )
    assert (item.key, item.title, item.mandatory, item.body) == (
        "bao-cao",
        "Báo cáo",
        True,
        "nội dung",
    )
    assert parse_rule("---\nkey: x\n---\nabc", layer="company", source="s").mandatory is False


@pytest.mark.parametrize(
    "text",
    [
        "không có front matter",
        "---\nkey: x\nnội dung không đóng",
        "---\nkey: X Hoa\n---\nabc",
        "---\nkey: x\nmandatory: maybe\n---\nabc",
        "---\nkey: x\nowner: ai\n---\nabc",
        "---\nkey: x\n---\n   ",
    ],
)
def test_parse_rule_rejects_bad_files(text: str) -> None:
    with pytest.raises(RuleFormatError):
        parse_rule(text, layer="company", source="s")


def test_compose_lower_layer_overrides_only_optional_items() -> None:
    """SDD 4.11.2, FR-RULE-05."""
    company = [_item("a", "company", mandatory=True), _item("b", "company")]
    dept = [_item("a", "dept"), _item("b", "dept"), _item("c", "dept")]

    result = compose([company, dept])

    assert [(i.key, i.layer) for i in result.items] == [
        ("a", "company"),  # bắt buộc: giữ của công ty
        ("b", "dept"),  # không bắt buộc: phòng thay, giữ vị trí
        ("c", "dept"),  # bổ sung
    ]
    assert [(i.key, i.layer) for i in result.skipped] == [("a", "dept")]


def test_render_marks_mandatory_and_versions_content() -> None:
    text = render(compose([[_item("a", "company", mandatory=True)]]), department="phong-x")
    assert "## a (bắt buộc)" in text
    assert "phòng `phong-x`" in text
    assert re.search(r"Phiên bản nội dung: `[0-9a-f]{12}`", text)


def test_load_layer_rejects_duplicate_keys(tmp_path: Path) -> None:
    (tmp_path / "1.md").write_text(_rule("trung"), encoding="utf-8")
    (tmp_path / "2.md").write_text(_rule("trung"), encoding="utf-8")
    (tmp_path / "README.md").write_text("không phải rule", encoding="utf-8")
    with pytest.raises(RuleFormatError, match="duplicate key"):
        load_layer(tmp_path, layer="company", root=tmp_path)


def _build(rules: Path, plugin: Path) -> int:
    return admin_main(["rules", "build", "--rules-dir", str(rules), "--plugin-dir", str(plugin)])


def _copy_repo_rules(tmp_path: Path) -> tuple[Path, Path]:
    rules = tmp_path / "rules"
    plugin = tmp_path / "plugin"
    shutil.copytree(ROOT / "rules", rules)
    shutil.copytree(ROOT / "plugin", plugin)
    return rules, plugin


def test_committed_plugin_rules_are_up_to_date() -> None:
    """Giống bước CI `pdca-admin rules build --check`."""
    result = plugin_build.plan(ROOT / "rules", ROOT / "plugin")
    assert result.stale == []
    assert result.skipped == []


def test_build_writes_department_files_and_options(tmp_path: Path) -> None:
    rules, plugin = _copy_repo_rules(tmp_path)
    new_dept = rules / "departments" / "ke-toan"
    new_dept.mkdir()
    (new_dept / "a.md").write_text(_rule("quy-trinh", body="Khóa sổ ngày 25"), encoding="utf-8")
    # Mục trùng key bắt buộc của công ty: bị bỏ và báo lại.
    (new_dept / "b.md").write_text(_rule("bao-cao-ngay", body="mẫu riêng"), encoding="utf-8")

    assert _build(rules, plugin) == 0

    text = (plugin / "rules" / "ke-toan.md").read_text(encoding="utf-8")
    assert "Khóa sổ ngày 25" in text
    assert "mẫu riêng" not in text
    manifest = json.loads((plugin / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["userConfig"]["department"]["options"] == ["ke-toan", "thu-nghiem-a"]
    assert plugin_build.plan(rules, plugin).stale == []

    shutil.rmtree(new_dept)
    assert _build(rules, plugin) == 0
    assert not (plugin / "rules" / "ke-toan.md").exists()


def test_build_rejects_rules_over_session_start_limit(tmp_path: Path) -> None:
    rules, plugin = _copy_repo_rules(tmp_path)
    (rules / "company" / "99-dai.md").write_text(_rule("dai", body="x" * 10_000), encoding="utf-8")
    with pytest.raises(RuleFormatError, match="SessionStart limit"):
        plugin_build.plan(rules, plugin)
    assert _build(rules, plugin) == 1


def test_plugin_files_are_valid_json() -> None:
    for name in (
        ".claude-plugin/plugin.json",
        ".mcp.json",
        "hooks/hooks.json",
    ):
        json.loads((ROOT / "plugin" / name).read_text(encoding="utf-8"))
    json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))


async def test_skill_allowed_tools_exist_on_server() -> None:
    """Tên tool trong allowed-tools phải khớp tool của server (plugin `pdca`, server `pdca`)."""
    server = build_server(cast(ToolDeps, cast(Any, None)))
    tools = {t.name for t in await server.list_tools()}
    prefix = "mcp__plugin_pdca_pdca__"
    for skill in (ROOT / "plugin" / "skills").glob("*/SKILL.md"):
        front = skill.read_text(encoding="utf-8").split("---")[1]
        allowed = re.search(r"^allowed-tools:(.*)$", front, re.MULTILINE)
        assert allowed, skill
        names = allowed.group(1).split()
        assert names, skill
        for name in names:
            assert name.startswith(prefix), name
            assert name.removeprefix(prefix) in tools, name
            # Tool ghi không được tự duyệt: luôn qua hộp xác nhận của Claude Code.
            assert name.removeprefix(prefix) not in {
                "submit_report",
                "log_activity",
                "update_task_status",
            }
