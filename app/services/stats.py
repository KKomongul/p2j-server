"""통계 집계 (API 명세 §7 `/stats/*`, ERD §3.7).

두 층으로 나뉜다.

1. **쓰기** — `recalc_day()`. todo 가 바뀌는 모든 경로에서 그날 행을 통째로 다시 계산한다.
   부분 갱신(+1/-1)을 하지 않는 게 핵심이다. 추가·삭제·미루기·소급 완료가 뒤섞여도
   `todos` 가 원본이고 `user_daily_stats` 는 언제나 그걸 그대로 요약한 값이 된다.
   (pending A-5 의 "즉시 반영" 안. 트리거 목록은 services/todos.py 가 호출하는 자리로 고정.)

2. **읽기** — `summary()` · `goal_stats()`. 일자별 값은 집계표에서, 시간대 분포와
   미룬 횟수만 `todos` 를 직접 센다 (04-backend-v1 §5.5).

연속 기록(streak)의 정의는 모바일 MockStore._streak() 과 같다.

- 그날 할 일을 **하나 이상 만들고 전부 끝낸 날**만 연속에 들어간다.
- 할 일이 아예 없던 날은 연속을 끊는다.
- **오늘은 아직 진행 중**이므로, 오늘을 못 끝냈어도 어제까지의 연속은 살아 있다.

mock ↔ 실서버를 오갈 때 화면의 숫자가 튀지 않아야 해서 규칙을 그대로 옮겼다.
"""

from __future__ import annotations

from datetime import UTC, date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import GoalNotFound
from app.core.time import KST, service_today, week_range
from app.db.models.daily_stat import UserDailyStat
from app.db.models.goal import Goal
from app.db.models.todo import Todo
from app.db.models.user import User

# 60분 이상 걸리는 일을 "무거운 일" 로 본다. 계획량 안내(§4)의 근거 지표와 같은 기준이다.
HEAVY_TASK_MINUTES = 60

# 연속 기록을 거꾸로 훑을 때의 상한. 모바일 mock 과 같은 90일.
STREAK_SCAN_DAYS = 90

WEEK_DAYS = 7
MONTH_DAYS = 30


# ---- 쓰기 -------------------------------------------------------------------------


async def recalc_day(db: AsyncSession, user_id: int, day: date) -> UserDailyStat | None:
    """하루치 집계를 `todos` 로부터 다시 계산해 저장한다. 할 일이 없으면 행을 지운다.

    행이 없다 = 그날 아무것도 계획하지 않았다. 연속 기록에서 이 날은 빈칸이 아니라 단절이다.
    0 으로 채운 행을 남기면 "계획이 없던 날" 과 "계획했는데 못 한 날" 이 구분되지 않는다.
    """
    row = (
        await db.execute(
            select(
                func.count(Todo.todo_id),
                func.count(Todo.todo_id).filter(Todo.status == "done"),
                func.coalesce(func.sum(Todo.estimated_minutes), 0),
                func.coalesce(func.sum(Todo.actual_minutes).filter(Todo.status == "done"), 0),
                func.count(Todo.todo_id).filter(
                    Todo.status == "done", Todo.estimated_minutes >= HEAVY_TASK_MINUTES
                ),
            ).where(Todo.user_id == user_id, Todo.date == day, Todo.deleted_at.is_(None))
        )
    ).one()
    total, done, estimated, actual, heavy = (int(v or 0) for v in row)

    stat = await db.get(UserDailyStat, (user_id, day))

    if total == 0:
        if stat is not None:
            await db.delete(stat)
            await db.flush()
        return None

    if stat is None:
        stat = UserDailyStat(user_id=user_id, date=day)
        db.add(stat)

    stat.total_count = total
    stat.done_count = done
    stat.achievement_rate = round(done / total, 3)
    stat.total_estimated_minutes = min(estimated, 32767)  # SMALLINT 상한
    stat.total_actual_minutes = min(actual, 32767)
    stat.heavy_done_count = heavy
    stat.streak_count = await _streak_at(db, user_id, day, cleared=done >= total)
    await db.flush()
    return stat


async def recalc_days(db: AsyncSession, user_id: int, *days: date | None) -> None:
    """여러 날짜를 한 번에. 미루기처럼 원래 날짜와 옮긴 날짜가 둘 다 바뀔 때 쓴다."""
    for day in dict.fromkeys(d for d in days if d is not None):
        await recalc_day(db, user_id, day)


async def _streak_at(db: AsyncSession, user_id: int, day: date, *, cleared: bool) -> int:
    """그날 기준 연속 일수 = 어제 값 + 1 (달성했다면). 저장용 근사값이다.

    과거 날짜를 뒤늦게 고치면 그 뒤 날들의 저장값은 낡는다. 그래서 조회 경로는 이 값을
    쓰지 않고 `current_streak()` 가 행을 직접 훑는다. 이 컬럼은 ERD 호환과
    랭킹의 대량 조회용이며, 매일 04:10 배치가 전일 기준으로 다시 맞춘다.
    """
    if not cleared:
        return 0
    previous = await db.get(UserDailyStat, (user_id, day - timedelta(days=1)))
    base = previous.streak_count if previous and previous.is_cleared else 0
    return min(base + 1, 32767)


