"""Thời điểm gửi tin hỏi–đáp: tôn trọng giờ làm việc và nghỉ phép của người nhận (FR-NTF-04)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def next_delivery(
    now: datetime,
    timezone: str,
    work_start: time,
    work_end: time,
    away_until: date | None,
) -> datetime | None:
    """`None` = gửi ngay; ngược lại là thời điểm (UTC) đầu giờ làm gần nhất sau `now`.

    Ngoài `[work_start, work_end]` hoặc đang nghỉ phép (`away_until` ≥ ngày địa phương) thì
    tin chờ tới `work_start` của ngày làm việc kế tiếp. Chỉ hoãn tin; câu hỏi vẫn `open`.
    """
    tz = ZoneInfo(timezone)
    local = now.astimezone(tz)
    day, at = local.date(), local.time()

    if away_until is not None and day <= away_until:
        start_day = away_until + timedelta(days=1)
    elif at > work_end:
        start_day = day + timedelta(days=1)
    elif at < work_start:
        start_day = day
    else:
        return None
    return datetime.combine(start_day, work_start, tzinfo=tz).astimezone(UTC)
