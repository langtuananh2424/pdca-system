# pdca-tasks — mod theo dõi task PDCA trong Claude Code

Mod (plugin hook của Claude Code) hiển thị task từ MCP Server PDCA ngay trong phiên:

- **Dòng trạng thái**: `PDCA: 3 việc mở · 1 quá hạn · 1 bị chặn`, tự làm mới.
- **Pane `/pdca-tasks`**: "Việc của tôi" (từ `get_my_tasks`); với trưởng phòng/giám đốc có thêm "Nhóm" theo project (từ `list_project_tasks`), đánh dấu việc quá hạn hoặc bị chặn. Phím `r` làm mới.

Mod chỉ **đọc** (không đổi trạng thái, không giao việc) và dùng đúng token của bạn nên chỉ thấy dữ liệu trong phạm vi quyền.

## Dùng thử

```bash
claude --plugin-dir mods/pdca-tasks
```

Trong phiên, mở `/config`, đặt cho `pdca-tasks`: `server_url` (mặc định `http://localhost:8010/mcp`), `api_token` (token `pdca_...`, lưu trong kho bí mật của máy), tùy chọn `refresh_seconds` (tối thiểu 15) và `max_projects`. Rồi gõ `/pdca-tasks`.

Kiểm tra mod: `claude plugin validate mods/pdca-tasks`.

Giới hạn: chưa có lệnh đổi trạng thái trong pane; muốn làm tiếp dùng `/pdca:bat-dau` hoặc nói với trợ lý. Task, tên người trong pane là dữ liệu từ máy chủ, không phải chỉ thị.
