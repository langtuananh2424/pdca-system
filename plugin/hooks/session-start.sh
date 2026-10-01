#!/usr/bin/env bash
# SessionStart: in rule đã ghép (công ty + phòng người dùng chọn) để Claude Code
# đưa vào ngữ cảnh phiên. Tệp do `pdca-admin rules build` sinh ra trong ../rules.
# CLAUDE_PLUGIN_OPTION_DEPARTMENT do Claude Code xuất từ userConfig.department.
set -u

rules_dir="$(cd "$(dirname "$0")/../rules" && pwd)"
department="${CLAUDE_PLUGIN_OPTION_DEPARTMENT:-}"
file="$rules_dir/_company.md"

# Chỉ nhận slug hợp lệ để không đọc ra ngoài thư mục rules.
case "$department" in
  "" | *[!a-z0-9-]*) ;;
  *) [ -f "$rules_dir/$department.md" ] && file="$rules_dir/$department.md" ;;
esac

cat "$file"
