"""선언 (API 명세 §6.2, ERD §3.11~§3.12).

아침에 "오늘 이거 합니다" 를 그룹에 못 박는다. 이 앱의 압력 장치다.

세 가지 규칙이 기능의 전부다.

1. **하루 한 번** — UNIQUE(group, user, date) 가 DB 에서 막는다 → 409.
2. **즉시 잠긴다** — 만든 뒤에는 수정도 삭제도 없다 → 422 DECLARATION_LOCKED.
   선언을 고칠 수 있으면 선언이 아니다.
3. **오늘 것만** — 지난 날짜를 소급해 선언할 수 없다 → 422 DECLARATION_CLOSED.

선언한 할 일은 `todos.declared_at` 이 채워져 제목·날짜·삭제가 잠긴다(완료 체크는 된다).

항목 상태는 스냅샷이 아니라 **지금의 할 일** 에서 읽는다. 다만 선언 당일에서 벗어난
할 일은 `deferred` 로 본다. 미뤄서 선언에서 빠져나갔어도 "아침에 약속했다" 는 기록은
남아야 하기 때문이다 — 미달성을 숨기지 않는 게 이 앱의 차별점이다 (§6.3).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, FieldValidationError
from app.core.time import now_utc, service_today, to_kst_iso
from app.db.models.declaration import Declaration, DeclarationItem
from app.db.models.group import GroupMember
from app.db.models.proof import Proof
from app.db.models.todo import Todo
from app.db.models.user import User
from app.services import groups as group_svc

MAX_ITEMS = 20


# ---- 상태 판정 ---------------------------------------------------------------------


def item_status(item: DeclarationItem, todo: Todo | None, declared_on: date) -> str:
    """선언 항목의 현재 상태. pending | done | deferred | skipped."""
    if todo is None or todo.deleted_at is not None:
        return "skipped"  # 계정 삭제 등으로 원본이 사라진 경우
    if todo.date != declared_on:
        return "deferred"  # 선언한 날에서 벗어났다
    return todo.status


def item_to_dict(
    item: DeclarationItem,
    todo: Todo | None,
    declared_on: date,
    proof: Proof | None = None,
) -> dict[str, Any]:
    from app.services.feed import proof_to_dict  # 순환 import 회피

    return {
        "declaration_item_id": item.declaration_item_id,
        "todo_id": item.todo_id,
        "title": item.title_snapshot,
        "status": item_status(item, todo, declared_on),
        "proof": proof_to_dict(proof) if proof else None,
    }


# ---- 조회 -------------------------------------------------------------------------


async def items_context(
    db: AsyncSession, declarations: list[Declaration]
) -> tuple[dict[int, Todo], dict[int, Proof]]:
    """항목들이 가리키는 할 일과 인증샷을 한 번에 읽는다 (N+1 방지)."""
    todo_ids = [
        item.todo_id for d in declarations for item in d.items if item.todo_id is not None
    ]
    item_ids = [item.declaration_item_id for d in declarations for item in d.items]

    todos: dict[int, Todo] = {}
    if todo_ids:
        rows = await db.scalars(select(Todo).where(Todo.todo_id.in_(todo_ids)))
        todos = {t.todo_id: t for t in rows}

    proofs: dict[int, Proof] = {}
    if item_ids:
        rows2 = await db.scalars(
            select(Proof).where(
                Proof.declaration_item_id.in_(item_ids), Proof.deleted_at.is_(None)
            )
        )
        proofs = {p.declaration_item_id: p for p in rows2}
    return todos, proofs


def _rate(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    done = sum(1 for i in items if i["status"] == "done")
    return round(done / len(items), 2)


async def to_dict(db: AsyncSession, declaration: Declaration) -> dict[str, Any]:
    todos, proofs = await items_context(db, [declaration])
    items = [
        item_to_dict(
            item,
            todos.get(item.todo_id) if item.todo_id else None,
            declaration.date,
            proofs.get(item.declaration_item_id),
        )
        for item in declaration.items
    ]
    return {
        "declaration_id": declaration.declaration_id,
        "group_id": declaration.group_id,
        "user_id": declaration.user_id,
        "date": declaration.date.isoformat(),
        "locked_at": to_kst_iso(declaration.locked_at),
        "items": items,
        "achievement_rate": _rate(items),
    }


async def list_for_day(
    db: AsyncSession, group_id: int, day: date | None
) -> list[dict[str, Any]]:
    """그 날의 그룹 선언 현황 (§6.2).

    **미선언 구성원도 `declaration_id: null` 로 함께 내려간다.** 아직 선언 안 한 사람이
    보이는 것 자체가 압력 장치라서, 목록에서 빼면 기능이 반쯤 사라진다.
    """
    target = day or service_today()

    members = (
        await db.execute(
            select(GroupMember, User)
            .join(User, User.user_id == GroupMember.user_id)
            .where(GroupMember.group_id == group_id, GroupMember.left_at.is_(None))
            .order_by(GroupMember.joined_at, GroupMember.id)
        )
    ).all()

    declarations = list(
        await db.scalars(
            select(Declaration).where(
                Declaration.group_id == group_id, Declaration.date == target
            )
        )
    )
    by_user = {d.user_id: d for d in declarations}
    todos, proofs = await items_context(db, declarations)

    out: list[dict[str, Any]] = []
    for member, account in members:
        declaration = by_user.get(member.user_id)
        items: list[dict[str, Any]] = []
        if declaration is not None:
            items = [
                item_to_dict(
                    item,
                    todos.get(item.todo_id) if item.todo_id else None,
                    declaration.date,
                    proofs.get(item.declaration_item_id),
                )
                for item in declaration.items
            ]
        out.append(
            {
                "user": {
                    "user_id": account.user_id,
                    "nickname": account.nickname,
                    "profile_image_url": account.profile_image_url,
                },
                "declaration_id": declaration.declaration_id if declaration else None,
                "date": target.isoformat(),
                "locked_at": to_kst_iso(declaration.locked_at) if declaration else None,
                "items": items,
                "achievement_rate": _rate(items),
            }
        )
    return out


# ---- 생성 -------------------------------------------------------------------------


async def create(
    db: AsyncSession, user: User, group_id: int, day: date | None, todo_ids: list[int]
) -> Declaration:
    await group_svc.require_member(db, user, group_id)

    today = service_today()
    target = day or today
    if target < today:
        raise AppError("DECLARATION_CLOSED", "지난 날짜는 선언할 수 없어요.")
    if target > today:
        raise FieldValidationError({"date": "오늘 할 일만 선언할 수 있어요."})

    unique_ids = list(dict.fromkeys(todo_ids))
    if not unique_ids:
        raise AppError("DECLARATION_EMPTY")
    if len(unique_ids) > MAX_ITEMS:
        raise FieldValidationError({"todo_ids": f"한 번에 {MAX_ITEMS}개까지 선언할 수 있어요."})

    existing = await db.scalar(
        select(Declaration.declaration_id).where(
            Declaration.group_id == group_id,
            Declaration.user_id == user.user_id,
            Declaration.date == target,
        )
    )
    if existing is not None:
        raise AppError("DECLARATION_ALREADY_EXISTS")

    rows = list(
        await db.scalars(
            select(Todo).where(
                Todo.todo_id.in_(unique_ids),
                Todo.user_id == user.user_id,
                Todo.deleted_at.is_(None),
            )
        )
    )
    found = {t.todo_id: t for t in rows}
    for index, todo_id in enumerate(unique_ids):
        todo = found.get(todo_id)
        if todo is None:
            raise FieldValidationError({f"todo_ids[{index}]": "할 일을 찾을 수 없어요."})
        if todo.date != target:
            raise FieldValidationError(
                {f"todo_ids[{index}]": "그날 할 일만 선언할 수 있어요."}
            )

    declaration = Declaration(
        group_id=group_id,
        user_id=user.user_id,
        date=target,
        locked_at=now_utc(),  # 만드는 순간 잠긴다
    )
    db.add(declaration)
    await db.flush()

    for todo_id in unique_ids:
        todo = found[todo_id]
        db.add(
            DeclarationItem(
                declaration_id=declaration.declaration_id,
                todo_id=todo.todo_id,
                title_snapshot=todo.title,
            )
        )
        # 이 시각 이후 제목·날짜·삭제가 잠긴다. 완료 체크는 그대로 된다 (04-backend §5.3).
        todo.declared_at = declaration.locked_at

    await db.flush()
    await db.refresh(declaration)
    return declaration


# ---- 개인 화면용 요약 -----------------------------------------------------------------


async def day_summary(db: AsyncSession, user: User, day: date) -> dict[str, Any] | None:
    """`GET /todos` 의 `declaration` 필드. 그날 내가 어느 그룹에 뭘 선언했는지.

    명세가 이 필드의 모양을 정해 두지 않아(§5 응답 예시에 없음) 최소한으로 채운다.
    여러 그룹에 선언할 수 있으므로 그룹 목록을 담는다. 선언이 없으면 null.
    """
    rows = list(
        await db.scalars(
            select(Declaration)
            .where(Declaration.user_id == user.user_id, Declaration.date == day)
            .order_by(Declaration.declaration_id)
        )
    )
    if not rows:
        return None

    todos, _ = await items_context(db, rows)
    entries = []
    for declaration in rows:
        statuses = [
            item_status(item, todos.get(item.todo_id) if item.todo_id else None, declaration.date)
            for item in declaration.items
        ]
        entries.append(
            {
                "declaration_id": declaration.declaration_id,
                "group_id": declaration.group_id,
                "total": len(statuses),
                "done": sum(1 for s in statuses if s == "done"),
            }
        )
    return {
        "date": day.isoformat(),
        "declared_count": sum(e["total"] for e in entries),
        "groups": entries,
    }
