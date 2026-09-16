"""하루 경계 계산 (BR-01).

P2J 의 하루는 자정이 아니라 04:00 KST 에 바뀐다. 새벽 3시에 끝낸 할 일은 "어제"의 성과다.
날짜를 다루는 모든 코드는 이 파일의 함수만 쓴다. 서버 로컬 timezone 에 의존하지 않는다
(Railway 컨테이너는 UTC, 개발 PC 는 KST).

모바일 MockStore.serverToday() 와 같은 규칙이므로 mock ↔ 실서버 전환 시 날짜가 튀지 않는다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from app.core.config import get_settings

# 한국은 서머타임이 없다. 고정 오프셋으로 충분하다.
KST = timezone(timedelta(hours=9), name="KST")


def _start_hour(day_start_hour: int | None) -> int:
    hour = get_settings().service_day_start_hour if day_start_hour is None else day_start_hour
    if not 0 <= hour <= 23:
        raise ValueError(f"day_start_hour 는 0~23 이어야 합니다. 받은 값: {hour}")
    return hour


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """DB 에서 읽은 시각을 aware UTC 로 맞춘다.

    SQLite 는 timezone 정보를 잃고 naive 로 돌려준다(PostgreSQL 은 aware). 저장은 언제나
    UTC 이므로 그대로 붙이면 된다. 저장된 시각을 `now_utc()` 와 비교하기 전에 반드시 거친다 —
    빠뜨리면 테스트(SQLite)에서만 "can't compare offset-naive and offset-aware" 가 난다.
    """
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def service_today(now: datetime | None = None, day_start_hour: int | None = None) -> date:
    """주어진 시각(기본 현재) 기준 "서비스상 오늘"."""
    instant = now or now_utc()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    kst = instant.astimezone(KST)
    return (kst - timedelta(hours=_start_hour(day_start_hour))).date()


def service_day_start(day: date, day_start_hour: int | None = None) -> datetime:
    """서비스 날짜가 시작되는 절대 시각. 2026-09-04 → 2026-09-04T04:00+09:00."""
    return datetime(day.year, day.month, day.day, _start_hour(day_start_hour), tzinfo=KST)


def service_day_range(day: date, day_start_hour: int | None = None) -> tuple[datetime, datetime]:
    """[시작, 끝) 반구간. DB 에서 completed_at 을 날짜로 묶을 때 쓴다."""
    start = service_day_start(day, day_start_hour)
    return start, start + timedelta(days=1)


def week_range(day: date) -> tuple[date, date]:
    """그 날짜가 속한 주의 (월요일, 일요일). Goal.progress.current_week_* 기준."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def to_kst_iso(value: datetime | None) -> str | None:
    """시각을 항상 KST 오프셋(+09:00) 문자열로 직렬화한다 (§1.1). naive 는 UTC 로 본다."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(KST).replace(microsecond=0).isoformat()


def to_date_str(value: date | None) -> str | None:
    return value.isoformat() if value else None
