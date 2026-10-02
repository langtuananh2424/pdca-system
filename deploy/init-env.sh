#!/usr/bin/env sh
# Tạo deploy/.env cho máy chủ từ deploy/.env.example, sinh mật khẩu ngẫu nhiên.
#   sh deploy/init-env.sh <tên miền> [--no-seed]
# <tên miền>: tên miền trỏ về IP máy chủ (Caddy xin chứng chỉ Let's Encrypt).
#   Chưa có tên miền: dùng <ip-có-gạch-ngang>.sslip.io, ví dụ 203-0-113-7.sslip.io.
# --no-seed: không nạp dữ liệu mẫu db/seed (staging/prod thật).
# Không ghi đè deploy/.env đã có: mật khẩu DB chỉ đặt được khi khởi tạo volume.
set -eu

domain="${1:-}"
if [ -z "$domain" ]; then
	echo "usage: sh deploy/init-env.sh <domain> [--no-seed]" >&2
	exit 2
fi
seed=1
[ "${2:-}" = "--no-seed" ] && seed=0

dir="$(cd "$(dirname "$0")" && pwd)"
env_file="$dir/.env"
if [ -e "$env_file" ]; then
	echo "$env_file already exists; edit it by hand" >&2
	exit 1
fi

gen() { openssl rand -hex 24; }

umask 077
sed \
	-e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(gen)|" \
	-e "s|^FLYWAY_PASSWORD=.*|FLYWAY_PASSWORD=$(gen)|" \
	-e "s|^PDCA_APP_PASSWORD=.*|PDCA_APP_PASSWORD=$(gen)|" \
	-e "s|^PDCA_READONLY_PASSWORD=.*|PDCA_READONLY_PASSWORD=$(gen)|" \
	-e "s|^PDCA_DOMAIN=.*|PDCA_DOMAIN=$domain|" \
	-e "s|^PDCA_ENV=.*|PDCA_ENV=staging|" \
	"$dir/.env.example" >"$env_file"
if [ "$seed" -eq 0 ]; then
	sed -i "s|^# FLYWAY_LOCATIONS=.*|FLYWAY_LOCATIONS=filesystem:/flyway/sql|" "$env_file"
fi

echo "wrote $env_file (domain: $domain, seed: $([ "$seed" -eq 1 ] && echo yes || echo no))"
