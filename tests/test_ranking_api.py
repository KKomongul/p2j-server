"""/groups/{id}/ranking · /streak 와 04:10 배치 (명세 §6.5, ERD §6.3, 04-backend §5.9)."""

from datetime import date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scheduler import next_run_at
from app.core.time import KST, now_utc, service_today, week_range
from app.db.models import Declaration, DeclarationItem, Group, RefreshToken, Todo, User
from app.services import batch as batch_svc


async def _group_with(client: AsyncClient, owner: dict[str, str], *others: dict[str, str]) -> dict:
    group = (
        await client.post("/v1/groups", json={"name": "3학년 개발조"}, headers=owner)
    ).json()["data"]
    for headers in others:
        await client.post(
            "/v1/groups/join", json={"invite_code": group["invite_code"]}, headers=headers
        )
    return group


async def _declare_and_finish(
    client: AsyncClient, headers: dict[str, str], group_id: int, titles: list[str], done: int
) -> None:
    todo_ids = []
    for title in titles:
        r = await client.post("/v1/todos", json={"title": title}, headers=headers)
        todo_ids.append(r.json()["data"]["todo_id"])
    await client.post(
        f"/v1/groups/{group_id}/declarations", json={"todo_ids": todo_ids}, headers=headers
    )
    for todo_id in todo_ids[:done]:
        await client.post(f"/v1/todos/{todo_id}/complete", json={}, headers=headers)


async def _seed_past_declaration(
    db: AsyncSession, group_id: int, user: User, day: date, titles: list[str], done: int
) -> Declaration:
    """지난 날짜의 선언을 직접 만든다. API 는 과거 선언을 막으므로(§6.2) 배치 검증용."""
    declaration = Declaration(
        group_id=group_id, user_id=user.user_id, date=day, locked_at=now_utc()
    )
    db.add(declaration)
    await db.flush()
    for i, title in enumerate(titles):
        todo = Todo(
            user_id=user.user_id,
            title=title,
            date=day,
            status="done" if i < done else "pending",
            completed_at=now_utc() if i < done else None,
            declared_at=declaration.locked_at,
            display_order=i + 1,
        )
        db.add(todo)
        await db.flush()
        db.add(
            DeclarationItem(
                declaration_id=declaration.declaration_id,
                todo_id=todo.todo_id,
                title_snapshot=title,
            )
        )
    await db.commit()
    return declaration


# ---- 랭킹 -------------------------------------------------------------------------


