# Triển khai MCP Server lên máy chủ và nối Claude Code

Mục tiêu: dựng MCP Server (Streamable HTTP, `https://<tên miền>/mcp`) trên một
máy chủ Linux bằng Docker Compose, rồi nối Claude Code vào để thử. Stack (LLD 9.1):
PostgreSQL 16 + Flyway + MCP Server + Scheduler (kênh `log`, không gửi tin ra ngoài).
MCP Server mở trên `127.0.0.1:${MCP_HTTP_PORT}` (mặc định 8010); TLS do lớp phía trước lo:

- **2A — máy chủ đã có nginx + Cloudflare Tunnel** (máy chủ chung với dự án khác,
  không có IP công khai): thêm một site nginx và một hostname trên tunnel.
- **2B — máy chủ riêng có IP công khai**: bật thêm Caddy (`--profile caddy`), TLS tự
  động bằng Let's Encrypt.

## 0. Hiện trạng máy chủ đích

Khảo sát ngày 2026-10-04. **Chưa triển khai PDCA lên máy chủ này.** Máy chủ đang chạy
dự án khác (PPS English) nên PDCA phải tránh đụng cổng, thư mục và runner. Repo này công
khai: không ghi tên tài khoản, hostname máy chủ hay đường dẫn cá nhân vào đây; bản kiểm kê
chi tiết giữ ngoài repo.

| Hạng mục | Hiện trạng | Hệ quả cho PDCA |
|---|---|---|
| Cổng loopback đã bị dự án khác chiếm | 3100, 3101, 5432, 5433, 5434, 8080, 8081, 9000, 9002 | **Không dùng 5434 cho `DB_PORT`** (từng gợi ý ở bản cũ): dùng `5435`. MCP giữ `8010` (đang trống). Kiểm tra lại trước khi chạy: `sudo ss -ltn \| grep -E ':(5435\|8010)\b'` phải rỗng. |
| Tên container/volume | Dự án khác dùng tiền tố `pps-` và `ppsvn-` | Compose đặt project `pdca` (container `pdca-*`, volume `pdca_pgdata`) nên không trùng. |
| Mạng | nginx nghe cổng 80, mỗi dự án một site theo `server_name`; TLS do Cloudflare Tunnel (không có IP công khai) | Dùng mục 2A: thêm một site nginx và một hostname trên tunnel. Không dùng Caddy (mục 2B). Luôn `sudo nginx -t` trước khi reload vì nginx này đang phục vụ dự án khác. |
| Tài nguyên | Dư dả cho PDCA (RAM khả dụng hàng chục GB, đĩa trống hàng chục GB) | Không cần tinh chỉnh. |
| Runner CI | Đã có một runner của dự án khác; chưa có runner cho repo này | Runner khác không nhận job của repo này. Nếu bật CI/CD (mục 7) phải cài runner riêng, nhãn `pdca`, thư mục riêng. |
| Thư mục triển khai | `/opt` đang chứa thư mục của dự án khác; `/opt/pdca-system` chưa tạo | Clone vào `/opt/pdca-system` (mục 2). |

**Chưa kiểm tra (làm trước khi triển khai):**

- Tunnel Cloudflare quản lý bằng dashboard hay tệp `config.yml` (quyết định cách thêm hostname ở mục 2A).
- Hostname dành cho PDCA (ví dụ `pdca.<tên miền công ty>`) chưa chốt; `PDCA_DOMAIN` phải trùng tên này.
- Tài khoản chạy runner có thuộc nhóm `docker` không.

**Lưu ý bảo mật về runner (mục 7).** Runner chạy cùng máy với dự án khác và tài khoản chạy
runner cần nhóm `docker`, tức gần như quyền root trên máy: workflow của repo này về lý thuyết
tác động được tới container của dự án khác. Nếu bật CI/CD: giới hạn môi trường `production`
cho nhánh `main`, đặt người duyệt bắt buộc (Required reviewers) và duyệt tay PR từ fork. Khi
chưa cần tự động, triển khai tay (mục 5) là lựa chọn an toàn hơn cho giai đoạn pilot.

## 1. Chuẩn bị máy chủ

- Linux có Docker Engine và plugin Compose v2 (`docker compose version`).
- 1 vCPU, 1–2 GB RAM là đủ để thử.
- Clone vào thư mục riêng (ví dụ `/opt/pdca-system`), không chung với dự án khác.
  Compose đặt tên project `pdca` nên container/volume tách khỏi dự án khác.
- Cổng trên loopback không được trùng dự án khác: `sudo ss -ltnp | grep -E ':(5432|8010)\b'`.
  Trùng thì đổi `DB_PORT` / `MCP_HTTP_PORT` trong `deploy/.env` (mục 2).

