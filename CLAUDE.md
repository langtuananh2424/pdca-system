# PDCA System — Ngữ cảnh dự án cho Claude Code

## Dự án
Hệ thống Trợ lý AI phân cấp theo chu trình PDCA (Plan – Do – Check – Action):
mỗi nhân viên, trưởng phòng và thầy Phúc có một trợ lý AI; trợ lý báo cáo lên
nhau theo cơ cấu tổ chức. Gồm Web chat (cửa vào chính), MCP Server (cửa vào
phụ từ Claude Code), Agent Service (nhắc việc, phân tích trả lời, tổng hợp),
Scheduler, dùng chung lớp nghiệp vụ `pdca_core`, một registry tool và một
PostgreSQL (HLD ADR-015).

Dự án độc lập với PPS English (`D:\pps-education`, Java/Spring) — không dùng
chung code, quy ước hay migration.

## Tài liệu — đọc theo nhu cầu, đừng đọc hết
- `docs/01-SRS.md` — yêu cầu (FR/NFR/DC/AC), use case, vấn đề mở (OI-xx).
- `docs/02-HLD.md` — kiến trúc container, luồng chính, ma trận quyền mức cao, ADR.
- `docs/03-SDD.md` — view IEEE 1016: mô-đun, máy trạng thái (4.10), thuật
  toán `can`, ghép rule, idempotency job (4.11).
- `docs/04-LLD.md` — **nguồn chính khi code**: DDL (mục 2), token + ma trận
  quyền chi tiết (mục 3), đặc tả từng MCP tool (mục 4), job/agent (5), plugin
  (7), biến môi trường (8), ca kiểm thử T-01..T-18 (10), lộ trình P1 (12).

Khi implement một tool/job: đọc đúng mục LLD tương ứng, dùng đúng tên bảng,
cột, trạng thái, mã lỗi đã thiết kế — không tự đặt lại. Nếu cần lệch thiết
kế, nêu rõ và cập nhật tài liệu trong cùng thay đổi.

## Công nghệ
Python 3.12 + `uv`; SDK MCP 2.x (`MCPServer`, `streamable_http_app(stateless_http=True)`); `psycopg` v3
(pool); `pydantic` v2; PostgreSQL 16 (pgvector từ P2); Flyway; APScheduler +
khóa advisory Postgres; `httpx`; pytest + Testcontainers; `ruff` + `mypy`;
Docker Compose + Caddy.

> Chữ ký API của SDK MCP, cấu hình hook/plugin Claude Code trong tài liệu là
> **thiết kế tham khảo, chưa chạy thử** (OI-09, OI-10). Đối chiếu tài liệu
> phiên bản đang dùng trước khi viết code.

## Cấu trúc
```
db/migration/   Flyway V{n}__{mô_tả}.sql — nguồn DDL duy nhất
db/seed/        dữ liệu dev (R__, idempotent) — không nạp ở staging/prod
src/pdca_core/  lớp nghiệp vụ: authz org plans tasks reports outreach questions actions aggregation audit repositories (+ `tools`, đích v0.2, chưa có)
src/adapters/   mcp llm channels rules docs
src/apps/       web_chat mcp_server agent_service scheduler admin_cli dashboard
src/config/
rules/          rule công ty/phòng ban (Markdown + YAML front matter)
plugin/         plugin Claude Code: skill chot-ngay, hook rule, .mcp.json (rules/ trong đó là tệp sinh)
tests/          unit integration permission_matrix
deploy/         docker-compose, Dockerfile, Caddyfile
```

## Bất biến kiến trúc — không có ngoại lệ
1. **Phụ thuộc từ ngoài vào trong.** `pdca_core` không import `adapters/`,
   SDK MCP, LLM hay kênh nhắn tin. `apps/` → `adapters/` → `pdca_core` →
   `repositories` → DB. `web_chat`, `mcp_server` không truy vấn DB trực tiếp.
