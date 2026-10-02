# Rule ba lớp (FR-RULE-01..06)

Nguồn duy nhất của rule công ty và phòng ban. Lớp cá nhân là `CLAUDE.md` của
từng người trên máy họ (chỉ bổ sung, không làm trái mục bắt buộc).

```text
rules/
├─ company/                 # thầy Phúc sửa
│  └─ *.md
└─ departments/
   └─ <slug-phong>/         # trưởng phòng sửa; slug: a-z, 0-9, dấu gạch
      └─ *.md
```

Mỗi tệp là một mục rule:

```markdown
---
key: bao-cao-ngay        # a-z, 0-9, -, _ ; duy nhất trong một lớp
title: Mẫu báo cáo ngày
mandatory: true          # true: lớp dưới không được thay mục cùng key
---
Nội dung Markdown...
```

Ghép (SDD 4.11.2): công ty trước, rồi phòng ban. Mục phòng ban cùng `key` thay
mục công ty, trừ khi mục công ty `mandatory: true` — khi đó mục phòng ban bị bỏ
và lệnh dựng báo `skipped`.

Sau khi sửa rule, dựng lại plugin và commit cả hai:

```bash
uv run pdca-admin rules build
```

CI chạy `pdca-admin rules build --check` và báo lỗi nếu `plugin/rules/` chưa
khớp. Mỗi tệp ghép phải ≤ 10.000 ký tự (giới hạn ngữ cảnh của hook SessionStart).

> Bộ nguyên tắc làm việc của thầy Phúc (GA-04) chưa có; các mục hiện tại chỉ
> là quy tắc vận hành rút từ SRS. Thư mục `departments/thu-nghiem-a` là mẫu
> cho phòng thử nghiệm của seed dev.