## 2. Dựng stack

```bash
sudo mkdir -p /opt/pdca-system && sudo chown "$USER" /opt/pdca-system
git clone https://github.com/langtuananh2424/pdca-system.git /opt/pdca-system
cd /opt/pdca-system
sh deploy/init-env.sh pdca.congty.vn        # tạo deploy/.env, sinh mật khẩu ngẫu nhiên
# Máy chủ đích đã có Postgres ở 5432–5434 (mục 0): dùng cổng loopback khác cho DB pdca.
sed -i 's/^DB_PORT=.*/DB_PORT=5435/' deploy/.env
docker compose -f deploy/docker-compose.yml --profile apps up -d --build
docker compose -f deploy/docker-compose.yml --profile apps ps
curl -s localhost:8010/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
# → có chữ "unauthorized" là MCP Server đã chạy
```

`init-env.sh` mặc định **nạp dữ liệu mẫu** (`db/seed`: 2 phòng, 7 người dùng
`*@example.com`) để thử ngay. Khi triển khai thật, chạy
`sh deploy/init-env.sh <tên miền> --no-seed` trên volume mới. `PDCA_DOMAIN` trong
`deploy/.env` phải đúng tên miền công khai: MCP Server từ chối Host khác (`421`).

### 2A. Sau nginx + Cloudflare Tunnel

1. Site nginx: mẫu ở `deploy/nginx/pdca.conf.example` (sửa `server_name` và cổng).

   ```bash
   sudo cp deploy/nginx/pdca.conf.example /etc/nginx/sites-available/pdca
   sudo nano /etc/nginx/sites-available/pdca          # server_name pdca.congty.vn;
   sudo ln -s /etc/nginx/sites-available/pdca /etc/nginx/sites-enabled/pdca
   sudo nginx -t && sudo systemctl reload nginx
   ```

2. Cloudflare Tunnel: thêm *Public hostname* `pdca.congty.vn` → `http://localhost:80`
   (Zero Trust → Networks → Tunnels → tunnel đang dùng → Public Hostname; hoặc thêm
   một mục `ingress` cho hostname đó trong `config.yml` của `cloudflared` rồi restart).
   Cloudflare tự tạo bản ghi DNS và cấp TLS.

### 2B. Máy chủ riêng với Caddy

Mở cổng **80** và **443** (Caddy cần cổng 80 để xin chứng chỉ), tên miền có bản ghi A
trỏ về IP máy chủ (chưa có thì dùng `<ip-có-gạch-ngang>.sslip.io`). Chạy thêm profile `caddy`:

```bash
docker compose -f deploy/docker-compose.yml --profile apps --profile caddy up -d --build
docker compose -f deploy/docker-compose.yml logs proxy | grep -i certificate
```

## 3. Cấp token

```bash
docker compose -f deploy/docker-compose.yml exec mcp \
  pdca-admin token issue --email staff.a1@example.com --label thu-nghiem
```

Token `pdca_…` chỉ hiện một lần; hạn mặc định 90 ngày. Thu hồi:
`pdca-admin token revoke --id <token_id>`.

Kiểm tra nhanh từ bất kỳ máy nào:

```bash
curl -s https://pdca.congty.vn/mcp \
  -H "Authorization: Bearer pdca_..." \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"whoami","arguments":{}}}'
```

Kết quả đúng chứa `"name": "Nhân viên A1"`. Thiếu hoặc sai token thì nhận
`unauthorized`.

## 4. Nối Claude Code

Cách nhanh để thử (lưu cấu hình cho thư mục hiện tại):

```bash
claude mcp add --transport http pdca https://pdca.congty.vn/mcp \
  --header "Authorization: Bearer pdca_..."
claude mcp list          # pdca: ... (HTTP) - √ Connected
```

Trong phiên Claude Code: `/mcp` thấy server `pdca` với 19 tool; thử hỏi
"gọi tool whoami của pdca" hoặc "việc của tôi hôm nay".

Lệnh trên lưu token dạng rõ trong `~/.claude.json`, chỉ nên dùng để thử. Cho
nhân viên dùng thật, cài plugin `pdca` (`plugin/README.md`): token nằm trong kho
bí mật của hệ điều hành, kèm rule và lệnh `/pdca:chot-ngay`.

## 5. Cập nhật phiên bản

Mặc định bản mới lên máy chủ tự động qua CI/CD (mục 7). Cập nhật tay khi cần:

```bash
git pull
docker compose -f deploy/docker-compose.yml --profile apps up -d --build
```

Flyway tự chạy migration mới trước khi MCP Server khởi động lại.

## 6. Thêm người dùng thật

Chưa có lệnh `pdca-admin` để tạo người dùng; tạm thời thêm bằng SQL rồi cấp token:

