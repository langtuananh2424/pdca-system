# Sao lưu DB PDCA

Sao lưu PostgreSQL của PDCA (LLD 9.2, NFR-REL-02) bằng `pg_dump` định dạng custom, chạy hằng ngày qua systemd timer, có kiểm tra tính toàn vẹn và xoay vòng.

| Tệp | Việc |
|---|---|
| `backup-db.sh` | Sao lưu một lần: dump, kiểm tra đọc được và có bảng cốt lõi, ghi SHA-256, xoay vòng |
| `restore-db.sh` | `--verify`: khôi phục thử vào container tạm (không đụng DB thật); `--live --yes`: thay DB đang chạy |
| `pdca-db-backup.service`, `.timer` | Chạy hằng ngày 03:45 (lệch giờ backup của các dự án khác), `Persistent=true` nên chạy bù khi máy tắt đúng giờ |

Lưu tại `/opt/pdca-backups` (đổi bằng `PDCA_BACKUP_DIR`): `daily/` giữ 7 bản, `weekly/` 4 bản (Chủ Nhật), `monthly/` 6 bản (ngày 01). Bản sao quyền `600`, chỉ chủ sở hữu đọc được vì chứa toàn bộ dữ liệu, kể cả mã băm token.

## Cài trên máy chủ (một lần)

Tên tài khoản và đường dẫn dưới đây theo `deploy/README.md` mục 0 và 7 (`pdca-runner`, `/opt/pdca-system`). Các lệnh `sudo` chạy bằng tài khoản quản trị.

```bash
sudo mkdir -p /opt/pdca-backups && sudo chown pdca-runner:pdca-runner /opt/pdca-backups && sudo chmod 700 /opt/pdca-backups
```

```bash
sudo cp /opt/pdca-system/deploy/backup/pdca-db-backup.service /opt/pdca-system/deploy/backup/pdca-db-backup.timer /etc/systemd/system/
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now pdca-db-backup.timer
```

Chạy thử ngay một lần và xem kết quả:

```bash
sudo systemctl start pdca-db-backup.service && sudo journalctl -u pdca-db-backup.service -n 15 --no-pager
```

Kết quả đúng: dòng `Đã sao lưu: pdca_<thời gian>.dump, <số byte>` và `Xong.`. Kiểm tra lịch:

```bash
systemctl list-timers pdca-db-backup.timer --no-pager
```

> Hai tệp `.service` và `.timer` được **sao chép** vào `/etc/systemd/system`, nên CI không tự cập nhật chúng. Sửa hai tệp này trong repo thì phải chép lại và chạy `daemon-reload`. Còn `backup-db.sh` luôn là bản mới nhất vì chạy thẳng từ `/opt/pdca-system`.

## Kiểm tra khôi phục (làm định kỳ, ít nhất mỗi quý)

Sao lưu chưa khôi phục thử thì chưa chắc dùng được. `--verify` dựng một container Postgres tạm, nạp bản sao, in số liệu rồi xóa container; không chạm DB đang chạy:

```bash
sudo -iu pdca-runner /opt/pdca-system/deploy/backup/restore-db.sh /opt/pdca-backups/daily/<tệp>.dump --verify
```

Kết quả đúng kết thúc bằng `Kiểm tra khôi phục: ĐẠT`, kèm phiên bản Flyway (hiện v6) và số dòng của `users`, `tasks`, `reports`, `audit_log`.

## Khôi phục thật

Chỉ khi cần thay DB đang chạy (hỏng dữ liệu, lỡ xóa). Lệnh này tự **sao lưu DB hiện tại trước**, dừng `mcp` và `scheduler`, khôi phục, rồi bật lại:

```bash
sudo -iu pdca-runner /opt/pdca-system/deploy/backup/restore-db.sh /opt/pdca-backups/daily/<tệp>.dump --live --yes
```

Khôi phục sang máy mới: dựng stack bằng `deploy/init-env.sh` (tạo vai trò qua `deploy/initdb/`), chép tệp `.dump` sang, rồi chạy lệnh trên.

## Giới hạn hiện tại

- **Chưa có bản sao ngoài máy chủ.** Mọi bản nằm trên cùng ổ đĩa với DB và các dự án khác, nên chỉ chống được xóa nhầm và hỏng dữ liệu, không chống hỏng ổ đĩa. Bước kế tiếp là đồng bộ thư mục này ra nơi khác (ví dụ rclone lên kho lưu trữ đám mây, mã hóa trước khi gửi vì dữ liệu có thể chứa thông tin cá nhân).
- Chưa có cảnh báo khi backup lỗi; hiện chỉ xem qua `journalctl -u pdca-db-backup.service` hoặc `systemctl status pdca-db-backup.service` (trạng thái `failed`).
- Bản sao là logic (`pg_dump`), không phải bản chụp vật lý; khôi phục theo thời điểm (PITR) chưa có.
