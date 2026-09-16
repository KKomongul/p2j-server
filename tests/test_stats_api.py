"""/stats/* 와 user_daily_stats 집계 (명세 §7, ERD §3.7)."""

from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now_utc, service_today
from app.db.models import Goal, Todo, User
from app.db.models.daily_stat import UserDailyStat
from app.services import stats as svc


async def _seed_day(
    db: AsyncSession, user: User, day: date, *, total: int, done: int, minutes: int = 30
) -> list[Todo]:
    """그날 할 일 total 개 중 done 개를 완료한 상태로 만든다."""
    todos = []
    for i in range(total):
        todo = Todo(
            user_id=user.user_id,
            title=f"{day} 항목 {i}",
            date=day,
            status="done" if i < done else "pending",
            estimated_minutes=minutes,
            actual_minutes=minutes if i < done else None,
            completed_at=now_utc() if i < done else None,
            display_order=i + 1,
        )
        db.add(todo)
        todos.append(todo)
    await db.flush()
    await svc.recalc_day(db, user.user_id, day)
    await db.commit()
    return todos


# ---- 집계 갱신 ---------------------------------------------------------------------


async def test_complete_updates_daily_stat(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    """완료가 같은 트랜잭션에서 집계를 갱신한다 (04-backend §5.3)."""
    r = await client.post(
        "/v1/todos", json={"title": "논문 읽기", "estimated_minutes": 90}, headers=auth_headers
    )
    todo_id = r.json()["data"]["todo_id"]

    stat = await db.get(UserDailyStat, (user.user_id, service_today()))
    assert stat is not None
    assert (stat.total_count, stat.done_count, stat.heavy_done_count) == (1, 0, 0)

    await client.post(
        f"/v1/todos/{todo_id}/complete", json={"actual_minutes": 80}, headers=auth_headers
    )
    await db.refresh(stat)
    assert (stat.total_count, stat.done_count) == (1, 1)
    assert stat.total_actual_minutes == 80
    assert stat.heavy_done_count == 1  # 90분 예상 → 무거운 일
    assert float(stat.achievement_rate) == 1.0


async def test_delete_and_postpone_recalculate_both_days(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    today = service_today()
    tomorrow = today + timedelta(days=1)

    first = (await client.post("/v1/todos", json={"title": "하나"}, headers=auth_headers)).json()
    second = (await client.post("/v1/todos", json={"title": "둘"}, headers=auth_headers)).json()

    await client.post(
        f"/v1/todos/{first['data']['todo_id']}/postpone",
        json={"to_date": tomorrow.isoformat()},
        headers=auth_headers,
    )
    today_stat = await db.get(UserDailyStat, (user.user_id, today))
    tomorrow_stat = await db.get(UserDailyStat, (user.user_id, tomorrow))
    await db.refresh(today_stat)
    assert today_stat.total_count == 1  # 미룬 항목이 빠졌다
    assert tomorrow_stat is not None and tomorrow_stat.total_count == 1  # 옮긴 날에 생겼다

    # 마지막 하나까지 지우면 그날 행 자체가 사라진다 ("계획 없던 날" 과 구분하지 않는다)
    await client.delete(f"/v1/todos/{second['data']['todo_id']}", headers=auth_headers)
    db.expunge_all()  # 테스트 세션의 identity map 을 비워 DB 를 다시 읽게 한다
    assert await db.get(UserDailyStat, (user.user_id, today)) is None


# ---- 연속 기록 ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cleared_offsets", "today_cleared", "expected"),
    [
        ([1, 2, 3], False, 3),  # 오늘 미완료여도 어제까지의 연속은 살아 있다
        ([1, 2, 3], True, 4),  # 오늘까지 끝내면 하루 더
        ([2, 3], False, 0),  # 어제가 비면 끊긴다
        ([], False, 0),
    ],
)
async def test_streak_matches_mobile_rule(
    db: AsyncSession, user: User, cleared_offsets: list[int], today_cleared: bool, expected: int
) -> None:
    """모바일 MockStore._streak() 과 같은 규칙이어야 한다."""
    today = service_today()
    for offset in cleared_offsets:
        await _seed_day(db, user, today - timedelta(days=offset), total=2, done=2)
    if today_cleared:
        await _seed_day(db, user, today, total=1, done=1)

    assert await svc.current_streak(db, user.user_id, today) == expected


async def test_day_with_no_todos_breaks_streak(db: AsyncSession, user: User) -> None:
    today = service_today()
    await _seed_day(db, user, today - timedelta(days=1), total=1, done=1)
    await _seed_day(db, user, today - timedelta(days=3), total=1, done=1)
    # 2일 전은 할 일 자체가 없다 → 빈칸이 아니라 단절
    assert await svc.current_streak(db, user.user_id, today) == 1
    assert await svc.longest_streak(db, user.user_id) == 1


# ---- /stats/summary ----------------------------------------------------------------


async def test_summary_week(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    today = service_today()
    await _seed_day(db, user, today, total=4, done=1, minutes=30)
    await _seed_day(db, user, today - timedelta(days=1), total=2, done=2, minutes=60)

    r = await client.get("/v1/stats/summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    assert data["period"] == "week"
    assert data["range"]["to"] == today.isoformat()
    assert data["range"]["from"] == (today - timedelta(days=6)).isoformat()
    assert data["total_todos"] == 6
    assert data["completed_todos"] == 3
    assert data["achievement_rate"] == 0.5
    assert data["total_actual_minutes"] == 30 + 120
    assert len(data["by_day"]) == 7
    assert data["by_day"][-1]["date"] == today.isoformat()
    assert data["by_day"][-2]["achievement_rate"] == 1.0
    assert data["current_streak"] == 1  # 어제 100%, 오늘은 진행 중
    assert sum(h["completed"] for h in data["by_hour"]) == 3


async def test_summary_month_is_thirty_days(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get("/v1/stats/summary?period=month", headers=auth_headers)
    data = r.json()["data"]
    assert len(data["by_day"]) == 30
    assert data["period"] == "month"


async def test_summary_rejects_unknown_period(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get("/v1/stats/summary?period=year", headers=auth_headers)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_most_deferred_counts_postpones(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = (
        await client.post("/v1/todos", json={"title": "논문 읽기"}, headers=auth_headers)
    ).json()["data"]
    today = service_today()
    for offset in (1, 2, 3):
        await client.post(
            f"/v1/todos/{created['todo_id']}/postpone",
            json={"to_date": (today + timedelta(days=offset)).isoformat()},
            headers=auth_headers,
        )

    # 미래로 밀린 항목도 잡혀야 한다. 상한을 두면 계속 미루는 항목이 창 밖으로 빠져나간다.
    data = (await client.get("/v1/stats/summary", headers=auth_headers)).json()["data"]
    entry = data["most_deferred"][0]
    assert entry["goal_id"] is None and entry["title"] == "논문 읽기"
    assert entry["defer_count"] == 3


# ---- /stats/goals/{id} -------------------------------------------------------------


async def test_goal_stats(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    goal = Goal(
        user_id=user.user_id,
        title="주 3회 운동",
        type="recurring",
        frequency_times=3,
        frequency_per="week",
        duration_weeks=4,
        start_date=service_today(),
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)

    today = service_today()
    for offset, status in ((0, "pending"), (1, "done"), (2, "done")):
        db.add(
            Todo(
                user_id=user.user_id,
                goal_id=goal.goal_id,
                title="운동",
                date=today - timedelta(days=offset),
                status=status,
                estimated_minutes=60,
                actual_minutes=55 if status == "done" else None,
                completed_at=now_utc() if status == "done" else None,
                display_order=1,
            )
        )
    await db.commit()

    r = await client.get(f"/v1/stats/goals/{goal.goal_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["goal_id"] == goal.goal_id
    assert data["title"] == "주 3회 운동"
    assert (data["total_todos"], data["completed_todos"]) == (3, 2)
    assert data["achievement_rate"] == 0.67
    assert data["total_actual_minutes"] == 110
    assert len(data["by_day"]) == 7


async def test_goal_stats_of_other_user_is_404(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession
) -> None:
    stranger = User(email="x@p2j.dev", password_hash="x", nickname="남")
    db.add(stranger)
    await db.commit()
    await db.refresh(stranger)
    goal = Goal(user_id=stranger.user_id, title="남의 목표", start_date=service_today())
    db.add(goal)
    await db.commit()
    await db.refresh(goal)

    r = await client.get(f"/v1/stats/goals/{goal.goal_id}", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "GOAL_NOT_FOUND"


async def test_stats_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/v1/stats/summary")).status_code == 401


async def test_backfill_rebuilds_rows(db: AsyncSession, user: User) -> None:
    today = service_today()
    past = today - timedelta(days=2)
    db.add(Todo(user_id=user.user_id, title="과거", date=past, display_order=1))
    await db.commit()
    assert await db.get(UserDailyStat, (user.user_id, today - timedelta(days=2))) is None

    await svc.backfill(db, user.user_id, today - timedelta(days=3), today)
    await db.commit()
    rows = list(
        await db.scalars(select(UserDailyStat).where(UserDailyStat.user_id == user.user_id))
    )
    assert len(rows) == 1 and rows[0].total_count == 1
