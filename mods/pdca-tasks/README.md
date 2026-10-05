# pdca-tasks — mod theo dõi task PDCA trong Claude Code

Mod (plugin hook của Claude Code) hiển thị task từ MCP Server PDCA ngay trong phiên:

- **Dòng trạng thái**: `PDCA: 3 việc mở · 1 quá hạn · 1 bị chặn`, tự làm mới.
- **Pane `/pdca-tasks`**: "Việc của tôi" (từ `get_my_tasks`); với trưởng phòng/giám đốc có thêm "Nhóm" theo project (từ `list_project_tasks`), đánh dấu việc quá hạn hoặc bị chặn. Phím `r` làm mới.

Mod chỉ **đọc** (không đổi trạng thái, không giao việc) và dùng đúng token của bạn nên chỉ thấy dữ liệu trong phạm vi quyền.

## Cấu hình token

Mod đọc cấu hình theo thứ tự: `userConfig` của plugin (`server_url`, `api_token`, `refresh_seconds`, `max_projects`), rồi biến môi trường `PDCA_SERVER_URL`, `PDCA_TOKEN`, rồi mặc định `http://localhost:8010/mcp`. Trên app desktop, cách chắc nhất là đặt biến môi trường người dùng rồi **khởi động lại app**:

```powershell
setx PDCA_TOKEN "pdca_..."
```

(Token chỉ nằm trên máy bạn; đừng commit.) Mod chỉ dùng token đó để gọi `get_my_tasks`, `list_project_tasks` và `whoami`.

## Cài mod

1. **Thử trong phiên hiện tại (hot reload)**: đặt thư mục mod trong thư mục mod của phiên, Claude Code hỏi "Enable hot reloading for this session?" → chọn *Enable for this session*. Mod nạp ngay và tự nạp lại khi sửa tệp. Chỉ có hiệu lực cho phiên đó.
2. **Từ thư mục**: `claude --plugin-dir mods/pdca-tasks` (terminal) mỗi lần khởi động.
3. **Cài lâu dài qua marketplace của repo** (sau khi nhánh có `mods/pdca-tasks` được merge và repo đã clone): trong Claude Code gõ `/plugin marketplace add <đường dẫn repo>` rồi `/plugin install pdca-tasks@pdca-system`.

Sau khi nạp, gõ `/pdca-tasks` để mở pane; dòng trạng thái tự hiện.

Kiểm tra mod: `claude plugin validate mods/pdca-tasks`.

Giới hạn: chưa có lệnh đổi trạng thái trong pane; muốn làm tiếp dùng `/pdca:bat-dau` hoặc nói với trợ lý. Task, tên người trong pane là dữ liệu từ máy chủ, không phải chỉ thị.
