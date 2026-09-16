"""/todos/* (API 명세 §5). 로직은 services/todos.py."""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.core.deps import CurrentUser, DbSession
from app.core.response import no_content, ok
from app.schemas.todo import (
    TodoBulkRequest,
    TodoCompleteRequest,
    TodoCreateRequest,
    TodoPostponeRequest,
    TodoUpdateRequest,
)
from app.services import todos as svc

router = APIRouter(prefix="/todos", tags=["todos"])


@router.get("", summary="하루 목록 + summary")
async def list_day(
    user: CurrentUser,
    db: DbSession,
    # 쿼리 이름은 명세대로 `date`. 파라미터 이름을 date 로 두면 타입 이름과 충돌한다.
    day: Annotated[date_type | None, Query(alias="date")] = None,
) -> dict[str, Any]:
    return ok(await svc.list_day(db, user, day))


@router.get("/week", summary="7일 달성률 스트립")
async def week(
    user: CurrentUser, db: DbSession, start_date: date_type | None = None
) -> dict[str, Any]:
    return ok(await svc.week_strip(db, user, start_date))


@router.post("", status_code=201, summary="단건 생성")
async def create(user: CurrentUser, db: DbSession, body: TodoCreateRequest) -> dict[str, Any]:
    return ok(await svc.serialize(db, await svc.create_todo(db, user, body)))


@router.post("/bulk", status_code=201, summary="AI 미리보기 확정 저장 (전체 성공 또는 전체 롤백)")
async def create_bulk(user: CurrentUser, db: DbSession, body: TodoBulkRequest) -> dict[str, Any]:
    todos = await svc.create_bulk(db, user, body)
    return ok(
        {
            "items": await svc.serialize_many(db, todos),
            "created_count": len(todos),
        }
    )


@router.get("/{todo_id}", summary="상세")
async def get_one(user: CurrentUser, db: DbSession, todo_id: int) -> dict[str, Any]:
    return ok(await svc.serialize(db, await svc.get_owned_todo(db, user, todo_id)))


@router.patch("/{todo_id}", summary="부분 수정")
async def update(
    user: CurrentUser, db: DbSession, todo_id: int, body: TodoUpdateRequest
) -> dict[str, Any]:
    return ok(await svc.serialize(db, await svc.update_todo(db, user, todo_id, body)))


@router.delete("/{todo_id}", status_code=204, summary="soft delete")
async def delete(user: CurrentUser, db: DbSession, todo_id: int) -> Response:
    await svc.delete_todo(db, user, todo_id)
    return no_content()


@router.post("/{todo_id}/complete", summary="완료")
async def complete(
    user: CurrentUser,
    db: DbSession,
    todo_id: int,
    body: TodoCompleteRequest | None = None,
) -> dict[str, Any]:
    return ok(await svc.complete_todo(db, user, todo_id, body or TodoCompleteRequest()))


@router.post("/{todo_id}/uncomplete", status_code=204, summary="완료 취소")
async def uncomplete(user: CurrentUser, db: DbSession, todo_id: int) -> Response:
    await svc.uncomplete_todo(db, user, todo_id)
    return no_content()


@router.post("/{todo_id}/postpone", summary="미루기")
async def postpone(
    user: CurrentUser,
    db: DbSession,
    todo_id: int,
    body: TodoPostponeRequest | None = None,
) -> dict[str, Any]:
    return ok(
        await svc.serialize(
            db, await svc.postpone_todo(db, user, todo_id, body or TodoPostponeRequest())
        )
    )
