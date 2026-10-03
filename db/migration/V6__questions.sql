-- V6: hỏi cấp trên (UC-18, FR-ASK; LLD 2.2 mục V6). Nguồn: docs/04-LLD.md — giữ đồng bộ.
-- Trạng thái `draft_by_agent` và `source_refs` để dành cho P2 (agent soạn nháp, FR-ASK-09).
-- Bảng nhận quyền qua `alter default privileges` của V2; không có đường delete.

create table questions (
  id             bigint generated always as identity primary key,
  asker_id       bigint not null references users(id),
  recipient_id   bigint not null references users(id),   -- do resolve_recipient chọn lúc hỏi (LLD 4.2); không đổi sau đó
  project_id     bigint references projects(id),
  task_id        bigint references tasks(id),
  body           text not null check (char_length(body) between 1 and 2000),
  status         text not null default 'open'
                 check (status in ('open','answered','declined','expired','cancelled')),
  decline_reason text check (char_length(decline_reason) <= 2000),
  due_at         timestamptz not null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  check (asker_id <> recipient_id)
);
create index questions_recipient_idx on questions (recipient_id, status, created_at);
create index questions_asker_idx on questions (asker_id, created_at);
create index questions_open_due_idx on questions (due_at) where status = 'open';

create table question_answers (
  id          bigint generated always as identity primary key,
  question_id bigint not null references questions(id),
  author_id   bigint not null references users(id),
  status      text not null check (status in ('draft_by_agent','sent')),
  body        text not null check (char_length(body) <= 2000),
  source_refs jsonb not null default '[]'::jsonb,        -- P2: chỉ id nguồn, không chứa nội dung
  created_at  timestamptz not null default now(),
  sent_at     timestamptz
);
-- Mỗi câu hỏi tối đa một câu trả lời đã gửi (FR-ASK-04; chặn gửi hai lần ở tầng DB).
create unique index question_one_sent_idx on question_answers (question_id) where status = 'sent';

-- Thêm hai loại tin; ràng buộc `kind` của V1 không đặt tên nên PostgreSQL gán outbound_messages_kind_check.
alter table outbound_messages drop constraint outbound_messages_kind_check;
alter table outbound_messages add constraint outbound_messages_kind_check
  check (kind in ('morning_nudge','progress_ask','clarify','reminder','summary','proposal_notice',
                  'question_notice','answer_notice'));
