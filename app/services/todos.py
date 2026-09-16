"""TODO 비즈니스 로직 (API 명세 §5).

소유권: 다른 사용자의 todo 는 존재를 숨기고 404 TODO_NOT_FOUND (§1.6).
날짜: 모든 "오늘"은 service_today(). 시각은 now_utc().
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    AppError,
    DeclaredTodoLocked,
    FieldValidationError,
    GoalNotFound,
    TodoNotFound,
)
from app.core.time import now_utc, service_today
from app.db.models.goal import Goal
from app.db.models.todo import Todo
from app.db.models.user import User
from app.schemas.todo import (
    TodoBulkRequest,
    TodoCompleteRequest,
    TodoCreateRequest,
    TodoPostponeRequest,
    TodoUpdateRequest,
    summary_dict,
    todo_to_dict,
)
from app.services.goals import goal_progress
from app.services.stats import current_streak, recalc_day, recalc_days

DATE_PAST_LIMIT_DAYS = 30
DATE_FUTURE_LIMIT_DAYS = 365


# ---- 조회 -------------------------------------------------------------------------


def _alive(user: User):  # type: ignore[no-untyped-def]
    return select(Todo).where(Todo.user_id == user.user_id, Todo.deleted_at.is_(None))


async def get_owned_todo(db: AsyncSession, user: User, todo_id: int) -> Todo:
    todo = await db.scalar(_alive(user).where(Todo.todo_id == todo_id))
    if todo is None:
        raise TodoNotFound()
    return todo


async def list_day(db: AsyncSession, user: User, day: date | None) -> dict[str, Any]:
    target = day or service_today()
    rows = (
        await db.scalars(
            _alive(user).where(Todo.date == target).order_by(Todo.display_order, Todo.todo_id)
        )
    ).all()
    todos = list(rows)
    return {
        "date": target.isoformat(),
        "items": [todo_to_dict(t) for t in todos],
        "summary": summary_dict(todos),
        # 그날 내가 어느 그룹에 뭘 선언했는지. 선언이 없으면 null (§6.2).
        "declaration": await declaration_summary(db, user, target),
    }


async def declaration_summary(db: AsyncSession, user: User, day: date) -> dict[str, Any] | None:
    # 선언 기능이 todos 를 import 하지 않도록 호출 시점에 가져온다.
    from app.services.declarations import day_summary

    return await day_summary(db, user, day)


async def week_strip(db: AsyncSession, user: User, start: date | None) -> list[dict[str, Any]]:
    """7일 달성률. 지금은 전부 실시간 집계. user_daily_stats 가 생기면 과거 일자는 거기서 읽는다."""
    first = start or (service_today() - timedelta(days=6))
    last = first + timedelta(days=6)
    rows = await db.execute(
        select(
            Todo.date,
            func.count(Todo.todo_id),
            func.count(Todo.todo_id).filter(Todo.status == "done"),
        )
        .where(
            Todo.user_id == user.user_id,
            Todo.deleted_at.is_(None),
            Todo.date >= first,
            Todo.date <= last,
        )
        .group_by(Todo.date)
    )
    by_date = {row[0]: (row[1], row[2]) for row in rows}
    out = []
    for i in range(7):
        day = first + timedelta(days=i)
        total, done = by_date.get(day, (0, 0))
        out.append(
            {
                "date": day.isoformat(),
                "total": total,
                "done": done,
                "achievement_rate": round(done / total, 2) if total else 0.0,
            }
        )
    return out


# ---- 생성·수정·삭제 --------------------------------------------------------------------


def _check_date_range(day: date, today: date) -> None:
    if not (
        today - timedelta(days=DATE_PAST_LIMIT_DAYS)
        <= day
        <= today + timedelta(days=DATE_FUTURE_LIMIT_DAYS)
    ):
        raise FieldValidationError({"date": "30일 전부터 1년 뒤까지의 날짜만 고를 수 있어요."})


async def _resolve_goal(db: AsyncSession, user: User, goal_id: int | None) -> Goal | None:
    if goal_id is None:
        return None
    goal = await db.scalar(
        select(Goal).where(
            Goal.goal_id == goal_id, Goal.user_id == user.user_id, Goal.deleted_at.is_(None)
        )
    )
    if goal is None:
        raise GoalNotFound()
    if goal.status == "archived":
        raise FieldValidationError({"goal_id": "보관한 목표에는 할 일을 붙일 수 없어요."})
    return goal


async def _next_order(db: AsyncSession, user: User, day: date) -> int:
    current = await db.scalar(
        select(func.max(Todo.display_order)).where(
            Todo.user_id == user.user_id, Todo.date == day, Todo.deleted_at.is_(None)
        )
    )
    return (current or 0) + 1


async def create_todo(db: AsyncSession, user: User, body: TodoCreateRequest) -> Todo:
    today = service_today()
    day = body.date or today
    _check_date_range(day, today)
    goal = await _resolve_goal(db, user, body.goal_id)

    todo = Todo(
        user_id=user.user_id,
        goal_id=goal.goal_id if goal else None,
        title=body.title,
        date=day,
        source="manual",
        estimated_minutes=body.estimated_minutes,
        memo=body.memo,
        display_order=await _next_order(db, user, day),
    )
    db.add(todo)
    await db.flush()
    await db.refresh(todo)
    await recalc_day(db, user.user_id, day)
    return todo


async def create_bulk(db: AsyncSession, user: User, body: TodoBulkRequest) -> list[Todo]:
    """§5 /todos/bulk. 항목 검증은 단건과 같고, 실패 시 details 키는 items[i].field.

    같은 세션에서 예외가 나면 get_db 가 rollback 하므로 부분 저장이 남지 않는다.
    """
    today = service_today()
    goal_cache: dict[int, Goal] = {}
    todos: list[Todo] = []
    next_order: dict[date, int] = {}

    for i, item in enumerate(body.items):
        day = item.date or today
        try:
            _check_date_range(day, today)
            goal = None
            if item.goal_id is not None:
                goal = goal_cache.get(item.goal_id) or await _resolve_goal(db, user, item.goal_id)
                goal_cache[item.goal_id] = goal  # type: ignore[assignment]
        except FieldValidationError as exc:
            raise FieldValidationError(
                {f"items[{i}].{k}": v for k, v in exc.details.items()}
            ) from exc
        except GoalNotFound as exc:
            raise FieldValidationError({f"items[{i}].goal_id": "목표를 찾을 수 없어요."}) from exc

        if day not in next_order:
            next_order[day] = await _next_order(db, user, day)
        todos.append(
            Todo(
                user_id=user.user_id,
                goal_id=goal.goal_id if goal else None,
                title=item.title,
                date=day,
                source=body.source,
                estimated_minutes=item.estimated_minutes,
                memo=item.memo,
                display_order=next_order[day],
            )
        )
        next_order[day] += 1

    db.add_all(todos)
    await db.flush()
    for t in todos:
        await db.refresh(t)
    await recalc_days(db, user.user_id, *{t.date for t in todos})
    return todos


LOCKED_FIELDS = {"title", "date", "goal_id"}


async def update_todo(db: AsyncSession, user: User, todo_id: int, body: TodoUpdateRequest) -> Todo:
    todo = await get_owned_todo(db, user, todo_id)
    changed = body.model_fields_set
    previous_date = todo.date  # 날짜가 바뀌면 양쪽 날의 집계를 다시 계산한다

    if todo.is_declared and changed & LOCKED_FIELDS:
        raise DeclaredTodoLocked()

    if "date" in changed and body.date is not None and body.date != todo.date:
        if todo.status == "done":
            raise AppError("TODO_ALREADY_DONE", "완료한 할 일은 날짜를 바꿀 수 없어요.")
        _check_date_range(body.date, service_today())
        if body.date > todo.date:
            todo.postpone_count += 1  # BR-03: 직접 날짜 편집도 미루기로 센다
        # autoflush 때문에 날짜를 바꾸기 전에 순서를 구해야 자기 자신을 세지 않는다
        todo.display_order = await _next_order(db, user, body.date)
        todo.date = body.date

    if "goal_id" in changed:
        goal = await _resolve_goal(db, user, body.goal_id)
        todo.goal_id = goal.goal_id if goal else None
    if "title" in changed and body.title is not None:
        todo.title = body.title
    if "estimated_minutes" in changed:
        todo.estimated_minutes = body.estimated_minutes
    if "order" in changed and body.order is not None:
        todo.display_order = body.order
    if "memo" in changed:
        todo.memo = body.memo

    await db.flush()
    await db.refresh(todo)
    await recalc_days(db, user.user_id, previous_date, todo.date)
    return todo


async def delete_todo(db: AsyncSession, user: User, todo_id: int) -> None:
    todo = await get_owned_todo(db, user, todo_id)
    if todo.is_declared:
        raise DeclaredTodoLocked()
    todo.deleted_at = now_utc()
    await db.flush()
    await recalc_day(db, user.user_id, todo.date)


# ---- 상태 -------------------------------------------------------------------------


async def complete_todo(
    db: AsyncSession, user: User, todo_id: int, body: TodoCompleteRequest
) -> dict[str, Any]:
    todo = await get_owned_todo(db, user, todo_id)
    if todo.status != "done":  # 이미 done 이면 멱등 — completed_at 을 갱신하지 않는다
        todo.status = "done"
        todo.completed_at = now_utc()
        todo.actual_minutes = body.actual_minutes or todo.estimated_minutes
        await db.flush()
        await db.refresh(todo)
        # 집계와 연속 기록을 같은 트랜잭션에서 갱신한다. 여기를 빠뜨리면
        # 통계·랭킹·계획량 안내가 전부 어긋난다 (04-backend-v1 §5.3).
        await recalc_day(db, user.user_id, todo.date)

    return {
        "todo": todo_to_dict(todo),
        "goal_progress": await goal_progress(db, todo.goal) if todo.goal else None,
        "personal_streak": await current_streak(db, user.user_id),
    }


async def uncomplete_todo(db: AsyncSession, user: User, todo_id: int) -> None:
    todo = await get_owned_todo(db, user, todo_id)
    todo.status = "pending"
    todo.completed_at = None
    todo.actual_minutes = None
    await db.flush()
    await recalc_day(db, user.user_id, todo.date)


async def postpone_todo(
    db: AsyncSession, user: User, todo_id: int, body: TodoPostponeRequest
) -> Todo:
    todo = await get_owned_todo(db, user, todo_id)
    if todo.status == "done":
        raise AppError("TODO_ALREADY_DONE", "완료한 할 일은 미룰 수 없어요.")
    target = body.to_date or (todo.date + timedelta(days=1))
    if target <= todo.date:
        raise FieldValidationError({"to_date": "지금 날짜보다 뒤로만 미룰 수 있어요."})
    _check_date_range(target, service_today())

    previous_date = todo.date
    todo.display_order = await _next_order(db, user, target)  # 날짜 변경 전에 (autoflush)
    todo.date = target
    todo.postpone_count += 1
    # 선언된 항목을 미루면 선언에서 빠진다. 스냅샷 상태 갱신·피드 이벤트는 12주차에.
    todo.declared_at = None
    await db.flush()
    await db.refresh(todo)
    await recalc_days(db, user.user_id, previous_date, target)
    return todo
