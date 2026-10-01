-- V3: xóa mềm cho blockers (LLD 2.1, DR-05).
-- `submit_report mode=replace` cần bỏ các vướng mắc cũ của báo cáo; pdca_app
-- không có quyền delete (LLD 2.3) nên đánh dấu deleted_at thay vì xóa.

alter table blockers add column deleted_at timestamptz;

create index blockers_report_idx on blockers(report_id) where deleted_at is null;
