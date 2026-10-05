---
key: tra-cuu-truoc-khi-ghi
title: Tra cứu mã bằng tool, không đoán
mandatory: true
---
- **Thiếu mã thì tra, không đoán**: trước khi gọi tool ghi (`update_task_status`, `assign_task`, `create_task`, `answer_question`...), nếu chưa có mã (`task_id`, `user_id`/`assignee_id`, `project_id`, `plan_id`, `question_id`) hoặc người dùng chỉ nói "task đó", "người đó", hãy tra bằng tool đọc: `get_my_tasks`, `list_project_tasks`, `list_project_members`, `list_plans`, `get_my_questions`, `whoami`. Có nhiều kết quả khớp thì hỏi lại người dùng.
- **Phiên mới không nhớ**: máy chủ không lưu cuộc trò chuyện. Mã nhắc lại từ phiên trước phải tra lại trước khi dùng cho thao tác ghi; dùng `/pdca:bat-dau` để dựng lại ngữ cảnh.
