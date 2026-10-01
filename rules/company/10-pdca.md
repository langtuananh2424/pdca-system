---
key: chu-trinh-pdca
title: Chu trình PDCA
mandatory: true
---
Công việc đi theo chu trình Plan – Do – Check – Action:

- **Plan**: kế hoạch năm → tháng → tuần → ngày do giám đốc và trưởng phòng lập.
- **Do**: làm task được giao; cập nhật trạng thái task (`update_task_status`) và ghi hoạt động ngắn (`log_activity`) khi người dùng đồng ý.
- **Check**: cuối ngày chốt báo cáo theo mẫu, mỗi project một báo cáo (lệnh `/pdca:chot-ngay`).
- **Action**: đề xuất chỉnh kế hoạch chỉ là đề xuất; người có thẩm quyền duyệt mới được áp dụng.

Khi người dùng hỏi về việc của họ, dùng công cụ của máy chủ `pdca` (`whoami`, `get_my_tasks`, `get_my_day_context`, `get_my_reports`) thay vì đoán.
