# Triển khai MCP Server lên máy chủ và nối Claude Code

Mục tiêu: dựng MCP Server (Streamable HTTP, `https://<tên miền>/mcp`) trên một
máy chủ Linux bằng Docker Compose, rồi nối Claude Code vào để thử. Stack giống
bản cục bộ (LLD 9.1): PostgreSQL 16 + Flyway + MCP Server + Caddy (TLS tự động
bằng Let's Encrypt) + Scheduler (kênh `log`, không gửi tin ra ngoài).

## 1. Chuẩn bị máy chủ

- Linux có Docker Engine và plugin Compose v2 (`docker compose version`).
- 1 vCPU, 1–2 GB RAM là đủ để thử.
- Mở cổng **80** và **443** TCP (Caddy cần cổng 80 để xin chứng chỉ). Ví dụ với ufw:
  `sudo ufw allow 80,443/tcp`.
- Một tên miền có bản ghi A trỏ về IP máy chủ, ví dụ `pdca.congty.vn`.
  Chưa có tên miền thì dùng `sslip.io`: IP `203.0.113.7` → tên miền
  `203-0-113-7.sslip.io` (tự phân giải về IP đó, Let's Encrypt cấp được chứng chỉ).

## 2. Dựng stack

```bash
git clone https://github.com/langtuananh2424/pdca-system.git
cd pdca-system
sh deploy/init-env.sh pdca.congty.vn        # tạo deploy/.env, sinh mật khẩu ngẫu nhiên
docker compose -f deploy/docker-compose.yml --profile apps up -d --build
docker compose -f deploy/docker-compose.yml --profile apps ps
```

`init-env.sh` mặc định **nạp dữ liệu mẫu** (`db/seed`: 2 phòng, 7 người dùng
`*@example.com`) để thử ngay. Khi triển khai thật, chạy
`sh deploy/init-env.sh <tên miền> --no-seed` trên volume mới.

Kiểm tra Caddy đã lấy chứng chỉ (dòng `certificate obtained successfully`):

```bash
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

Trong phiên Claude Code: `/mcp` thấy server `pdca` với 15 tool; thử hỏi
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
  job `check`) chạy ruff, mypy, pytest.
- `main`: nhánh release. Mở PR `develop` → `main`; khi merge, CI chạy lại `check` rồi job
  `deploy` SSH vào máy chủ, `git merge --ff-only` đúng commit đó và
  `docker compose ... --profile apps up -d --build`.
- PR từ fork chỉ chạy `check`, không đọc được secret và không triển khai.

**Chuẩn bị trên máy chủ (một lần):**

```bash
sudo adduser --disabled-password deploy && sudo usermod -aG docker deploy
sudo -iu deploy
git clone https://github.com/langtuananh2424/pdca-system.git ~/pdca-system
cd ~/pdca-system && sh deploy/init-env.sh pdca.congty.vn --no-seed
ssh-keygen -t ed25519 -N '' -f ~/gha_deploy -C github-actions-deploy
cat ~/gha_deploy.pub >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys
cat ~/gha_deploy          # dán vào secret DEPLOY_SSH_KEY, rồi xóa: rm ~/gha_deploy*
```

Thư mục clone phải ở nhánh `main`, không có commit hay sửa đổi riêng (tệp `deploy/.env`
đã gitignore nên được giữ nguyên).

**Trên GitHub** — Settings → Environments → `production` (đặt *Deployment branches* chỉ `main`),
thêm các secret:

| Secret | Giá trị |
|---|---|
| `DEPLOY_HOST` | IP hoặc tên miền máy chủ |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | Khóa riêng `~/gha_deploy` (cả dòng `BEGIN`/`END`) |
| `DEPLOY_KNOWN_HOSTS` | Kết quả `ssh-keyscan -t ed25519 <host>` chạy từ máy tin cậy (đối chiếu fingerprint) |
| `DEPLOY_PATH` | `/home/deploy/pdca-system` |
| `DEPLOY_PORT` | (tùy chọn) cổng SSH nếu khác 22 |

## Sự cố thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Caddy không lấy được chứng chỉ | DNS chưa trỏ đúng IP, hoặc cổng 80/443 bị tường lửa chặn (cả firewall của nhà cung cấp VPS). |
| `421` / `Invalid Host header` | `PDCA_DOMAIN` trong `deploy/.env` khác tên miền đang gọi; sửa rồi `up -d` lại. |
| `unauthorized` | Token sai, hết hạn hoặc đã thu hồi; thiếu tiền tố `Bearer `. |
| `claude mcp list` báo lỗi kết nối | Thử lệnh `curl` ở mục 3 trên cùng máy để tách lỗi mạng/TLS khỏi lỗi cấu hình Claude Code. |
| Job `deploy` lỗi `Not possible to fast-forward` | Thư mục trên máy chủ có commit/sửa đổi riêng; `git status` rồi đưa về `origin/main`. |
| Job `deploy` lỗi `Host key verification failed` | `DEPLOY_KNOWN_HOSTS` sai hoặc máy chủ đổi khóa; chạy lại `ssh-keyscan`. |
| Đổi mật khẩu trong `.env` không có tác dụng | Mật khẩu DB chỉ đặt khi khởi tạo volume (`deploy/initdb/`); đổi bằng `ALTER ROLE` hoặc xóa volume `pdca_pgdata` (mất dữ liệu). |

Ghi chú bảo mật: PostgreSQL chỉ mở trên `127.0.0.1` của máy chủ; `deploy/.env`
có quyền 600 và không commit. Sao lưu (LLD 9.2) chưa tự động hóa.