# ---- 연속 기록 ---------------------------------------------------------------------


def streak_from_rows(cleared_days: set[date], today: date) -> int:
    """달성한 날짜 집합에서 오늘까지 이어진 연속 일수를 센다.

    오늘을 아직 못 끝냈으면 어제부터 센다 (오늘은 진행 중이므로 끊지 않는다).
    """
    cursor = today if today in cleared_days else today - timedelta(days=1)
    streak = 0
    for _ in range(STREAK_SCAN_DAYS):
        if cursor not in cleared_days:
            break
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def _cleared_days(
    db: AsyncSession, user_id: int, since: date | None = None
) -> tuple[set[date], list[date]]:
    """(달성한 날짜 집합, 정렬된 목록). 달성 = 그날 할 일을 전부 끝냄."""
    stmt = select(UserDailyStat.date).where(
        UserDailyStat.user_id == user_id,
        UserDailyStat.total_count > 0,
        UserDailyStat.done_count >= UserDailyStat.total_count,
    )
    if since is not None:
        stmt = stmt.where(UserDailyStat.date >= since)
    days = sorted((await db.scalars(stmt)).all())
    return set(days), days


async def current_streak(db: AsyncSession, user_id: int, today: date | None = None) -> int:
    day = today or service_today()
    cleared, _ = await _cleared_days(db, user_id, day - timedelta(days=STREAK_SCAN_DAYS))
    return streak_from_rows(cleared, day)


async def longest_streak(db: AsyncSession, user_id: int) -> int:
    _, days = await _cleared_days(db, user_id)
    best = run = 0
    previous: date | None = None
    for day in days:
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


# ---- 읽기 -------------------------------------------------------------------------


def period_range(period: str, today: date) -> tuple[date, date]:
    """`/stats/summary` 의 기간. 명세 §7 예시가 "오늘로 끝나는 최근 N일" 이다.

    그룹 랭킹(§6.5)은 예시가 달력 주(월~일)라서 규칙이 다르다. services/ranking.py 참고.
    """
    span = MONTH_DAYS if period == "month" else WEEK_DAYS
    return today - timedelta(days=span - 1), today


async def summary(db: AsyncSession, user: User, period: str) -> dict[str, Any]:
    today = service_today()
    first, last = period_range(period, today)

    stats = list(
        await db.scalars(
            select(UserDailyStat)
            .where(
                UserDailyStat.user_id == user.user_id,
                UserDailyStat.date >= first,
                UserDailyStat.date <= last,
            )
            .order_by(UserDailyStat.date)
        )
    )
    by_date = {s.date: s for s in stats}

    total = sum(s.total_count for s in stats)
    done = sum(s.done_count for s in stats)
    actual = sum(s.total_actual_minutes for s in stats)

    by_day = []
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        stat = by_date.get(day)
        by_day.append(
            {
                "date": day.isoformat(),
                "achievement_rate": float(stat.achievement_rate) if stat else 0.0,
            }
        )

    return {
        "period": period,
        "range": {"from": first.isoformat(), "to": last.isoformat()},
        "achievement_rate": round(done / total, 2) if total else 0.0,
        "total_todos": total,
        "completed_todos": done,
        "total_actual_minutes": actual,
        "current_streak": await current_streak(db, user.user_id, today),
        "longest_streak": await longest_streak(db, user.user_id),
        "by_day": by_day,
        "by_hour": await _by_hour(db, user.user_id, first, last),
        "most_deferred": await _most_deferred(db, user.user_id, first),
    }


async def _by_hour(
    db: AsyncSession, user_id: int, first: date, last: date
) -> list[dict[str, int]]:
    """완료 시각의 시간대 분포. KST 기준.

    명세의 SQL 은 `EXTRACT(HOUR FROM completed_at AT TIME ZONE 'Asia/Seoul')` 인데
    SQLite 에는 타임존 변환이 없다. 기간이 길어야 31일이라 행을 가져와 파이썬에서 센다.
    DB 에 따라 답이 달라지지 않는다는 이점도 있다.
    """
    rows = await db.scalars(
        select(Todo.completed_at).where(
            Todo.user_id == user_id,
            Todo.deleted_at.is_(None),
            Todo.status == "done",
            Todo.completed_at.is_not(None),
            Todo.date >= first,
            Todo.date <= last,
        )
    )
    counts: dict[int, int] = {}
    for value in rows:
        if value is None:
            continue
        # naive 는 UTC 로 본다. core/time.to_kst_iso() 와 같은 규칙이다.
        moment = value if value.tzinfo else value.replace(tzinfo=UTC)
        hour = moment.astimezone(KST).hour
        counts[hour] = counts.get(hour, 0) + 1
    return [{"hour": h, "completed": counts[h]} for h in sorted(counts)]


MOST_DEFERRED_LIMIT = 5


