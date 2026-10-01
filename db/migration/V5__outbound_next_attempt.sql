-- V5: giãn cách thử lại khi gửi tin lỗi tạm thời (SDD 4.10: failed → queued có giới hạn).
-- Không có cột này thì bộ gửi chạy mỗi phút sẽ đốt hết lượt thử trong vài phút
-- khi máy chủ SMTP gián đoạn ngắn.

alter table outbound_messages add column next_attempt_at timestamptz;

create index outbound_pending_idx on outbound_messages (next_attempt_at, id)
  where status in ('queued', 'failed');