```bash
docker compose -f deploy/docker-compose.yml exec db psql -U postgres -d pdca -c "
  insert into users (name, email, role, department_id, manager_id)
  values ('Nguyễn Văn A', 'a@congty.vn', 'staff',
          (select id from departments where name = 'Phòng Thử nghiệm A'),
          (select head_user_id from departments where name = 'Phòng Thử nghiệm A'));"
```

`role`: `staff`, `dept_head`, `director`, `admin` (LLD 2.1, bảng `users`).

## 7. CI/CD: `develop` → `main` → máy chủ

- `develop`: nhánh phát triển. PR tính năng nhắm vào `develop`; CI (`.github/workflows/ci.yml`,
  job `check`, chạy trên runner của GitHub) chạy ruff, mypy, pytest.
- `main`: nhánh release. Mở PR `develop` → `main`; khi merge, CI chạy lại `check` rồi job
  `deploy` chạy **trên self-hosted runner đặt tại máy chủ** (máy chủ sau Cloudflare Tunnel
  nên GitHub không SSH vào được): fast-forward thư mục `DEPLOY_PATH` đúng commit đó,
  `docker compose ... --profile apps up -d --build`, rồi gọi thử `/mcp`.
- Không cần secret nào: runner đã ở trên máy chủ.

**Cài runner (một lần, trên máy chủ).** Runner của repo khác (ví dụ pps-education) không
nhận job của repo này, nên cài thêm một runner riêng, trong thư mục riêng:

1. GitHub → repo `pdca-system` → Settings → Actions → Runners → *New self-hosted runner* →
   Linux; làm theo các lệnh hiện ra trong thư mục mới, ví dụ `~/actions-runner-pdca`.
2. Khi `./config.sh` hỏi nhãn (*labels*), nhập `pdca`; tên runner tùy ý.
3. Chạy như dịch vụ: `sudo ./svc.sh install <user> && sudo ./svc.sh start`. `<user>` cần
   thuộc nhóm `docker` và có quyền ghi vào `DEPLOY_PATH`.

**Trên GitHub:**

- Settings → Environments → `production`: mục *Deployment branches and tags* chọn
  *Selected branches and tags*, thêm `main`. Thêm **variable** (không phải secret)
  `DEPLOY_PATH` = `/opt/pdca-system`; `MCP_HTTP_PORT` nếu đổi khỏi 8010.
- Settings → Actions → General → *Fork pull request workflows from outside
  collaborators*: chọn **Require approval for all external contributors**. Repo public
  có self-hosted runner: PR từ fork có thể sửa workflow để chạy lên runner; duyệt tay
  trước khi chạy là lớp chặn chính. Không bấm duyệt PR fork có sửa `.github/workflows/`.

Thư mục `DEPLOY_PATH` phải ở nhánh `main`, không có commit hay sửa đổi riêng (tệp
`deploy/.env` đã gitignore nên được giữ nguyên).

## Sự cố thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Caddy không lấy được chứng chỉ | DNS chưa trỏ đúng IP, hoặc cổng 80/443 bị tường lửa chặn (cả firewall của nhà cung cấp VPS). |
| `421` / `Invalid Host header` | `PDCA_DOMAIN` trong `deploy/.env` khác tên miền đang gọi; sửa rồi `up -d` lại. |
| `unauthorized` | Token sai, hết hạn hoặc đã thu hồi; thiếu tiền tố `Bearer `. |
| `claude mcp list` báo lỗi kết nối | Thử lệnh `curl` ở mục 3 trên cùng máy để tách lỗi mạng/TLS khỏi lỗi cấu hình Claude Code. |
| Job `deploy` lỗi `Not possible to fast-forward` | Thư mục trên máy chủ có commit/sửa đổi riêng; `git status` rồi đưa về `origin/main`. |
| Job `deploy` treo ở *Waiting for a runner* | Runner nhãn `pdca` chưa chạy: `sudo ./svc.sh status` trong thư mục runner. |
| `502 Bad Gateway` từ nginx | MCP Server chưa lên hoặc sai cổng: `curl localhost:8010/mcp` như mục 2; khớp `proxy_pass` với `MCP_HTTP_PORT`. |
| Đổi mật khẩu trong `.env` không có tác dụng | Mật khẩu DB chỉ đặt khi khởi tạo volume (`deploy/initdb/`); đổi bằng `ALTER ROLE` hoặc xóa volume `pdca_pgdata` (mất dữ liệu). |

Ghi chú bảo mật: PostgreSQL chỉ mở trên `127.0.0.1` của máy chủ; `deploy/.env`
có quyền 600 và không commit. Sao lưu (LLD 9.2) chưa tự động hóa.