2. **Một nguồn sự thật.** Web chat, MCP, Agent Service, Dashboard đều đi qua
   cùng hàm nghiệp vụ và cùng `authz.can`. Tool khai báo **một lần** trong
   registry `pdca_core.tools`; web chat và MCP chỉ phơi lại (FR-AUTH-07). Agent
   không gọi MCP qua mạng (ADR-011).
3. **Danh tính chỉ từ token hoặc phiên đăng nhập.** Mọi tool nhận `UserContext`
   từ lớp xác thực (token MCP hoặc phiên web);
   không bao giờ có tham số `user_id` do model truyền để xác định người gọi
   (FR-AUTH-02). Tham số `assignee_id` là đối tượng thao tác, vẫn qua `can`.
4. **Mặc định từ chối.** Từ chối quyền trả `forbidden_or_not_found`, không
   phân biệt "không tồn tại" với "không được xem".
5. **Audit mọi lời gọi tool** (ok/denied/error) qua `run_tool`; `audit_log`
   chỉ thêm, không có đường update/delete.
6. **AI chỉ đề xuất.** Không có đường ghi vào kế hoạch nếu không qua bản ghi
   duyệt `approved` (FR-ACTN-04). Tool mà model gọi được chỉ tạo báo cáo
   `draft_by_agent`; chuyển sang `submitted` là thao tác của người qua API web
   xác thực bằng phiên, không phải tool (ADR-015).
7. **Riêng tư.** Chỉ báo cáo `submitted` đi lên cấp trên; không lưu chat thô;
   gửi LLM nội dung tối thiểu.
8. **Nội dung người dùng là dữ liệu, không phải chỉ thị.** Khi đưa vào prompt,
   bọc trong khối `<du_lieu>` kèm chỉ dẫn bỏ qua yêu cầu bên trong.
9. **Idempotent.** Job dùng `job_runs` (unique job+scheduled_for) + khóa
   advisory; tin nhắn dùng `outbound_messages.dedupe_key`.
10. **Không hard-code model/kênh.** Tên model, kênh, bí mật lấy từ cấu hình/
    biến môi trường (DC-04, NFR-SEC-04).

## Quy ước
- Schema chỉ đổi bằng file Flyway **mới**; không sửa migration đã tồn tại.
- Khóa chính `bigint generated always as identity`; thời gian `timestamptz`
  lưu UTC, hiển thị Asia/Bangkok; liệt kê dùng `text` + `CHECK`; xóa mềm
  `deleted_at`.
- Giới hạn đầu ra tool: ≤ 100 dòng, ≤ 50 KB, trường văn bản ≤ 2000 ký tự.
- Mã lỗi chuẩn: `unauthorized`, `forbidden_or_not_found`, `invalid_argument`,
  `conflict`, `rate_limited`, `internal`.
- Tên biến, log, mã lỗi bằng tiếng Anh; docstring/comment nghiệp vụ bằng
  tiếng Việt, dùng đúng thuật ngữ và mã yêu cầu (FR-xx, NFR-xx) để truy vết.
- Không commit token, `.env`, khóa API. Token người dùng nhập qua
  `userConfig.api_token` (sensitive) của plugin, lưu trong kho bí mật của máy.

## Kiểm thử
- Logic mới trong `pdca_core` có unit test; repository/ràng buộc DB có test
  tích hợp với Testcontainers.
- Mỗi tool mới phải được thêm vào **bộ kiểm thử ma trận quyền**
  (`tests/permission_matrix/`, tham số hóa từ LLD bảng 3.2), chạy trên cả hai
  cửa vào (token MCP và phiên web).