async def test_ranking_uses_calendar_week(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    """랭킹은 여럿이 같은 표를 보므로 경계가 사람마다 달라지면 안 된다 (달력 주)."""
    _, jiho = await make_user("지호")
    group = await _group_with(client, auth_headers, jiho)

    r = await client.get(f"/v1/groups/{group['group_id']}/ranking", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    monday, sunday = week_range(service_today())
    assert data["range"] == {"from": monday.isoformat(), "to": sunday.isoformat()}


async def test_ranking_orders_by_achievement(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    jiho_user, jiho = await make_user("지호")
    youngjun_user, youngjun = await make_user("영준")
    group = await _group_with(client, auth_headers, jiho, youngjun)

    await _declare_and_finish(client, jiho, group["group_id"], ["A", "B"], done=2)  # 100%
    await _declare_and_finish(client, auth_headers, group["group_id"], ["C", "D"], done=1)  # 50%
    # 영준은 선언조차 하지 않았다 → 0% 로 목록에 남는다 (명세 §9 미결 5번의 결정)

    data = (
        await client.get(f"/v1/groups/{group['group_id']}/ranking", headers=auth_headers)
    ).json()["data"]
    rankings = data["rankings"]
    assert [r["nickname"] for r in rankings] == ["지호", "태한", "영준"]
    assert [r["rank"] for r in rankings] == [1, 2, 3]
    assert rankings[0]["achievement_rate"] == 1.0
    assert rankings[0]["declared_days"] == 1
    assert rankings[2]["achievement_rate"] == 0.0
    assert rankings[2]["declared_days"] == 0
    assert data["my_rank"] == 2
    assert {jiho_user.user_id, youngjun_user.user_id} <= {r["user_id"] for r in rankings}


async def test_ranking_ties_share_a_rank(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    _, jiho = await make_user("지호")
    _, youngjun = await make_user("영준")
    group = await _group_with(client, auth_headers, jiho, youngjun)

    await _declare_and_finish(client, jiho, group["group_id"], ["A"], done=1)
    await _declare_and_finish(client, auth_headers, group["group_id"], ["B"], done=1)

    rankings = (
        await client.get(f"/v1/groups/{group['group_id']}/ranking", headers=auth_headers)
    ).json()["data"]["rankings"]
    assert [r["rank"] for r in rankings] == [1, 1, 3]  # 동점은 같은 순위, 다음은 건너뛴다


async def test_ranking_period_all(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    group = await _group_with(client, auth_headers)
    r = await client.get(
        f"/v1/groups/{group['group_id']}/ranking?period=all", headers=auth_headers
    )
    assert r.status_code == 200
    assert r.json()["data"]["period"] == "all"

    bad = await client.get(
        f"/v1/groups/{group['group_id']}/ranking?period=decade", headers=auth_headers
    )
    assert bad.status_code == 400


# ---- 그룹 스트릭 --------------------------------------------------------------------


async def test_streak_today_status(
    client: AsyncClient, auth_headers: dict[str, str], make_user
) -> None:
    _, jiho = await make_user("지호")
    group = await _group_with(client, auth_headers, jiho)
    url = f"/v1/groups/{group['group_id']}/streak"

    empty = (await client.get(url, headers=auth_headers)).json()["data"]
    assert empty["today_status"] == "in_progress"  # 아무도 선언하지 않았다
    assert empty["members_total"] == 2
    assert empty["group_streak"] == 0

    await _declare_and_finish(client, auth_headers, group["group_id"], ["A", "B"], done=1)
    partial = (await client.get(url, headers=auth_headers)).json()["data"]
    assert partial["today_status"] == "in_progress"
    assert partial["declared_members_today"] == 1
    assert partial["members_completed_today"] == 0

    todos = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]["items"]
    rest = next(t["todo_id"] for t in todos if t["status"] == "pending")
    await client.post(f"/v1/todos/{rest}/complete", json={}, headers=auth_headers)

    done = (await client.get(url, headers=auth_headers)).json()["data"]
    # 선언한 사람 전원이 100%. 미선언 멤버는 판정에서 빠지되 members_total 에는 남는다.
    assert done["today_status"] == "success"
    assert done["members_completed_today"] == 1
    assert done["members_total"] == 2


async def test_streak_breaks_when_a_declared_item_is_postponed(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    group = await _group_with(client, auth_headers)
    await _declare_and_finish(client, auth_headers, group["group_id"], ["A", "B"], done=1)

    todos = (await client.get("/v1/todos", headers=auth_headers)).json()["data"]["items"]
    rest = next(t["todo_id"] for t in todos if t["status"] == "pending")
    await client.post(
        f"/v1/todos/{rest}/postpone",
        json={"to_date": (service_today() + timedelta(days=1)).isoformat()},
        headers=auth_headers,
    )

    data = (
        await client.get(f"/v1/groups/{group['group_id']}/streak", headers=auth_headers)
    ).json()["data"]
    assert data["today_status"] == "broken"  # 오늘 안에 되돌릴 수 없다


# ---- 배치 -------------------------------------------------------------------------


async def test_batch_raises_streak_on_a_fully_kept_day(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    group_data = await _group_with(client, auth_headers)
    yesterday = service_today() - timedelta(days=1)
    await _seed_past_declaration(
        db, group_data["group_id"], user, yesterday, ["A", "B"], done=2
    )

    report = await batch_svc.run_daily(db, yesterday)
    assert report.target_date == yesterday
    assert (report.groups_evaluated, report.groups_succeeded) == (1, 1)
    assert report.failures == []

    db.expunge_all()
    group = await db.get(Group, group_data["group_id"])
    assert group.group_streak == 1
    assert group.last_streak_date == yesterday

    # 같은 날을 다시 돌려도 값이 튀지 않는다 (멱등)
    again = await batch_svc.run_daily(db, yesterday)
    assert again.groups_succeeded == 0
    db.expunge_all()
    group = await db.get(Group, group_data["group_id"])
    assert group.group_streak == 1


async def test_batch_breaks_streak_on_a_missed_day(
    client: AsyncClient, auth_headers: dict[str, str], db: AsyncSession, user: User
) -> None:
    group_data = await _group_with(client, auth_headers)
    today = service_today()
    gid = group_data["group_id"]
    await _seed_past_declaration(db, gid, user, today - timedelta(days=2), ["A"], 1)
    await _seed_past_declaration(db, gid, user, today - timedelta(days=1), ["B"], 0)

    await batch_svc.run_daily(db, today - timedelta(days=2))
    db.expunge_all()
    assert (await db.get(Group, group_data["group_id"])).group_streak == 1

    await batch_svc.run_daily(db, today - timedelta(days=1))
    db.expunge_all()
    group = await db.get(Group, group_data["group_id"])
    assert group.group_streak == 0  # 지키지 못한 날에 끊긴다
    assert group.last_streak_date == today - timedelta(days=2)


async def test_batch_closes_daily_stats_and_purges_tokens(
    db: AsyncSession, user: User
) -> None:
    yesterday = service_today() - timedelta(days=1)
    db.add(Todo(user_id=user.user_id, title="어제 한 일", date=yesterday, display_order=1))
    db.add(
        RefreshToken(
            user_id=user.user_id,
            token_hash="a" * 64,
            expires_at=now_utc() - timedelta(days=batch_svc.TOKEN_RETENTION_DAYS + 1),
        )
    )
    db.add(
        RefreshToken(
            user_id=user.user_id,
            token_hash="b" * 64,
            expires_at=now_utc() + timedelta(days=1),
        )
    )
    await db.commit()

    report = await batch_svc.run_daily(db, yesterday)
    assert report.users_recalculated == 1
    assert report.tokens_deleted == 1  # 만료 7일 지난 것만
    assert report.failures == []


async def test_batch_report_serializes(db: AsyncSession, user: User) -> None:
    report = await batch_svc.run_daily(db)
    body = report.to_dict()
    assert set(body) == {
        "target_date",
        "users_recalculated",
        "groups_evaluated",
        "groups_succeeded",
        "tokens_deleted",
        "failures",
    }
    # 기본 대상은 전일이다 (04:00 에 하루가 바뀌므로)
    assert body["target_date"] == (service_today() - timedelta(days=1)).isoformat()


# ---- 스케줄러 ---------------------------------------------------------------------


def test_next_run_is_computed_in_kst() -> None:
    """Railway 컨테이너는 UTC 다. 서버 로컬 시계를 믿으면 04:10 이 13:10 이 된다."""
    before = datetime(2026, 9, 16, 1, 0, tzinfo=KST)
    assert next_run_at(before, 4, 10) == datetime(2026, 9, 16, 4, 10, tzinfo=KST)

    after = datetime(2026, 9, 16, 5, 0, tzinfo=KST)
    assert next_run_at(after, 4, 10) == datetime(2026, 9, 17, 4, 10, tzinfo=KST)

    # UTC 로 들어와도 같은 답이 나와야 한다 (2026-09-16 01:00 KST == 전날 16:00 UTC)
    from datetime import UTC

    assert next_run_at(datetime(2026, 9, 15, 16, 0, tzinfo=UTC), 4, 10) == datetime(
        2026, 9, 16, 4, 10, tzinfo=KST
    )
