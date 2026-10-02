-- V2: cấp quyền cho vai trò ứng dụng (LLD 2.3).
-- Login + mật khẩu của pdca_app, pdca_readonly do môi trường tạo trước
-- (deploy/initdb/01-roles.sh); migration chỉ cấp quyền trên đối tượng.

do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'pdca_app')
     or not exists (select 1 from pg_roles where rolname = 'pdca_readonly') then
    raise exception 'missing roles pdca_app/pdca_readonly: create them before migrating (deploy/initdb/01-roles.sh)';
  end if;
end
$$;

grant usage on schema public to pdca_app, pdca_readonly;

-- pdca_app: select/insert/update trên bảng nghiệp vụ, không delete (xóa mềm, DR-05).
grant select, insert, update on all tables in schema public to pdca_app;
grant usage on all sequences in schema public to pdca_app;

-- audit_log chỉ thêm (FR-AUD-02): pdca_app chỉ select + insert.
revoke update, delete, truncate on audit_log from pdca_app;

-- Bảng lịch sử Flyway không thuộc nghiệp vụ.
revoke all on flyway_schema_history from pdca_app;

-- pdca_readonly: chỉ đọc (Dashboard P2, báo cáo).
grant select on all tables in schema public to pdca_readonly;
revoke all on flyway_schema_history from pdca_readonly;

-- Bảng tạo ở migration sau (do vai trò flyway sở hữu) nhận cùng quyền mặc định.
-- Bảng chỉ thêm như audit_log phải revoke update tường minh trong migration đó.
alter default privileges for role flyway in schema public
  grant select, insert, update on tables to pdca_app;
alter default privileges for role flyway in schema public
  grant usage on sequences to pdca_app;
alter default privileges for role flyway in schema public
  grant select on tables to pdca_readonly;
