-- V1: lược đồ lõi P1 (LLD 2.2). Nguồn: docs/04-LLD.md mục 2.2 — giữ đồng bộ.

-- Tổ chức
create table departments (
  id          bigint generated always as identity primary key,
  name        text not null unique,
  head_user_id bigint,                      -- FK thêm sau khi có users
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table users (
  id            bigint generated always as identity primary key,
  name          text not null,
  email         text not null unique,
  role          text not null check (role in ('staff','dept_head','director','admin')),
  department_id bigint references departments(id),
  manager_id    bigint references users(id),
  status        text not null default 'active' check (status in ('active','locked')),
  away_until    date,
  work_start    time not null default '08:30',
  work_end      time not null default '17:30',
  timezone      text not null default 'Asia/Bangkok',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz
);
alter table departments add constraint fk_dept_head
  foreign key (head_user_id) references users(id);

create table projects (
  id            bigint generated always as identity primary key,
  name          text not null,
  department_id bigint not null references departments(id),
  status        text not null default 'active' check (status in ('active','paused','closed')),
  config        jsonb not null default '{}'::jsonb,   -- FR-ORG-04
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz
);

create table project_members (
  project_id   bigint not null references projects(id),
  user_id      bigint not null references users(id),
  project_role text not null default 'member' check (project_role in ('member','lead')),
  primary key (project_id, user_id)
);

create table api_tokens (
  id           bigint generated always as identity primary key,
  user_id      bigint not null references users(id),
  token_hash   bytea not null unique,        -- SHA-256 của token
  label        text,
  expires_at   timestamptz not null,
  revoked_at   timestamptz,
  last_used_at timestamptz,
  created_at   timestamptz not null default now()
);

-- PDCA: Plan
create table plans (
  id         bigint generated always as identity primary key,
  project_id bigint references projects(id),
  parent_id  bigint references plans(id),
  level      text not null check (level in ('year','month','week','day')),
  goal       text not null,
  start_date date not null,
  end_date   date not null,
  owner_id   bigint not null references users(id),
  status     text not null default 'active' check (status in ('draft','active','done','cancelled')),
  version    int not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  deleted_at timestamptz,
  check (end_date >= start_date)
);
create index plans_parent_idx on plans(parent_id);
create index plans_project_idx on plans(project_id, level, start_date);

create table plan_versions (
  plan_id    bigint not null references plans(id),
  version    int not null,
  snapshot   jsonb not null,
  changed_by bigint not null references users(id),
  reason     text,
  evidence   jsonb,                           -- ví dụ danh sách report_id làm căn cứ
  changed_at timestamptz not null default now(),
  primary key (plan_id, version)
);

-- PDCA: Do
create table tasks (
  id          bigint generated always as identity primary key,
  plan_id     bigint references plans(id),
  project_id  bigint not null references projects(id),
  assignee_id bigint not null references users(id),
  created_by  bigint not null references users(id),
  title       text not null,
  detail      text,
  due_date    date,
  status      text not null default 'todo'
              check (status in ('todo','in_progress','blocked','done','cancelled')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  deleted_at  timestamptz
);
create index tasks_assignee_idx on tasks(assignee_id, status, due_date);

create table task_events (
  id          bigint generated always as identity primary key,
  task_id     bigint not null references tasks(id),
  from_status text,
  to_status   text not null,
  by_user_id  bigint not null references users(id),
  note        text,
  at          timestamptz not null default now()
);

create table activities (
  id         bigint generated always as identity primary key,
  user_id    bigint not null references users(id),
  project_id bigint not null references projects(id),
  task_id    bigint references tasks(id),
  summary    text not null check (char_length(summary) <= 2000),
  source     text not null default 'user' check (source in ('user','agent_approved','hook_approved')),
  at         timestamptz not null default now()
);
create index activities_user_day_idx on activities(user_id, at);

-- PDCA: Check
create table reports (
  id                 bigint generated always as identity primary key,
  user_id            bigint not null references users(id),
  project_id         bigint not null references projects(id),
  report_date        date not null,
  done               text not null default '',
  blockers           text not null default '',
  schedule_conflicts text not null default '',
  source             text not null check (source in ('claude_code','channel_reply','manual')),
  status             text not null check (status in ('draft_by_agent','submitted','not_reported')),
  raw_text_approved  text,                   -- văn bản người dùng đã duyệt (NFR-PRV-01)
  confidence         numeric(3,2),
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (user_id, project_id, report_date)  -- FR-CHK-03
);

create table blockers (
  id        bigint generated always as identity primary key,
  report_id bigint not null references reports(id),
  kind      text not null check (kind in ('technical','people','external','schedule','other')),
  severity  text not null default 'medium' check (severity in ('low','medium','high')),
  text      text not null
);

-- Tổng hợp và Action (P2 dùng, tạo sẵn schema)
create table summaries (
  id               bigint generated always as identity primary key,
  scope            text not null check (scope in ('project','department','company')),
  scope_id         bigint,
  period_kind      text not null check (period_kind in ('day','week','month')),
  period_start     date not null,
  content          jsonb not null,
  source_report_ids bigint[] not null default '{}',
  generated_at     timestamptz not null default now(),
  unique (scope, scope_id, period_kind, period_start)
);

create table action_proposals (
  id                 bigint generated always as identity primary key,
  plan_id            bigint not null references plans(id),
  base_version       int not null,
  change             jsonb not null,
  rationale          text not null,
  evidence_report_ids bigint[] not null default '{}',
  status             text not null default 'proposed'
                     check (status in ('proposed','approved','rejected','expired','applied','apply_failed')),
  proposed_at        timestamptz not null default now(),
  decided_by         bigint references users(id),
  decided_at         timestamptz,
  decision_note      text
);

-- Vận hành
create table outbound_messages (
  id          bigint generated always as identity primary key,
  user_id     bigint not null references users(id),
  channel     text not null,
  kind        text not null check (kind in ('morning_nudge','progress_ask','clarify','reminder','summary','proposal_notice')),
  payload     jsonb not null,
  status      text not null default 'queued' check (status in ('queued','sent','replied','failed','dead')),
  attempts    int not null default 0,
  dedupe_key  text not null unique,
  external_id text,
  created_at  timestamptz not null default now(),
  sent_at     timestamptz
);

create table job_runs (
  id            bigint generated always as identity primary key,
  job           text not null,
  scheduled_for timestamptz not null,
  status        text not null check (status in ('running','succeeded','failed','skipped')),
  started_at    timestamptz not null default now(),
  finished_at   timestamptz,
  detail        jsonb,
  unique (job, scheduled_for)
);

create table llm_usage (
  id            bigint generated always as identity primary key,
  task_kind     text not null,
  model         text not null,
  input_tokens  int not null,
  output_tokens int not null,
  cached_tokens int not null default 0,
  cost_usd      numeric(10,5),
  user_id       bigint references users(id),
  at            timestamptz not null default now()
);

create table audit_log (
  id              bigint generated always as identity primary key,
  at              timestamptz not null default now(),
  request_id      text not null,
  user_id         bigint references users(id),
  actor_kind      text not null check (actor_kind in ('user_mcp','agent','admin_cli','system')),
  tool            text not null,
  params_redacted jsonb,
  result          text not null check (result in ('ok','denied','error')),
  error_code      text
);
create index audit_at_idx on audit_log(at);
create index audit_user_idx on audit_log(user_id, at);

-- Audit chỉ thêm (FR-AUD-02): chặn UPDATE/DELETE bằng quyền
revoke update, delete, truncate on audit_log from public;
