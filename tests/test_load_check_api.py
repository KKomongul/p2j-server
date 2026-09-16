"""/ai/load-check — 계획량 안내 (명세 §4, ERD §6.2)."""

from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import now_utc, service_today
from app.db.models import Todo, User
from app.services import stats as stats_svc
from app.services.load_check import MIN_RECORD_DAYS


async def _history(db: AsyncSession, user: User, days: int, *, heavy_per_day: int = 1) -> None:
    """지난 `days` 일 동안 매일 무거운 일 `heavy_per_day` 개씩 끝낸 기록을 만든다."""
    today = service_today()
    for offset in range(1, days + 1):
        day = today - timedelta(days=offset)
        for i in range(heavy_per_day):
            db.add(
                Todo(
                    user_id=user.user_id,
                    title=f"{day} 무거운 일 {i}",
                    date=day,
                    status="done",
                    estimated_minutes=60,
                    actual_minutes=50,
                    completed_at=now_utc(),
                    display_order=i + 1,
                )
            )
        await db.flush()
        await stats_svc.recalc_day(db, user.user_id, day)
    await db.commit()


async def _plan_today(db: AsyncSession, user: User, count: int, minutes: int) -> list[int]:
    today = service_today()
    ids = []
    for i in range(count):
        todo = Todo(
            user_id=user.user_id,
            title=f"오늘 계획 {i}",
            date=today,
            estimated_minutes=minutes,
            display_order=i + 1,
        )
        db.add(todo)
        await db.flush()
        ids.append(todo.todo_id)
    await stats_svc.recalc_day(db, user.user_id, today)
    await db.commit()
    return ids


# ---- 근거 부족 ---------------------------------------------------------------------


async def test_thin_history_never_warns(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    """실행 기록이 7일 미만이면 경고하지 않는다 (§4). 막 시작한 사람에게 훈수를 두지 않는다."""
    await _history(db, user, MIN_RECORD_DAYS - 1)
    await _plan_today(db, user, count=5, minutes=120)

    r = await client.get("/v1/ai/load-check", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["level"] == "ok"
    assert data["message"] is None
    assert data["suggestions"] == []
    assert data["evidence"]["record_days"] == MIN_RECORD_DAYS - 1


async def test_no_history_at_all_is_ok(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    data = (await client.get("/v1/ai/load-check", headers=auth_headers)).json()["data"]
    assert data["level"] == "ok" and data["message"] is None
    assert data["check_id"].startswith("chk_")


# ---- 경고 -------------------------------------------------------------------------


async def test_warns_when_today_has_too_many_heavy_tasks(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    """평소 하루 1개씩 하던 사람이 오늘 4개를 계획하면 경고한다."""
    await _history(db, user, 10, heavy_per_day=1)
    await _plan_today(db, user, count=4, minutes=60)

    data = (await client.get("/v1/ai/load-check", headers=auth_headers)).json()["data"]
    assert data["level"] == "warning"
    assert data["message"] and "부담" in data["message"]

    evidence = data["evidence"]
    assert evidence["window_days"] == 14
    assert evidence["heavy_task_threshold_minutes"] == 60
    assert evidence["days_with_3plus_heavy"] == 0
    assert evidence["avg_completed_minutes_per_day"] == 50
    assert evidence["today_planned_minutes"] == 240
    assert evidence["today_heavy_count"] == 4

    # 평소 분량 아래로 내려갈 만큼만, 가장 늦게 추가한 것부터 제안한다
    assert 1 <= len(data["suggestions"]) <= 3
    first = data["suggestions"][0]
    assert first["action"] == "defer"
    assert first["to_date"] == (service_today() + timedelta(days=1)).isoformat()
    assert first["reason"] == "가장 늦게 추가된 항목"


async def test_no_warning_when_today_matches_habit(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    await _history(db, user, 10, heavy_per_day=3)
    await _plan_today(db, user, count=3, minutes=60)

    data = (await client.get("/v1/ai/load-check", headers=auth_headers)).json()["data"]
    # 평소에도 3개씩 하던 사람이다. 같은 양에 경고하면 기능이 잔소리가 된다.
    assert data["level"] == "ok"
    assert data["evidence"]["days_with_3plus_heavy"] == 10


async def test_declared_todos_are_not_suggested(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    """선언한 항목은 미룰 수 없으므로 제안에서도 뺀다."""
    await _history(db, user, 10, heavy_per_day=1)
    ids = await _plan_today(db, user, count=4, minutes=60)
    for todo_id in ids:
        todo = await db.get(Todo, todo_id)
        todo.declared_at = now_utc()
    await db.commit()

    data = (await client.get("/v1/ai/load-check", headers=auth_headers)).json()["data"]
    assert data["level"] == "warning"
    assert data["suggestions"] == []


# ---- 캐시와 응답 기록 ----------------------------------------------------------------


async def test_result_is_cached_for_the_day(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    """하루 한 번만 계산한다. 아침에 한 말을 점심에 바꾸지 않는다 (§4)."""
    await _history(db, user, 10, heavy_per_day=1)
    first = (await client.get("/v1/ai/load-check", headers=auth_headers)).json()["data"]
    assert first["level"] == "ok"

    await _plan_today(db, user, count=5, minutes=120)  # 뒤늦게 계획을 잔뜩 넣어도
    second = (await client.get("/v1/ai/load-check", headers=auth_headers)).json()["data"]
    assert second["check_id"] == first["check_id"]
    assert second["level"] == "ok"


async def test_response_is_recorded(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    await _history(db, user, 10, heavy_per_day=1)
    ids = await _plan_today(db, user, count=4, minutes=60)
    check = (await client.get("/v1/ai/load-check", headers=auth_headers)).json()["data"]

    r = await client.post(
        f"/v1/ai/load-check/{check['check_id']}/response",
        json={"accepted": True, "applied_todo_ids": [ids[-1], ids[-1]]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["accepted"] is True
    assert data["applied_todo_ids"] == [ids[-1]]  # 중복은 접힌다
    assert data["responded_at"] is not None


async def test_response_to_unknown_check_is_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.post(
        "/v1/ai/load-check/chk_deadbeef/response",
        json={"accepted": False},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "LOAD_CHECK_NOT_FOUND"


async def test_other_users_check_is_404(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    _, other_headers = await make_user("남")
    check = (await client.get("/v1/ai/load-check", headers=other_headers)).json()["data"]

    r = await client.post(
        f"/v1/ai/load-check/{check['check_id']}/response",
        json={"accepted": True},
        headers=auth_headers,
    )
    assert r.status_code == 404


async def test_load_check_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/v1/ai/load-check")).status_code == 401
