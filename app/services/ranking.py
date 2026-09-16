"""랭킹과 그룹 스트릭 (API 명세 §6.5, ERD §6.3).

**그룹 스트릭 규칙** — 선언한 구성원 전원이 선언 항목을 100% 끝낸 날만 +1.
미선언 구성원은 판정에서 빼되 `members_total` 에는 넣어 화면에 보이게 한다.
아무도 선언하지 않은 날은 성공이 아니다 (0명이 전원 달성한 것으로 치지 않는다).

**미선언 구성원의 랭킹 처리** (명세 §9 미결 5번) — 목록에 넣고 0% 로 집계한다.
빼 버리면 선언을 안 할수록 순위표에서 사라져 유리해진다. 이 앱에서 안 하는 것은
숨겨지는 게 아니라 드러나야 한다.

기간 규칙이 `/stats/summary` 와 다르다. 명세 예시가 여기서는 달력 주(월~일)이고
통계에서는 "오늘로 끝나는 최근 7일" 이라 양쪽을 예시 그대로 따랐다.
랭킹은 여럿이 같은 표를 보는 화면이라 경계가 사람마다 달라지면 안 된다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import service_today, week_range
from app.db.models.declaration import Declaration, DeclarationItem
from app.db.models.group import Group, GroupMember
from app.db.models.todo import Todo
from app.db.models.user import User
from app.services import groups as group_svc
from app.services.declarations import item_status

PERIODS = ("week", "month", "all")


def ranking_range(period: str, today: date, group_start: date | None = None) -> tuple[date, date]:
    """달력 기준. week = 이번 주 월~일, month = 이번 달 1일~말일, all = 그룹 개설일~오늘."""
    if period == "month":
        first = today.replace(day=1)
        next_month = (first + timedelta(days=32)).replace(day=1)
        return first, next_month - timedelta(days=1)
    if period == "all":
        return group_start or today, today
    return week_range(today)


# ---- 선언 집계 ---------------------------------------------------------------------


async def _declaration_stats(
    db: AsyncSession, group_id: int, first: date, last: date
) -> dict[int, dict[str, Any]]:
    """{user_id: {declared_days, total_items, done_items}} — 기간 안의 선언 이행 현황."""
    rows = (
        await db.execute(
            select(
                Declaration.user_id,
                Declaration.date,
                DeclarationItem.declaration_item_id,
                DeclarationItem.todo_id,
                DeclarationItem.title_snapshot,
                Todo.status,
                Todo.date,
                Todo.deleted_at,
            )
            .join(
                DeclarationItem,
                DeclarationItem.declaration_id == Declaration.declaration_id,
            )
            .outerjoin(Todo, Todo.todo_id == DeclarationItem.todo_id)
            .where(
                Declaration.group_id == group_id,
                Declaration.date >= first,
                Declaration.date <= last,
            )
        )
    ).all()

    out: dict[int, dict[str, Any]] = {}
    for user_id, decl_date, _item_id, todo_id, _title, todo_status, todo_date, deleted_at in rows:
        entry = out.setdefault(
            int(user_id), {"days": set(), "total_items": 0, "done_items": 0}
        )
        entry["days"].add(decl_date)
        entry["total_items"] += 1
        # item_status() 와 같은 판정을 컬럼 값만으로 되풀이한다 (행을 통째로 들고 오지 않기 위해).
        done = (
            todo_id is not None
            and deleted_at is None
            and todo_date == decl_date
            and todo_status == "done"
        )
        if done:
            entry["done_items"] += 1
    return out


async def group_ranking(
    db: AsyncSession, viewer: User, group: Group, period: str
) -> dict[str, Any]:
    today = service_today()
    opened_on = group.created_at.date() if group.created_at else None
    first, last = ranking_range(period, today, opened_on)

    members = (
        await db.execute(
            select(GroupMember, User)
            .join(User, User.user_id == GroupMember.user_id)
            .where(GroupMember.group_id == group.group_id, GroupMember.left_at.is_(None))
            .order_by(GroupMember.joined_at, GroupMember.id)
        )
    ).all()
    if not members:
        return {
            "period": period,
            "range": {"from": first.isoformat(), "to": last.isoformat()},
            "rankings": [],
            "my_rank": None,
        }

    user_ids = [m.user_id for m, _ in members]
    stats = await _declaration_stats(db, group.group_id, first, last)
    streaks = await group_svc.member_streaks(db, user_ids, today)

    entries = []
    for member, account in members:
        stat = stats.get(member.user_id)
        total = stat["total_items"] if stat else 0
        done = stat["done_items"] if stat else 0
        entries.append(
            {
                "user_id": account.user_id,
                "nickname": account.nickname,
                "profile_image_url": account.profile_image_url,
                "achievement_rate": round(done / total, 2) if total else 0.0,
                "declared_days": len(stat["days"]) if stat else 0,
                "declared_items": total,
                "completed_items": done,
                "streak": streaks.get(member.user_id, 0),
            }
        )

    # 달성률 → 선언한 날 수 → 가입 순. 아무것도 안 한 사람이 위로 올라가지 않도록.
    entries.sort(key=lambda e: (-e["achievement_rate"], -e["declared_days"], e["user_id"]))

    rankings = []
    previous: tuple[float, int] | None = None
    rank = 0
    for index, entry in enumerate(entries, start=1):
        key = (entry["achievement_rate"], entry["declared_days"])
        if key != previous:  # 동점은 같은 순위, 다음 순위는 건너뛴다 (1, 2, 2, 4)
            rank = index
            previous = key
        rankings.append({"rank": rank, **entry})

    my_rank = next((r["rank"] for r in rankings if r["user_id"] == viewer.user_id), None)
    return {
        "period": period,
        "range": {"from": first.isoformat(), "to": last.isoformat()},
        "rankings": rankings,
        "my_rank": my_rank,
    }


# ---- 그룹 스트릭 --------------------------------------------------------------------


async def _day_outcome(db: AsyncSession, group_id: int, day: date) -> dict[str, Any]:
    """그 날의 선언 이행 결과. 배치 판정과 조회가 같은 함수를 쓴다."""
    declarations = list(
        await db.scalars(
            select(Declaration).where(Declaration.group_id == group_id, Declaration.date == day)
        )
    )
    todo_ids = [i.todo_id for d in declarations for i in d.items if i.todo_id is not None]
    todos = (
        {t.todo_id: t for t in await db.scalars(select(Todo).where(Todo.todo_id.in_(todo_ids)))}
        if todo_ids
        else {}
    )

    completed = 0
    unrecoverable = False
    for declaration in declarations:
        statuses = [
            item_status(item, todos.get(item.todo_id) if item.todo_id else None, day)
            for item in declaration.items
        ]
        if statuses and all(s == "done" for s in statuses):
            completed += 1
        # 미루거나 버린 항목은 그날 안에 되돌릴 수 없다.
        if any(s in ("deferred", "skipped") for s in statuses):
            unrecoverable = True

    return {
        "declared_members": len(declarations),
        "members_completed": completed,
        "unrecoverable": unrecoverable,
        # 선언자가 0명인 날은 성공이 아니다. 아무도 약속하지 않은 날을 달성으로 칠 수 없다.
        "success": bool(declarations) and completed == len(declarations),
    }


async def group_streak(db: AsyncSession, group: Group) -> dict[str, Any]:
    today = service_today()
    outcome = await _day_outcome(db, group.group_id, today)
    members_total = await db.scalar(
        select(func.count(GroupMember.id)).where(
            GroupMember.group_id == group.group_id, GroupMember.left_at.is_(None)
        )
    )

    if outcome["success"]:
        status = "success"
    elif outcome["unrecoverable"]:
        status = "broken"
    else:
        status = "in_progress"  # 아직 오늘이 끝나지 않았다

    return {
        "group_streak": group.group_streak,
        "last_success_date": group.last_streak_date.isoformat()
        if group.last_streak_date
        else None,
        "today_status": status,
        "declared_members_today": outcome["declared_members"],
        "members_completed_today": outcome["members_completed"],
        "members_total": int(members_total or 0),
    }


async def evaluate_streak(db: AsyncSession, group: Group, day: date) -> bool:
    """배치가 전일을 확정한다 (ERD §6.3). 성공이면 +1, 아니면 0 으로 끊는다.

    같은 날을 두 번 판정해도 값이 튀지 않도록 `last_streak_date` 로 멱등성을 지킨다.
    """
    if group.last_streak_date is not None and group.last_streak_date >= day:
        return False

    outcome = await _day_outcome(db, group.group_id, day)
    if outcome["success"]:
        # 어제 성공했어야 이어진다. 하루라도 건너뛰었으면 1 부터 다시.
        continued = group.last_streak_date == day - timedelta(days=1)
        group.group_streak = (group.group_streak + 1) if continued else 1
        group.last_streak_date = day
    else:
        group.group_streak = 0
    await db.flush()
    return outcome["success"]