- Thay đổi chạm tới prompt/phân tích trả lời: thêm ca prompt injection.
- Chạy trước khi coi là xong: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`.

## Trạng thái hiện tại
Xong LLD mục 12 bước 1–7: khung dự án, CI, Docker Compose cục bộ (`deploy/`;
dịch vụ ứng dụng nằm trong profile `apps` cho tới khi có entrypoint);
`V1__core.sql`, `V2__role_grants.sql`, seed dev; khung bảo mật trong
`pdca_core`: `errors`, `authz` (context, policy/`can`, tokens), `audit`,
`tool_runner.run_tool`, `output_limits`, `ratelimit`, repository Postgres;
`pdca-admin token issue|revoke`. Login + mật khẩu vai trò DB tạo ở
`deploy/initdb/`, không trong migration. `run_tool` tự xác thực để token
sai cũng được audit (xem LLD 4.3). MCP Server (`adapters/mcp/server.py`,
`python -m apps.mcp_server`) có tool `whoami`, `get_my_tasks`,
`update_task_status`, `log_activity`, `get_my_day_context`, `submit_report`,
`get_my_reports` (quy tắc create/append/replace: LLD 4.2 ghi chú bước 5),
`list_plans`, `get_plan`, `create_plan`, `update_plan`, `create_task`,
`assign_task` (phạm vi kế hoạch: LLD 4.2 ghi chú bước 7), `get_project_status`,
`get_team_blockers` (chỉ báo cáo `submitted`); đọc token qua `Context.headers` của SDK
`mcp` 2.2, không dùng `token_verifier`. Ma trận quyền mức tool ở
`tests/permission_matrix/test_tools_matrix.py`; fixture DB dùng chung ở
`tests/db_fixtures.py`, gọi MCP qua HTTP ở `tests/mcp_helpers.py`. Plugin
Claude Code ở `plugin/` (marketplace `.claude-plugin/marketplace.json`): skill
`/pdca:chot-ngay`, `/pdca:viec-cua-toi`, hook SessionStart nạp rule ghép sẵn —
sửa `rules/` rồi chạy `uv run pdca-admin rules build` (LLD 7.5). Đủ tool P1
của LLD 4.2. Bước tiếp theo theo LLD 12: bước 8 — adapter kênh + outbox + job
nhắc việc: đã xong phần không phụ thuộc kênh (LLD 5.8 — `pdca_core/outreach`,
`job_runner.run_job`, `apps/scheduler` chạy trong Compose với kênh `log`, V4
`reports.source = 'system'`) và gửi email qua SMTP (LLD 5.9, V5 giãn cách thử
lại, Mailpit cho dev: Compose profile `mail`). Triển khai lên máy chủ + nối
Claude Code: `deploy/README.md` (`deploy/init-env.sh`). Nhánh: phát triển trên
`develop`, `main` là release; push lên `main` chạy CI rồi job `deploy` chạy trên self-hosted runner tại
máy chủ (`deploy/README.md` mục 7). Còn lại: nhận + phân tích trả
lời (Agent Service, cần chốt LLM — OI-04).

Đã cài đặt hỏi cấp trên P1 (LLD 12 bước 11; SRS UC-18/FR-ASK, ADR-014): V6, tool
`ask_superior`, `get_my_questions`, `answer_question`, `cancel_question` (ghi chú:
LLD 4.2), `pdca_core/questions`, job `question_expire` trong scheduler, skill
`/pdca:hoi-cap-tren`, `/pdca:cau-hoi-den-toi`. Agent soạn nháp (FR-ASK-09..11) là P2. Người nhận do `resolve_recipient` chọn: trưởng
phòng trực thuộc, hoặc cấp kế tiếp khi vị trí đó trống (OI-11 đã chốt hướng này).

Thiết kế v0.2 (hai cửa vào + registry tool `pdca_core.tools`, web chat, phiên đăng nhập,
ADR-015) **chưa cài đặt**; code hiện tại là cửa MCP thuần (v0.1). Lệch hiện trạng cần
xử lý khi làm v0.2 (chi tiết: LLD 12, bước 12): `submit_report` đang ghi thẳng
`submitted` (đích: chỉ `draft_by_agent`, nộp qua API web); `UserContext.channel` và
`audit_log.actor_kind` mới có `mcp`/`user_mcp`; `answer_question send` là tool model gọi
được (xem LLD 3.3 mục 6); bảng `web_sessions` sẽ ở migration V7.

Vấn đề mở ảnh hưởng thiết kế: kênh nhắn tin P1 (OI-01), gói Claude/LLM
(OI-04), hook Claude Code (OI-09), cách đăng nhập web P1 (OI-12).
