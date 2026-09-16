"""댓글·리액션 (API 명세 §6.4, ERD §3.14~§3.15).

같은 그룹 구성원만 읽고 쓸 수 있다. 권한 확인은 `feed.get_visible_proof()` 하나로 모은다.

리액션은 사용자당 1개다. `PUT` 이 곧 UPSERT 이고, 다른 종류를 다시 누르면 교체된다.
UNIQUE(post_id, user_id) 가 이 규칙을 DB 에서 보장한다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, Forbidden
from app.core.response import clamp_limit, decode_cursor, encode_cursor
from app.core.time import now_utc, to_kst_iso
from app.db.models.proof import REACTION_TYPES, Comment, Reaction
from app.db.models.user import User
from app.services import feed as feed_svc


def comment_to_dict(comment: Comment, author: User | None) -> dict[str, Any]:
    return {
        "comment_id": comment.comment_id,
        "post_id": comment.post_id,
        "user": {
            "user_id": author.user_id if author else comment.user_id,
            "nickname": author.nickname if author else "",
            "profile_image_url": author.profile_image_url if author else None,
        },
        "content": comment.content,
        "created_at": to_kst_iso(comment.created_at),
    }


# ---- 댓글 -------------------------------------------------------------------------


async def add_comment(
    db: AsyncSession, user: User, post_id: int, content: str
) -> dict[str, Any]:
    await feed_svc.get_visible_proof(db, user, post_id)
    comment = Comment(post_id=post_id, user_id=user.user_id, content=content)
    db.add(comment)
    await db.flush()
    await db.refresh(comment)
    return comment_to_dict(comment, user)


async def list_comments(
    db: AsyncSession, user: User, post_id: int, cursor: str | None, limit: int | None
) -> tuple[list[dict[str, Any]], str | None]:
    """오래된 순. 댓글은 대화라서 위에서 아래로 읽힌다."""
    await feed_svc.get_visible_proof(db, user, post_id)
    size = clamp_limit(limit)

    stmt = select(Comment).where(Comment.post_id == post_id, Comment.deleted_at.is_(None))
    decoded = decode_cursor(cursor)
    if decoded and isinstance(decoded.get("id"), int):
        stmt = stmt.where(Comment.comment_id > decoded["id"])

    rows = list(await db.scalars(stmt.order_by(Comment.comment_id).limit(size + 1)))
    next_cursor = None
    if len(rows) > size:
        rows = rows[:size]
        next_cursor = encode_cursor({"id": rows[-1].comment_id})

    authors = {
        u.user_id: u
        for u in await db.scalars(select(User).where(User.user_id.in_({c.user_id for c in rows})))
    } if rows else {}
    return [comment_to_dict(c, authors.get(c.user_id)) for c in rows], next_cursor


async def delete_comment(db: AsyncSession, user: User, comment_id: int) -> None:
    comment = await db.scalar(
        select(Comment).where(Comment.comment_id == comment_id, Comment.deleted_at.is_(None))
    )
    if comment is None:
        raise AppError("COMMENT_NOT_FOUND")
    if comment.user_id != user.user_id:
        raise Forbidden()  # 작성자 본인만 (§6.4)
    comment.deleted_at = now_utc()
    await db.flush()


# ---- 리액션 -------------------------------------------------------------------------


async def _tally(db: AsyncSession, post_id: int, viewer_id: int) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(Reaction.type, func.count(Reaction.reaction_id))
            .where(Reaction.post_id == post_id)
            .group_by(Reaction.type)
        )
    ).all()
    mine = await db.scalar(
        select(Reaction.type).where(Reaction.post_id == post_id, Reaction.user_id == viewer_id)
    )
    return {
        "post_id": post_id,
        "reactions": {kind: int(n) for kind, n in rows},
        "my_reaction": mine,
    }


async def set_reaction(
    db: AsyncSession, user: User, post_id: int, kind: str
) -> dict[str, Any]:
    if kind not in REACTION_TYPES:
        raise AppError("VALIDATION_ERROR", details={"type": "선택할 수 없는 값이에요."})
    await feed_svc.get_visible_proof(db, user, post_id)

    existing = await db.scalar(
        select(Reaction).where(Reaction.post_id == post_id, Reaction.user_id == user.user_id)
    )
    if existing is None:
        db.add(Reaction(post_id=post_id, user_id=user.user_id, type=kind))
    else:
        existing.type = kind  # 재호출은 교체다. 누적이 아니다.
    await db.flush()
    return await _tally(db, post_id, user.user_id)


async def clear_reaction(db: AsyncSession, user: User, post_id: int) -> None:
    await feed_svc.get_visible_proof(db, user, post_id)
    existing = await db.scalar(
        select(Reaction).where(Reaction.post_id == post_id, Reaction.user_id == user.user_id)
    )
    if existing is not None:  # 없어도 204. 취소는 멱등이다.
        await db.delete(existing)
        await db.flush()
