"""/posts/* · /comments/* (API 명세 §6.4).

게시물(인증샷)에 달리는 댓글과 리액션. 같은 그룹 구성원만 읽고 쓸 수 있다.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from app.core.deps import CurrentUser, DbSession
from app.core.response import no_content, ok, paged
from app.schemas.social import CommentCreateRequest, ReactionPutRequest
from app.services import feed as feed_svc
from app.services import posts as svc

router = APIRouter(tags=["social"])


# ---- 게시물 -----------------------------------------------------------------------


@router.get("/posts/{post_id}", summary="인증샷 단건")
async def get_post(user: CurrentUser, db: DbSession, post_id: int) -> dict[str, Any]:
    proof = await feed_svc.get_visible_proof(db, user, post_id)
    extras = await feed_svc.decorate(db, [proof], user.user_id)
    return ok(feed_svc.proof_to_dict(proof, **extras.get(post_id, {})))


@router.delete("/posts/{post_id}", status_code=204, summary="인증샷 삭제 (작성자 본인만)")
async def delete_post(user: CurrentUser, db: DbSession, post_id: int) -> Response:
    await feed_svc.delete_proof(db, user, post_id)
    return no_content()


# ---- 댓글 -------------------------------------------------------------------------


@router.get("/posts/{post_id}/comments", summary="댓글 목록 (오래된 순, 커서)")
async def list_comments(
    user: CurrentUser,
    db: DbSession,
    post_id: int,
    cursor: str | None = None,
    limit: Annotated[int | None, Query(ge=1, le=50)] = None,
) -> dict[str, Any]:
    items, next_cursor = await svc.list_comments(db, user, post_id, cursor, limit)
    return paged(items, next_cursor)


@router.post("/posts/{post_id}/comments", status_code=201, summary="댓글 달기")
async def add_comment(
    user: CurrentUser, db: DbSession, post_id: int, body: CommentCreateRequest
) -> dict[str, Any]:
    return ok(await svc.add_comment(db, user, post_id, body.content))


@router.delete("/comments/{comment_id}", status_code=204, summary="댓글 삭제 (작성자 본인만)")
async def delete_comment(user: CurrentUser, db: DbSession, comment_id: int) -> Response:
    await svc.delete_comment(db, user, comment_id)
    return no_content()


# ---- 리액션 -------------------------------------------------------------------------


@router.put("/posts/{post_id}/reactions", summary="리액션 (사용자당 1개, 재호출 시 교체)")
async def set_reaction(
    user: CurrentUser, db: DbSession, post_id: int, body: ReactionPutRequest
) -> dict[str, Any]:
    return ok(await svc.set_reaction(db, user, post_id, body.type))


@router.delete("/posts/{post_id}/reactions", status_code=204, summary="리액션 취소")
async def clear_reaction(user: CurrentUser, db: DbSession, post_id: int) -> Response:
    await svc.clear_reaction(db, user, post_id)
    return no_content()
