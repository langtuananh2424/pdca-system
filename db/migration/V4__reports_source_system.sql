-- V4: báo cáo do hệ thống tạo (job mark_not_reported, FR-NTF-05) có source = 'system'.
-- Bản ghi not_reported không mang nội dung nào do AI sinh (T-05).

alter table reports drop constraint reports_source_check;
alter table reports add constraint reports_source_check
  check (source in ('claude_code', 'channel_reply', 'manual', 'system'));
