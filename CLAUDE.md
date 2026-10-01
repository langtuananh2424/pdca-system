# PDCA System — Ngữ cảnh dự án cho Claude Code

## Dự án
Hệ thống Trợ lý AI phân cấp theo chu trình PDCA (Plan – Do – Check – Action):
mỗi nhân viên, trưởng phòng và thầy Phúc có một trợ lý AI; trợ lý báo cáo lên
nhau theo cơ cấu tổ chức. Gồm MCP Server (cửa vào từ Claude Code), Agent
Service (nhắc việc, phân tích trả lời, tổng hợp), Scheduler, dùng chung lớp
nghiệp vụ `pdca_core` và một PostgreSQL.

Dự án độc lập với PPS English (`D:\pps-education`, Java/Spring) — không dùng
chung code, quy ước hay migration.

## Tài liệu — đọc theo nhu cầu, đừng đọc hết
- `docs/01-SRS.md` — yêu cầu (FR/NFR/DC/AC), use case, vấn đề mở (OI-xx).
- `docs/02-HLD.md` — kiến trúc container, luồng chính, ma trận quyền mức cao, ADR.
- `docs/03-SDD.md` — view IEEE 1016: mô-đun, máy trạng thái (4.10), thuật
  toán `can`, ghép rule, idempotency job (4.11).
- `docs/04-LLD.md` — **nguồn chính khi code**: DDL (mục 2), token + ma trận
  quyền chi tiết (mục 3), đặc tả từng MCP tool (mục 4), job/agent (5), plugin
  (7), biến môi trường (8), ca kiểm thử T-01..T-10 (10), lộ trình P1 (12).

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
src/pdca_core/  lớp nghiệp vụ: authz org plans tasks reports actions aggregation audit repositories
src/adapters/   mcp llm channels rules docs
src/apps/       mcp_server agent_service scheduler admin_cli dashboard
src/config/
rules/          rule công ty/phòng ban (Markdown + YAML front matter)
plugin/         plugin Claude Code: rule, lệnh chot-ngay, .mcp.json
tests/          unit integration permission_matrix
deploy/         docker-compose, Dockerfile, Caddyfile
```

## Bất biến kiến trúc — không có ngoại lệ
1. **Phụ thuộc từ ngoài vào trong.** `pdca_core` không import `adapters/`,
   SDK MCP, LLM hay kênh nhắn tin. `apps/` → `adapters/` → `pdca_core` →
   `repositories` → DB. `mcp_server` không truy vấn DB trực tiếp.
2. **Một nguồn sự thật.** MCP, Agent Service, Dashboard đều đi qua cùng hàm
   nghiệp vụ và cùng `authz.can`. Agent không gọi MCP qua mạng (ADR-011).
3. **Danh tính chỉ từ token.** Mọi tool nhận `UserContext` từ lớp xác thực;
   không bao giờ có tham số `user_id` do model truyền để xác định người gọi
   (FR-AUTH-02). Tham số `assignee_id` là đối tượng thao tác, vẫn qua `can`.
4. **Mặc định từ chối.** Từ chối quyền trả `forbidden_or_not_found`, không
   phân biệt "không tồn tại" với "không được xem".
5. **Audit mọi lời gọi tool** (ok/denied/error) qua `run_tool`; `audit_log`
   chỉ thêm, không có đường update/delete.
6. **AI chỉ đề xuất.** Không có đường ghi vào kế hoạch nếu không qua bản ghi
   duyệt `approved` (FR-ACTN-04). `submit_report` chỉ gọi sau khi người dùng
   xác nhận bản nháp.
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
- Không commit token, `.env`, khóa API. `PDCA_TOKEN` chỉ nằm ở biến môi
  trường máy người dùng.

## Kiểm thử
- Logic mới trong `pdca_core` có unit test; repository/ràng buộc DB có test
  tích hợp với Testcontainers.
- Mỗi tool mới phải được thêm vào **bộ kiểm thử ma trận quyền**
  (`tests/permission_matrix/`, tham số hóa từ LLD bảng 3.2).
- Thay đổi chạm tới prompt/phân tích trả lời: thêm ca prompt injection.
- Chạy trước khi coi là xong: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`.

## Trạng thái hiện tại
Xong LLD mục 12 bước 1: tài liệu thiết kế v0.1 (nháp), khung thư mục,
`pyproject.toml` + `uv.lock`, CI (GitHub Actions), Docker Compose cục bộ
(`deploy/`; dịch vụ ứng dụng nằm trong profile `apps` cho tới khi có
entrypoint). Bước tiếp theo: bước 2 — Flyway V1, grant cho vai trò DB, seed.
Login + mật khẩu các vai trò DB tạo ở `deploy/initdb/`, không trong migration.

Vấn đề mở ảnh hưởng thiết kế: kênh nhắn tin P1 (OI-01), gói Claude/LLM
(OI-04), hook Claude Code (OI-09).
