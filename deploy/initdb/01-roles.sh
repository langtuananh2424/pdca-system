#!/bin/sh
# Tạo vai trò đăng nhập (LLD 2.3) khi khởi tạo volume Postgres lần đầu.
# Ở đây chỉ tạo login + mật khẩu (thuộc về môi trường); quyền trên bảng
# (grant cho pdca_app, pdca_readonly) do migration Flyway cấp.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v flyway_pw="$FLYWAY_PASSWORD" \
  -v app_pw="$PDCA_APP_PASSWORD" \
  -v ro_pw="$PDCA_READONLY_PASSWORD" <<'SQL'
create role flyway login password :'flyway_pw';
create role pdca_app login password :'app_pw';
create role pdca_readonly login password :'ro_pw';
alter database :"DBNAME" owner to flyway;
revoke all on database :"DBNAME" from public;
grant connect on database :"DBNAME" to pdca_app, pdca_readonly;
SQL
