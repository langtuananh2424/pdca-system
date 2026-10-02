-- Dữ liệu khởi tạo cho môi trường dev/thử nghiệm (LLD 2.5).
-- Chỉ nạp khi FLYWAY_LOCATIONS có db/seed; không dùng cho staging/prod.
-- Không seed token: token cấp qua pdca-admin.
-- Idempotent: Flyway chạy lại tệp R__ khi nội dung đổi.

insert into departments (name) values
  ('Phòng Thử nghiệm A'),
  ('Phòng Thử nghiệm B')
on conflict (name) do nothing;

insert into users (name, email, role, department_id, manager_id) values
  ('Quản trị viên', 'admin@example.com', 'admin', null, null),
  ('Giám đốc (thử nghiệm)', 'director@example.com', 'director', null, null)
on conflict (email) do nothing;

insert into users (name, email, role, department_id, manager_id)
select v.name, v.email, 'dept_head', d.id, u.id
from (values
  ('Trưởng phòng A', 'head.a@example.com', 'Phòng Thử nghiệm A'),
  ('Trưởng phòng B', 'head.b@example.com', 'Phòng Thử nghiệm B')
) as v(name, email, dept)
join departments d on d.name = v.dept
join users u on u.email = 'director@example.com'
on conflict (email) do nothing;

update departments d set head_user_id = u.id, updated_at = now()
from users u
where u.department_id = d.id and u.role = 'dept_head' and d.head_user_id is null;

insert into users (name, email, role, department_id, manager_id)
select v.name, v.email, 'staff', d.id, d.head_user_id
from (values
  ('Nhân viên A1', 'staff.a1@example.com', 'Phòng Thử nghiệm A'),
  ('Nhân viên A2', 'staff.a2@example.com', 'Phòng Thử nghiệm A'),
  ('Nhân viên B1', 'staff.b1@example.com', 'Phòng Thử nghiệm B')
) as v(name, email, dept)
join departments d on d.name = v.dept
on conflict (email) do nothing;

insert into projects (name, department_id, config)
select v.name, d.id, v.config::jsonb
from (values
  ('Dự án thử nghiệm A', 'Phòng Thử nghiệm A',
   '{"kind": "software", "check": {"cadence": "daily", "questions": ["Có blocker kỹ thuật nào không?"]}}'),
  ('Dự án thử nghiệm B', 'Phòng Thử nghiệm B',
   '{"kind": "operations", "check": {"cadence": "daily", "questions": []}}')
) as v(name, dept, config)
join departments d on d.name = v.dept
where not exists (select 1 from projects p where p.name = v.name and p.department_id = d.id);

insert into project_members (project_id, user_id, project_role)
select p.id, u.id, case when u.role = 'dept_head' then 'lead' else 'member' end
from projects p
join users u on u.department_id = p.department_id
where p.name in ('Dự án thử nghiệm A', 'Dự án thử nghiệm B')
  -- Chỉ tài khoản seed: chạy lại không kéo người dùng khác của phòng vào project mẫu.
  and u.email in ('head.a@example.com', 'head.b@example.com', 'staff.a1@example.com',
                  'staff.a2@example.com', 'staff.b1@example.com')
on conflict do nothing;
