# Plugin Claude Code `pdca` (LLD 7)

Gồm:

| Thành phần | Tệp | Việc |
|---|---|---|
| MCP Server | `.mcp.json` | Kết nối server `pdca` qua HTTPS, token Bearer lấy từ cấu hình plugin |
| Rule | `hooks/hooks.json`, `hooks/session-start.sh`, `rules/*.md` | Đầu mỗi phiên nạp rule công ty + phòng đã chọn (FR-RULE-02/03) |
| `/pdca:chot-ngay [ngày]` | `skills/chot-ngay/SKILL.md` | Soạn nháp báo cáo ngày → người dùng duyệt → nộp (UC-04) |
| `/pdca:viec-cua-toi [trạng thái]` | `skills/viec-cua-toi/SKILL.md` | Xem task của mình |
| `/pdca:hoi-cap-tren [câu hỏi]` | `skills/hoi-cap-tren/SKILL.md` | Soạn câu hỏi → người dùng duyệt → gửi cấp trên; xem câu đã gửi (UC-18) |
| `/pdca:cau-hoi-den-toi [mã]` | `skills/cau-hoi-den-toi/SKILL.md` | Xem và trả lời câu hỏi gửi tới mình (UC-18) |

`rules/*.md` là **tệp sinh ra** từ thư mục `rules/` ở gốc repo — đừng sửa tay;
chạy `uv run pdca-admin rules build` rồi commit.

## Cài đặt cho nhân viên

Cần: Claude Code (bản hỗ trợ `userConfig.options`, v2.1.271 trở lên), quyền đọc
repo này, và trên Windows cần Git for Windows (hook rule chạy bằng `bash`).

1. Thêm marketplace (một lần mỗi máy):

   ```text
   /plugin marketplace add <URL git của repo pdca-system>
   ```

2. Cài plugin:

   ```text
   /plugin install pdca@pdca-system
   ```

3. Khi bật, Claude Code hỏi ba giá trị:
   - **Địa chỉ MCP Server**: URL `/mcp` do quản trị cung cấp.
   - **API token**: token `pdca_…` quản trị cấp bằng `pdca-admin token issue`.
     Token được lưu trong kho bí mật của hệ điều hành, không nằm trong tệp cấu hình.
   - **Phòng ban**: chọn phòng của bạn.
4. Bật tự cập nhật cho marketplace `pdca-system` trong `/plugin` → **Marketplaces**
   để nhận rule mới ở phiên sau (AC-02). Plugin không ghim `version`, nên mỗi
   commit mới là một phiên bản mới.
5. Mở phiên mới, chạy `/mcp` kiểm tra server `plugin:pdca:pdca` đã kết nối, rồi
   thử `/pdca:viec-cua-toi`.

Đổi token hoặc phòng: `/config` (phòng ban) hoặc gỡ và bật lại plugin (token).

## Ghi chú thiết kế

- Hook `SessionStart` chỉ hỗ trợ kiểu `command` lúc khởi động (không gọi được tool
  MCP), nên rule được ghép sẵn lúc build thay vì lấy từ server.
- Tool chỉ đọc được duyệt sẵn trong `allowed-tools` của skill; tool ghi
  (`submit_report`, `log_activity`, `update_task_status`) luôn qua hộp xác nhận của
  Claude Code, ngoài bước xác nhận trong hội thoại.
- Lớp rule cá nhân là `CLAUDE.md` của từng người; chỉ được bổ sung, không làm
  trái mục bắt buộc.
