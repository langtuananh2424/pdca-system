# Hệ thống Trợ lý AI phân cấp theo chu trình PDCA

Tài liệu thiết kế trong `docs/`:

| Tệp | Mã | Nội dung |
|---|---|---|
| `docs/01-SRS.md` | AIA-SRS-001 | Đặc tả yêu cầu |
| `docs/02-HLD.md` | AIA-HLD-001 | Thiết kế mức cao, ADR |
| `docs/03-SDD.md` | AIA-SDD-001 | Mô tả thiết kế (IEEE 1016) |
| `docs/04-LLD.md` | AIA-LLD-001 | Thiết kế chi tiết: DDL, tool MCP, cấu hình |

## Cấu trúc (theo SDD 4.8)

```text
db/migration/   Flyway V{n}__{mô_tả}.sql
src/pdca_core/  lớp nghiệp vụ (không phụ thuộc hệ thống ngoài)
src/adapters/   mcp, llm, channels, rules, docs
src/apps/       mcp_server, agent_service, scheduler, admin_cli, dashboard
src/config/
rules/          rule công ty / phòng ban (có thể tách kho git riêng)
plugin/         plugin Claude Code: rule, lệnh chot-ngay, cấu hình MCP
tests/          unit, integration, permission_matrix
deploy/         docker-compose, Dockerfile, Caddyfile
```

## Chạy cục bộ

```bash
cp deploy/.env.example deploy/.env   # đặt mật khẩu
docker compose -f deploy/docker-compose.yml up -d
```

Lệnh trên dựng PostgreSQL 16 và chạy Flyway. Thêm `--profile apps --build` để
dựng MCP Server + Caddy (`https://localhost/mcp`). Cấp token:

```bash
docker compose -f deploy/docker-compose.yml exec mcp pdca-admin token issue --email staff.a1@example.com
```

Triển khai lên máy chủ (tên miền + Let's Encrypt) và nối Claude Code bằng
`claude mcp add`: `deploy/README.md`.

Lộ trình cài đặt P1: LLD mục 12.