async def _most_deferred(db: AsyncSession, user_id: int, first: date) -> list[dict[str, Any]]:
    """가장 많이 미룬 항목 (pending A-6 의 (a)안 `postpone_count` 기반).

    목표가 붙은 할 일은 목표 단위로 합산하고(제목은 목표 제목), 목표가 없는 할 일은
    그 할 일 자체를 `goal_id: null` 로 내려보낸다. 목표 있는 것만 세면 목표를 안 쓰는
    사용자에게는 이 목록이 늘 비어 쓸모가 없다.

    기간의 **끝은 보지 않는다.** 미루면 날짜가 앞으로 가므로 상한을 두면 정작 계속
    미루고 있는 항목이 창 밖으로 빠져나가 목록이 항상 비게 된다.
    """
    rows = await db.execute(
        select(Todo.goal_id, Todo.title, Todo.postpone_count, Goal.title)
        .outerjoin(Goal, Goal.goal_id == Todo.goal_id)
        .where(
            Todo.user_id == user_id,
            Todo.deleted_at.is_(None),
            Todo.postpone_count > 0,
            Todo.date >= first,
        )
    )

    by_goal: dict[int, dict[str, Any]] = {}
    loose: list[dict[str, Any]] = []
    for goal_id, todo_title, count, goal_title in rows:
        if goal_id is None:
            loose.append({"goal_id": None, "title": todo_title, "defer_count": int(count)})
            continue
        entry = by_goal.setdefault(
            goal_id, {"goal_id": goal_id, "title": goal_title or todo_title, "defer_count": 0}
        )
        entry["defer_count"] += int(count)

    merged = [*by_goal.values(), *loose]
    merged.sort(key=lambda e: (-e["defer_count"], str(e["title"])))
    return merged[:MOST_DEFERRED_LIMIT]


# ---- 목표별 추이 ---------------------------------------------------------------------


async def goal_stats(db: AsyncSession, user: User, goal_id: int, period: str) -> dict[str, Any]:
    """`GET /stats/goals/{goal_id}` — 개별 목표의 기간별 달성 추이 (§7)."""
    goal = await db.scalar(
        select(Goal).where(
            Goal.goal_id == goal_id, Goal.user_id == user.user_id, Goal.deleted_at.is_(None)
        )
    )
    if goal is None:
        raise GoalNotFound()

    today = service_today()
    first, last = period_range(period, today)

    rows = await db.execute(
        select(
            Todo.date,
            func.count(Todo.todo_id),
            func.count(Todo.todo_id).filter(Todo.status == "done"),
        )
        .where(
            Todo.goal_id == goal.goal_id,
            Todo.deleted_at.is_(None),
            Todo.date >= first,
            Todo.date <= last,
        )
        .group_by(Todo.date)
    )
    by_date = {row[0]: (int(row[1]), int(row[2])) for row in rows}

    by_day = []
    for i in range((last - first).days + 1):
        day = first + timedelta(days=i)
        total, done = by_date.get(day, (0, 0))
        by_day.append(
            {
                "date": day.isoformat(),
                "total": total,
                "done": done,
                "achievement_rate": round(done / total, 2) if total else 0.0,
            }
        )

    # 기간과 무관한 전체 누계. 목표 화면의 "n/N 회" 와 같은 값이어야 한다.
    lifetime = (
        await db.execute(
            select(
                func.count(Todo.todo_id),
                func.count(Todo.todo_id).filter(Todo.status == "done"),
                func.coalesce(func.sum(Todo.actual_minutes).filter(Todo.status == "done"), 0),
            ).where(Todo.goal_id == goal.goal_id, Todo.deleted_at.is_(None))
        )
    ).one()
    total_all, done_all, minutes_all = (int(v or 0) for v in lifetime)

    week_first, week_last = week_range(today)
    week_done = await db.scalar(
        select(func.count(Todo.todo_id)).where(
            Todo.goal_id == goal.goal_id,
            Todo.deleted_at.is_(None),
            Todo.status == "done",
            Todo.date >= week_first,
            Todo.date <= week_last,
        )
    )

    return {
        "goal_id": goal.goal_id,
        "title": goal.title,
        "color": goal.color,
        "status": goal.status,
        "period": period,
        "range": {"from": first.isoformat(), "to": last.isoformat()},
        "total_todos": total_all,
        "completed_todos": done_all,
        "achievement_rate": round(done_all / total_all, 2) if total_all else 0.0,
        "total_actual_minutes": minutes_all,
        "current_week_done": int(week_done or 0),
        "by_day": by_day,
    }


# ---- 백필 -------------------------------------------------------------------------


async def backfill(db: AsyncSession, user_id: int, first: date, last: date) -> int:
    """구간 전체를 다시 계산한다. 배치와, 집계표가 없던 시절의 데이터 복구에 쓴다."""
    changed = 0
    for i in range((last - first).days + 1):
        await recalc_day(db, user_id, first + timedelta(days=i))
        changed += 1
    return changed


async def purge_empty_rows(db: AsyncSession, user_id: int) -> None:
    """total_count 가 0 인 행은 남기지 않는다 (recalc_day 주석 참고)."""
    await db.execute(
        delete(UserDailyStat).where(
            UserDailyStat.user_id == user_id, UserDailyStat.total_count == 0
        )
    )
