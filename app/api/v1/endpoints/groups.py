"""/groups/* (API 명세 §6.1~§6.3, §6.5).

그룹·선언·인증·피드·랭킹이 모두 그룹 하위 경로라 한 파일에 둔다.
접근 제어는 `groups.require_member()` 한 줄로 통일한다 — 구성원이 아니면 403,
그룹이 없으면 404.
"""

from __future__ import annotations

from datetime import date as date_type
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from app.core.deps import CurrentUser, DbSession
from app.core.response import no_content, ok, paged
from app.schemas.social import (
    DeclarationCreateRequest,
    GroupCreateRequest,
    GroupJoinRequest,
    OwnershipTransferRequest,
    ProofCreateRequest,
)
from app.services import declarations as decl_svc
from app.services import feed as feed_svc
from app.services import groups as svc
from app.services import ranking as ranking_svc

router = APIRouter(prefix="/groups", tags=["groups"])

RankingPeriod = Literal["week", "month", "all"]


# ---- 그룹 -------------------------------------------------------------------------


@router.get("", summary="내가 속한 그룹 목록 (미확인 수 포함)")
async def list_my_groups(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return ok(await svc.list_my_groups(db, user))


@router.post("", status_code=201, summary="그룹 만들기 (3~6명)")
async def create(user: CurrentUser, db: DbSession, body: GroupCreateRequest) -> dict[str, Any]:
    group = await svc.create_group(db, user, body.name, body.max_members)
    _, member = await svc.require_member(db, user, group.group_id)
    return ok(await svc.group_to_dict(db, group, member))


@router.post("/join", summary="초대 코드로 참여")
async def join(user: CurrentUser, db: DbSession, body: GroupJoinRequest) -> dict[str, Any]:
    group = await svc.join_group(db, user, body.invite_code)
    _, member = await svc.require_member(db, user, group.group_id)
    return ok(await svc.group_to_dict(db, group, member, with_activity=True))


@router.get("/{group_id}", summary="그룹 상세")
async def get_one(user: CurrentUser, db: DbSession, group_id: int) -> dict[str, Any]:
    group, member = await svc.require_member(db, user, group_id)
    return ok(await svc.group_to_dict(db, group, member, with_activity=True))


@router.post("/{group_id}/invite", summary="새 초대 코드 발급 (7일)")
async def invite(user: CurrentUser, db: DbSession, group_id: int) -> dict[str, Any]:
    await svc.require_member(db, user, group_id)
    return ok(svc.invite_to_dict(await svc.issue_invite(db, group_id, user.user_id)))


@router.get("/{group_id}/members", summary="구성원과 오늘 현황")
async def members(
    user: CurrentUser, db: DbSession, group_id: int, date: date_type | None = None
) -> dict[str, Any]:
    await svc.require_member(db, user, group_id)
    return ok(await svc.list_members(db, group_id, date))


@router.post("/{group_id}/owner", summary="방장 넘기기")
async def transfer(
    user: CurrentUser, db: DbSession, group_id: int, body: OwnershipTransferRequest
) -> dict[str, Any]:
    group = await svc.transfer_ownership(db, user, group_id, body.user_id)
    _, member = await svc.require_member(db, user, group_id)
    return ok(await svc.group_to_dict(db, group, member))


@router.delete("/{group_id}/members/me", status_code=204, summary="그룹 나가기")
async def leave(user: CurrentUser, db: DbSession, group_id: int) -> Response:
    await svc.leave_group(db, user, group_id)
    return no_content()


# ---- 선언 -------------------------------------------------------------------------


@router.post("/{group_id}/declarations", status_code=201, summary="아침 선언 (즉시 잠김)")
async def declare(
    user: CurrentUser, db: DbSession, group_id: int, body: DeclarationCreateRequest
) -> dict[str, Any]:
    declaration = await decl_svc.create(db, user, group_id, body.date, body.todo_ids)
    return ok(await decl_svc.to_dict(db, declaration))


@router.get("/{group_id}/declarations", summary="그날 선언 현황 (미선언자 포함)")
async def declarations(
    user: CurrentUser, db: DbSession, group_id: int, date: date_type | None = None
) -> dict[str, Any]:
    await svc.require_member(db, user, group_id)
    return ok(await decl_svc.list_for_day(db, group_id, date))


# ---- 인증·피드 ---------------------------------------------------------------------


@router.post("/{group_id}/proofs", status_code=201, summary="인증샷 등록")
async def create_proof(
    user: CurrentUser, db: DbSession, group_id: int, body: ProofCreateRequest
) -> dict[str, Any]:
    await svc.require_member(db, user, group_id)
    proof = await feed_svc.create_proof(
        db, user, group_id, body.declaration_item_id, body.file_key, body.caption
    )
    return ok(feed_svc.proof_to_dict(proof, author=user))


@router.get("/{group_id}/feed", summary="사용자별로 묶인 피드 (미달성 항목 포함)")
async def feed(
    user: CurrentUser,
    db: DbSession,
    group_id: int,
    date: date_type | None = None,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> dict[str, Any]:
    _, member = await svc.require_member(db, user, group_id)
    items, next_cursor = await feed_svc.list_feed(db, user, group_id, date, cursor, limit)
    # 첫 장을 읽으면 미확인 배지를 내린다. 별도 read 엔드포인트를 두지 않는다.
    if cursor is None:
        await svc.touch_read(db, member)
    return paged(items, next_cursor)


# ---- 랭킹·스트릭 --------------------------------------------------------------------


@router.get("/{group_id}/ranking", summary="기간별 순위 (달력 주/월 기준)")
async def ranking(
    user: CurrentUser, db: DbSession, group_id: int, period: RankingPeriod = "week"
) -> dict[str, Any]:
    group, _ = await svc.require_member(db, user, group_id)
    return ok(await ranking_svc.group_ranking(db, user, group, period))


@router.get("/{group_id}/streak", summary="그룹 연속 달성 현황")
async def streak(user: CurrentUser, db: DbSession, group_id: int) -> dict[str, Any]:
    group, _ = await svc.require_member(db, user, group_id)
    return ok(await ranking_svc.group_streak(db, group))
