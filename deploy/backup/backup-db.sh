#!/usr/bin/env bash
# Sao lưu PostgreSQL của PDCA (LLD 9.2, NFR-REL-02).
#
# pg_dump định dạng custom (-Fc) hằng ngày, kiểm tra bản sao đọc được và có bảng cốt lõi,
# ghi mã băm SHA-256, rồi xoay vòng: 7 bản ngày + 4 bản tuần (Chủ Nhật) + 6 bản tháng (ngày 01).
#
# Chạy bằng tài khoản thuộc nhóm docker (trên máy chủ: pdca-runner), từ systemd timer
# (deploy/backup/pdca-db-backup.timer) hoặc tay:  deploy/backup/backup-db.sh
#
# Biến môi trường (tùy chọn):
#   PDCA_BACKUP_DIR            thư mục lưu (mặc định /opt/pdca-backups)
#   PDCA_BACKUP_DAILY_KEEP     số bản ngày giữ lại (mặc định 7)
#   PDCA_BACKUP_WEEKLY_KEEP    số bản tuần (mặc định 4)
#   PDCA_BACKUP_MONTHLY_KEEP   số bản tháng (mặc định 6)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml")
BACKUP_DIR="${PDCA_BACKUP_DIR:-/opt/pdca-backups}"
DAILY_KEEP="${PDCA_BACKUP_DAILY_KEEP:-7}"
WEEKLY_KEEP="${PDCA_BACKUP_WEEKLY_KEEP:-4}"
MONTHLY_KEEP="${PDCA_BACKUP_MONTHLY_KEEP:-6}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

# Chỉ chủ sở hữu đọc được: bản sao chứa toàn bộ dữ liệu, kể cả mã băm token.
umask 077
mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly" "$BACKUP_DIR/monthly"

stamp="$(date +%Y%m%d_%H%M%S)"
final="$BACKUP_DIR/daily/pdca_${stamp}.dump"
partial="${final}.partial"
trap 'rm -f "$partial"' EXIT

log "Bắt đầu sao lưu DB pdca -> $final"
# Trong container db, kết nối cục bộ bằng tài khoản postgres không cần mật khẩu.
"${COMPOSE[@]}" exec -T db pg_dump -U postgres -d pdca -Fc >"$partial"

size="$(wc -c <"$partial")"
if [ "$size" -lt 1024 ]; then
  log "LỖI: bản sao quá nhỏ (${size} byte), bỏ."
  exit 1
fi

# Mục lục đọc được và có bảng cốt lõi (audit_log, users, flyway_schema_history).
catalog="$("${COMPOSE[@]}" exec -T db pg_restore -l <"$partial")"
for table in audit_log users flyway_schema_history; do
  if ! grep -q "TABLE public ${table} " <<<"$catalog"; then
    log "LỖI: bản sao thiếu bảng ${table}, bỏ."
    exit 1
  fi
done

mv "$partial" "$final"
(cd "$(dirname "$final")" && sha256sum "$(basename "$final")" >"$(basename "$final").sha256")
log "Đã sao lưu: $(basename "$final"), ${size} byte"

# Bản tuần (Chủ Nhật) và bản tháng (ngày 01) là bản sao của bản ngày.
if [ "$(date +%u)" = "7" ]; then
  cp "$final" "$BACKUP_DIR/weekly/"
  cp "$final.sha256" "$BACKUP_DIR/weekly/"
fi
if [ "$(date +%d)" = "01" ]; then
  cp "$final" "$BACKUP_DIR/monthly/"
  cp "$final.sha256" "$BACKUP_DIR/monthly/"
fi

# Xoay vòng: giữ N bản mới nhất mỗi thư mục (tên có dấu thời gian nên sắp theo tên).
prune() {
  local dir="$1" keep="$2" old
  while IFS= read -r old; do
    rm -f "$old" "$old.sha256"
    log "Xóa bản cũ: $(basename "$old")"
  done < <(find "$dir" -maxdepth 1 -name 'pdca_*.dump' | sort | head -n "-${keep}")
}
prune "$BACKUP_DIR/daily" "$DAILY_KEEP"
prune "$BACKUP_DIR/weekly" "$WEEKLY_KEEP"
prune "$BACKUP_DIR/monthly" "$MONTHLY_KEEP"

log "Xong. Dung lượng thư mục sao lưu: $(du -sh "$BACKUP_DIR" | cut -f1)"
