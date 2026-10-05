#!/usr/bin/env bash
# Khôi phục hoặc kiểm tra bản sao DB PDCA (LLD 9.2, NFR-REL-02).
#
#   restore-db.sh <tệp.dump> --verify         khôi phục thử vào container tạm, in số liệu; KHÔNG đụng DB thật
#   restore-db.sh <tệp.dump> --live --yes     thay DB pdca đang chạy bằng bản sao (dừng mcp, scheduler)
#
# --live luôn sao lưu DB hiện tại trước (deploy/backup/backup-db.sh). Chạy bằng tài khoản thuộc nhóm docker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml")
POSTGRES_IMAGE="postgres:16-alpine" # giữ đồng bộ deploy/docker-compose.yml

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
usage() {
  echo "usage: $0 <tệp.dump> --verify | --live --yes" >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
dump="$1"
mode="$2"
confirm="${3:-}"
[ -f "$dump" ] || { echo "Không thấy tệp: $dump" >&2; exit 2; }

# Kiểm tra mã băm nếu có tệp .sha256 cạnh bản sao.
if [ -f "$dump.sha256" ]; then
  (cd "$(dirname "$dump")" && sha256sum -c "$(basename "$dump").sha256") || {
    echo "Mã băm không khớp: bản sao có thể đã hỏng." >&2
    exit 1
  }
fi

case "$mode" in
  --verify)
    name="pdca-restore-verify-$$"
    trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
    log "Dựng container tạm $name"
    docker run -d --rm --name "$name" -e POSTGRES_HOST_AUTH_METHOD=trust \
      -e POSTGRES_DB=pdca "$POSTGRES_IMAGE" >/dev/null
    # Image postgres khởi động hai lần (máy chủ tạm để khởi tạo, rồi máy chủ thật): đợi lần thứ hai.
    for _ in $(seq 1 60); do
      [ "$(docker logs "$name" 2>&1 | grep -c 'ready to accept connections')" -ge 2 ] && break
      sleep 1
    done
    docker exec "$name" pg_isready -U postgres -d pdca >/dev/null
    log "Khôi phục vào container tạm"
    # Container tạm không có vai trò flyway/pdca_app: bỏ chủ sở hữu và quyền, chỉ kiểm dữ liệu.
    docker exec -i "$name" pg_restore -U postgres -d pdca --no-owner --no-privileges <"$dump"
    log "Số liệu sau khi khôi phục:"
    docker exec "$name" psql -U postgres -d pdca -Atc \
      "select 'flyway_version', max(version::int) from flyway_schema_history where success" \
      -c "select 'users', count(*) from users" \
      -c "select 'tasks', count(*) from tasks" \
      -c "select 'reports', count(*) from reports" \
      -c "select 'audit_log', count(*) from audit_log"
    log "Kiểm tra khôi phục: ĐẠT (container tạm đã được xóa)"
    ;;
  --live)
    [ "$confirm" = "--yes" ] || {
      echo "Khôi phục --live THAY THẾ dữ liệu hiện tại. Thêm --yes để xác nhận." >&2
      exit 2
    }
    log "Sao lưu DB hiện tại trước khi khôi phục"
    "$ROOT/deploy/backup/backup-db.sh"
    log "Dừng mcp và scheduler"
    "${COMPOSE[@]}" stop mcp scheduler
    log "Khôi phục (xóa đối tượng cũ rồi nạp lại)"
    "${COMPOSE[@]}" exec -T db pg_restore -U postgres -d pdca --clean --if-exists <"$dump"
    log "Bật lại mcp và scheduler"
    "${COMPOSE[@]}" --profile apps up -d mcp scheduler
    log "Đã khôi phục từ $(basename "$dump")"
    ;;
  *)
    usage
    ;;
esac
